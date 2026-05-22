# -*- coding: utf-8 -*-

import binascii
from copy import deepcopy
import json
import logging
import re
import shutil
import subprocess
from html import unescape as html_unescape
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit, urlparse
import requests
import yaml

from ..utils import b64plus
from ..utils.dns_resolve import subscription_resolve_entry
from ..types.nodes import NodeShadowsocks, NodeShadowsocksR, NodeV2Ray,NodeTrojan, NodeMihomo
from .base_configs import shadowsocks_get_config, V2RayBaseConfigs
from .shadowsocks_parsers import ParserShadowsocksBasic, ParserShadowsocksSIP002, ParserShadowsocksD
from .shadowsocksr_parsers import ParserShadowsocksR
from .v2ray_parsers import ParserV2RayN, ParserV2RayQuantumult
from .clash_parser import ParserClash
from .node_filters import NodeFilter
from .trojan_parser import TrojanParser

from config import config
PROXY_SETTINGS = config["proxy"]
LOCAL_ADDRESS = config["localAddress"]
LOCAL_PORT = config["localPort"]
TIMEOUT = 10

logger = logging.getLogger("Sub")

class UniversalParser:
	def __init__(self):
		self.__nodes = []
		self.__ss_base_cfg = shadowsocks_get_config(LOCAL_ADDRESS, LOCAL_PORT, TIMEOUT)

	@property
	def nodes(self):
		return deepcopy(self.__nodes)

	def __get_ss_base_config(self):
		return deepcopy(self.__ss_base_cfg)

	def __clean_nodes(self):
		self.__nodes.clear()

	def set_nodes(self, nodes: list):
		self.__clean_nodes()
		self.__nodes = nodes
	
	def set_group(self, group: str):
		tmp_nodes = deepcopy(self.__nodes)
		self.__clean_nodes()
		for node in tmp_nodes:
			if group:
				node.update_config({"group": group})
			self.__nodes.append(node)

	def __parse_bool(self, value, default=False):
		if value is None:
			return default
		return str(value).lower() in ("1", "true", "yes", "on")

	def __parse_mihomo_link(self, link: str):
		parsed = urlsplit(link)
		scheme = parsed.scheme.lower()
		if scheme not in ("vless", "reality", "hy", "hy2", "hysteria", "hysteria2", "anytls", "tuic"):
			return None

		name = unquote(parsed.fragment) if parsed.fragment else parsed.hostname or "N/A"
		group = "N/A"
		query = parse_qs(parsed.query)
		server = parsed.hostname
		port = parsed.port
		if not server or not port:
			logger.error("Invalid link (missing host or port): %s", link)
			return None

		cfg_type = {
			"reality": "vless",
			"hy": "hysteria",
			"hysteria": "hysteria",
			"hy2": "hysteria2",
			"hysteria2": "hysteria2",
		}.get(scheme, scheme)
		cfg = {
			"type": cfg_type,
			"name": name,
			"remarks": name,
			"group": group,
			"server": server,
			"server_port": int(port),
			"port": int(port),
		}

		if scheme in ("vless", "reality"):
			cfg["uuid"] = parsed.username or ""
			cfg["network"] = query.get("type", ["tcp"])[0]
			security = "reality" if scheme == "reality" else query.get("security", ["none"])[0]
			cfg["tls"] = security in ("tls", "reality")
			cfg["servername"] = query.get("sni", query.get("host", [""]))[0]
			cfg["skip-cert-verify"] = self.__parse_bool(query.get("allowInsecure", ["0"])[0])
			flow = query.get("flow", [""])[0]
			if flow:
				cfg["flow"] = flow
			if cfg["network"] == "ws":
				cfg["ws-opts"] = {
					"path": query.get("path", ["/"])[0],
					"headers": {"Host": query.get("host", [""])[0]},
				}
			if security == "reality":
				cfg["reality-opts"] = {
					"public-key": query.get("pbk", [""])[0],
					"short-id": query.get("sid", [""])[0],
				}
				fingerprint = query.get("fp", [""])[0]
				if fingerprint:
					cfg["client-fingerprint"] = fingerprint
				spider_x = query.get("spx", [""])[0]
				if spider_x:
					cfg["reality-opts"]["spider-x"] = spider_x
		elif cfg_type == "hysteria":
			cfg["auth-str"] = parsed.username or parsed.password or ""
			cfg["sni"] = query.get("sni", query.get("peer", [""]))[0]
			cfg["skip-cert-verify"] = self.__parse_bool(query.get("insecure", ["0"])[0])
			cfg["up"] = query.get("upmbps", query.get("up", [""]))[0]
			cfg["down"] = query.get("downmbps", query.get("down", [""]))[0]
			cfg["obfs"] = query.get("obfs", [""])[0]
			if cfg["obfs"]:
				cfg["obfs-param"] = query.get("obfs-password", [""])[0]
		elif cfg_type == "hysteria2":
			cfg["password"] = parsed.username or parsed.password or ""
			cfg["sni"] = query.get("sni", [""])[0]
			cfg["skip-cert-verify"] = self.__parse_bool(query.get("insecure", ["0"])[0])
		elif cfg_type == "anytls":
			cfg["password"] = parsed.username or parsed.password or ""
			cfg["sni"] = query.get("sni", [""])[0]
			cfg["skip-cert-verify"] = self.__parse_bool(query.get("insecure", ["0"])[0])
		elif cfg_type == "tuic":
			cfg["uuid"] = parsed.username or ""
			cfg["password"] = parsed.password or query.get("password", [""])[0]
			cfg["token"] = query.get("token", [""])[0]
			cfg["sni"] = query.get("sni", [""])[0]
			cfg["alpn"] = query.get("alpn", [])
			cfg["udp-relay-mode"] = query.get("udp_relay_mode", ["native"])[0]
			cfg["congestion-controller"] = query.get("congestion_control", ["bbr"])[0]
			cfg["skip-cert-verify"] = self.__parse_bool(query.get("allow_insecure", ["0"])[0])

		return cfg

	def parse_links(self, links: list):
		#Single link parse
		result = []
		for link in links:
			link = link.replace("\r", "").strip()
			if not link:
				continue
			node = None
			if link[:5] == "ss://":
				#Shadowsocks
				cfg = None
				try:
					pssb = ParserShadowsocksBasic(self.__get_ss_base_config())
					cfg = pssb.parse_single_link(link)
				except ValueError:
					pssip002 = ParserShadowsocksSIP002(self.__get_ss_base_config())
					cfg = pssip002.parse_single_link(link)
				if cfg:
					node = NodeShadowsocks(cfg)
				else:
					logger.warning(f"Invalid shadowsocks link {link}")

			elif link[:6] == "ssr://":
				#ShadowsocksR
				pssr = ParserShadowsocksR(self.__get_ss_base_config())
				cfg = pssr.parse_single_link(link)
				if cfg:
					node = NodeShadowsocksR(cfg)
				else:
					logger.warning(f"Invalid shadowsocksR link {link}")

			elif link[:8] == "vmess://":
				#Vmess link (V2RayN and Quan)
				#V2RayN Parser
				cfg = None
				logger.info("Try V2RayN Parser.")
				pv2rn = ParserV2RayN()
				try:
					cfg = pv2rn.parseSubsConfig(link)
				except ValueError:
					pass
				if not cfg:
					#Quantumult Parser
					logger.info("Try Quantumult Parser.")
					pq = ParserV2RayQuantumult()
					try:
						cfg = pq.parseSubsConfig(link)
					except ValueError:
						pass
				if not cfg:
					logger.error(f"Invalid vmess link: {link}")
				else:
					gen_cfg = V2RayBaseConfigs.generate_config(cfg, LOCAL_ADDRESS, LOCAL_PORT)
					node = NodeV2Ray(gen_cfg)
			elif link[:9] == "trojan://":
				cfg = None
				logger.info("Try Trojan Parser.")
				pvTrojan = TrojanParser()
				try:
					cfg = pvTrojan._parseLink(link)
					# logger.info(cfg)
				except ValueError:
					pass
				if cfg:
					node = NodeTrojan(cfg)
			elif (
				link.startswith("vless://")
				or link.startswith("hy://")
				or link.startswith("hy2://")
				or link.startswith("hysteria://")
				or link.startswith("hysteria2://")
				or link.startswith("anytls://")
				or link.startswith("tuic://")
				or link.startswith("reality://")
			):
				cfg = self.__parse_mihomo_link(link)
				if cfg:
					node = NodeMihomo(cfg)
			else:
				logger.warn(f"Unsupport link: {link}")

			if node:
				result.append(node)

		return result

	def __parse_clash(self, clash_cfg: str) -> list:
		result = []
		pc = ParserClash(shadowsocks_get_config(LOCAL_ADDRESS, LOCAL_PORT, TIMEOUT))
		pc.parse_config(clash_cfg)
		cfgs = pc.config_list
		for cfg in cfgs:
			if cfg["type"] == "ss":
				result.append(NodeShadowsocks(cfg["config"]))
			elif cfg["type"] == "vmess":
				result.append(
					NodeV2Ray(
						V2RayBaseConfigs.generate_config(cfg["config"], LOCAL_ADDRESS, LOCAL_PORT)
					)
				)
			elif cfg["type"]=="trojan":
				result.append(NodeTrojan(cfg["config"]))
			elif cfg["type"] in ("vless", "hysteria", "hysteria2", "anytls", "tuic"):
				if "remarks" not in cfg["config"]:
					cfg["config"]["remarks"] = cfg["config"].get("name", cfg["config"].get("server", "N/A"))
				if "group" not in cfg["config"]:
					cfg["config"]["group"] = "N/A"
				if "server_port" not in cfg["config"]:
					cfg["config"]["server_port"] = cfg["config"].get("port", 0)
				result.append(NodeMihomo(cfg["config"]))

		return result

	@staticmethod
	def __looks_like_html_response(text: str) -> bool:
		"""识别常见 WAF/挑战页。纯 Base64 不含 '<'，避免把整段节点串误判成 HTML。"""
		if not text or not text.strip():
			return False
		s0 = text.lstrip("\ufeff \t\r\n")
		if "<" not in s0[:512]:
			return False
		head = s0[:8192].lower()
		if head.startswith("<!doctype html") or head.startswith("<html"):
			return True
		if head.startswith("<!--") and "<html" in s0[:4096].lower():
			return True
		if "cf-browser-verification" in head:
			return True
		if "attention required" in head and "cloudflare" in head:
			return True
		if "just a moment" in head and "cloudflare" in head:
			return True
		return False

	@staticmethod
	def __extract_share_links_from_html(html_blob: str) -> str:
		"""从整页 HTML 源码中提取 share 链接（部分页面会把节点 URL 写在脚本或属性里）。"""
		if not html_blob or len(html_blob) < 200:
			return ""
		pat = re.compile(
			r"(?:vless|vmess|ss|ssr|trojan|hysteria2|hy2|tuic)://[^\s\"'<>\\)]+",
			re.I,
		)
		links = pat.findall(html_blob)
		if len(links) < 3:
			return ""
		out = []
		seen = set()
		for u in links:
			if u not in seen and len(u) > 12:
				seen.add(u)
				out.append(u)
		if len(out) < 3:
			return ""
		return "\n".join(out)

	@staticmethod
	def __extract_subscription_from_html_blob(html_blob: str) -> str:
		"""部分面板用 HTML 壳包一层，正文在 textarea/pre/code 里。"""
		if not html_blob or len(html_blob) < 80:
			return ""
		for pat in (
			r"<textarea[^>]*>([\s\S]*?)</textarea>",
			r"<pre[^>]*>([\s\S]*?)</pre>",
			r"<code[^>]*>([\s\S]*?)</code>",
		):
			m = re.search(pat, html_blob, re.I)
			if not m:
				continue
			block = html_unescape(m.group(1)).strip()
			if len(block) < 40:
				continue
			if "://" in block:
				return block
			compact = re.sub(r"\s+", "", block[:800])
			if re.match(r"^[A-Za-z0-9+/=_-]+$", compact):
				return block
		try:
			from bs4 import BeautifulSoup

			soup = BeautifulSoup(html_blob, "html.parser")
			for el in soup.find_all(["textarea", "pre", "code"]):
				block = el.get_text(separator="\n", strip=True)
				if len(block) < 40:
					continue
				if "://" in block:
					return block
				compact = re.sub(r"\s+", "", block[:800])
				if re.match(r"^[A-Za-z0-9+/=_-]+$", compact):
					return block
		except Exception:
			pass
		return ""

	def __fetch_subscription_via_session(self, url: str, verify: bool) -> str:
		"""先访问站点根路径再拉订阅，部分 WAF 依赖 Cookie / Referer。"""
		try:
			p = urlparse(url)
			base = "{}://{}".format(p.scheme, p.netloc)
			s = requests.Session()
			proxies = None
			if PROXY_SETTINGS["enabled"]:
				auth = ""
				if PROXY_SETTINGS["username"]:
					auth = "{}:{}@".format(PROXY_SETTINGS["username"], PROXY_SETTINGS["password"])
				px = "socks5://{}{}:{}".format(auth, PROXY_SETTINGS["address"], PROXY_SETTINGS["port"])
				proxies = {"http": px, "https": px}
			browser = {
				"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
				"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
				"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
				"Connection": "keep-alive",
				"Upgrade-Insecure-Requests": "1",
				"Sec-Fetch-Dest": "document",
				"Sec-Fetch-Mode": "navigate",
				"Sec-Fetch-Site": "none",
				"Sec-Fetch-User": "?1",
			}
			root = base.rstrip("/") + "/"
			s.get(root, headers=browser, timeout=12, verify=verify, allow_redirects=True, proxies=proxies)
			h2 = dict(browser)
			h2["Accept"] = "*/*"
			h2["Referer"] = root
			r = s.get(url, headers=h2, timeout=22, verify=verify, allow_redirects=True, proxies=proxies)
			if r.status_code == 200 and (r.text or "").strip():
				r.encoding = r.apparent_encoding or "utf-8"
				return r.text or ""
		except Exception as e:
			logger.warning("Session warm-up subscription fetch failed: %s", e.__class__.__name__)
		return ""

	@staticmethod
	def __subscription_bytes_to_text(raw: bytes) -> str:
		"""curl 原始 stdout 可能是 gzip 或 UTF-16；勿用 subprocess text=True 强转 UTF-8 以免弄坏 Base64。"""
		if not raw:
			return ""
		blob = raw
		if len(blob) >= 2 and blob[0] == 0x1F and blob[1] == 0x8B:
			try:
				import gzip

				blob = gzip.decompress(blob)
			except Exception:
				pass
		if blob.startswith(b"\xef\xbb\xbf"):
			blob = blob[3:]
		if blob.startswith(b"\xff\xfe"):
			return blob[2:].decode("utf-16-le", errors="replace")
		if blob.startswith(b"\xfe\xff"):
			return blob[2:].decode("utf-16-be", errors="replace")
		return blob.decode("utf-8", errors="replace")

	@staticmethod
	def __unwrap_json_subscription_text(text: str) -> str:
		"""部分 API 返回 JSON，真实订阅在 data/content 等字段里。"""
		s = text.strip()
		if len(s) < 40 or s[0] not in "{[":
			return ""
		try:
			obj = json.loads(s)
		except (ValueError, json.JSONDecodeError):
			return ""

		def _from_dict(d):
			if not isinstance(d, dict):
				return ""
			for key in (
				"data",
				"content",
				"body",
				"result",
				"subscription",
				"config",
				"sub",
				"raw",
				"nodes",
				"list",
			):
				v = d.get(key)
				if isinstance(v, str) and len(v) > 60:
					compact = re.sub(r"\s+", "", v[:400])
					if "://" in v or re.match(r"^[A-Za-z0-9+/=_-]+$", compact):
						return v.strip()
				if isinstance(v, list) and v:
					parts = [x for x in v if isinstance(x, str) and x.strip()]
					if parts:
						joined = "\n".join(parts)
						if len(joined) > 60 and "://" in joined:
							return joined
			return ""

		if isinstance(obj, dict):
			got = _from_dict(obj)
			if got:
				return got
		if isinstance(obj, list):
			for item in obj:
				if isinstance(item, str) and len(item) > 60:
					if "://" in item:
						return item.strip()
					compact = re.sub(r"\s+", "", item[:400])
					if re.match(r"^[A-Za-z0-9+/=_-]+$", compact):
						return item.strip()
				if isinstance(item, dict):
					got = _from_dict(item)
					if got:
						return got
		return ""

	def __fetch_subscription_via_system_curl(self, url: str, verify: bool, resolve_entry: Optional[str] = None) -> str:
		"""系统自带 curl（Windows 常为 Schannel）与 Python OpenSSL 的 TLS 指纹不同，部分 CDN 只拦后者。"""
		curl_exe = shutil.which("curl")
		if not curl_exe:
			return ""
		uas = (
			"Mihomo/1.18.0",
			"ClashMetaForAndroid/2.10.1.Meta",
			"Stash/2.6.0",
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
		)
		for ua in uas:
			try:
				cmd = [curl_exe, "-sS", "-L", "--max-time", "28", "--compressed"]
				if not verify:
					cmd.append("-k")
				if PROXY_SETTINGS["enabled"]:
					auth = ""
					if PROXY_SETTINGS["username"]:
						auth = "{}:{}@".format(
							PROXY_SETTINGS["username"],
							PROXY_SETTINGS["password"],
						)
					px = "socks5h://{}{}:{}".format(
						auth,
						PROXY_SETTINGS["address"],
						PROXY_SETTINGS["port"],
					)
					cmd.extend(["--proxy", px])
				if resolve_entry:
					cmd.extend(["--resolve", resolve_entry])
				cmd.extend(["-H", "Accept: */*", "-A", ua, url])
				cp = subprocess.run(cmd, capture_output=True, timeout=35)
				if cp.returncode != 0:
					logger.warning("curl exited %s for UA [%s]", cp.returncode, ua)
					continue
				raw = cp.stdout or b""
				if len(raw) < 80:
					continue
				out = self.__subscription_bytes_to_text(raw).strip()
				if len(out) < 80:
					continue
				if self.__looks_like_html_response(out):
					logger.warning("curl returned HTML-like body (len=%d) for UA [%s].", len(out), ua)
					continue
				logger.info("Subscription fetched via system curl (UA=%s, raw=%d text=%d chars).", ua, len(raw), len(out))
				return out
			except Exception as e:
				logger.warning("curl subscription fetch failed (%s): %s", ua, e.__class__.__name__)
		return ""

	def __looks_like_ssl_failure(self, exc: BaseException) -> bool:
		cur: Optional[BaseException] = exc
		seen = set()
		while cur is not None and id(cur) not in seen:
			seen.add(id(cur))
			name = type(cur).__name__
			if "SSL" in name or name == "SSLError":
				return True
			cur = cur.__cause__
		msg = str(exc).lower()
		return "ssl" in msg and ("error" in msg or "eof" in msg or "handshake" in msg)

	def __fetch_subscription_via_browser_tls(self, url: str, verify: bool, resolve_entry: Optional[str] = None) -> str:
		"""使用 curl_cffi 模拟真实浏览器 TLS/JA3 指纹（比仅改 User-Agent 更接近浏览器）。"""
		try:
			from curl_cffi import requests as brq
		except ImportError:
			return ""
		if not url.lower().startswith("http"):
			return ""
		impersonates = (
			"chrome131_android",
			"chrome131",
			"chrome124",
			"chrome120",
			"chrome110",
			"edge101",
			"safari17_0",
			"chrome",
		)
		proxies = None
		if PROXY_SETTINGS["enabled"]:
			auth = ""
			if PROXY_SETTINGS["username"]:
				auth = "{}:{}@".format(
					PROXY_SETTINGS["username"],
					PROXY_SETTINGS["password"],
				)
			px = "socks5://{}{}:{}".format(
				auth,
				PROXY_SETTINGS["address"],
				PROXY_SETTINGS["port"],
			)
			proxies = {"http": px, "https": px}
		headers = {
			"Accept": "*/*",
			"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
			"Cache-Control": "no-cache",
			"Pragma": "no-cache",
		}
		for imp in impersonates:
			try:
				br_kw = dict(
					impersonate=imp,
					headers=headers,
					timeout=28,
					verify=verify,
					proxies=proxies,
					allow_redirects=True,
				)
				if resolve_entry:
					try:
						from curl_cffi import CurlOpt

						br_kw["curl_options"] = {CurlOpt.RESOLVE: [resolve_entry]}
					except Exception:
						pass
				r = brq.get(url, **br_kw)
				resp_sc = getattr(r, "status_code", 0)
				text = (r.text or "").strip() if r is not None else ""
				if resp_sc == 200 and text:
					if self.__looks_like_html_response(text):
						logger.warning(
							"Browser-TLS fetch (%s) still looks like HTML/WAF (len=%d); trying next profile.",
							imp,
							len(text),
						)
						continue
					logger.info(
						"Subscription fetched via browser TLS impersonation (%s), %d chars.",
						imp,
						len(text),
					)
					return r.text or ""
				logger.warning(
					"Browser-TLS fetch (%s) not usable: status=%s len=%d",
					imp,
					resp_sc,
					len(text),
				)
			except Exception as e:
				logger.warning("Browser-TLS fetch failed (%s): %s", imp, e.__class__.__name__)
		return ""

	def __fetch_subscription_text(self, url: str) -> str:
		# 部分机场对 python-requests 等 UA 返回 HTML/挑战页但仍为 200；勿在首次 200 就返回。
		# 优先使用 Clash / Mihomo 等客户端 UA。
		header_candidates = [
			{"User-Agent": "Mihomo/1.18.0", "Accept": "*/*"},
			{"User-Agent": "ClashMetaForAndroid/2.10.1.Meta", "Accept": "*/*"},
			{"User-Agent": "clash-verge/v1.7.7", "Accept": "*/*"},
			{"User-Agent": "ClashForWindows/0.20.39", "Accept": "*/*"},
			{"User-Agent": "FlClash/v0.8.0", "Accept": "*/*"},
			{"User-Agent": "Stash/2.6.0", "Accept": "*/*"},
			{"User-Agent": "curl/8.7.1", "Accept": "*/*"},
			{"User-Agent": "python-requests/2.31.0", "Accept": "*/*"},
			{
				"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
				"Accept": "text/plain,application/yaml,text/yaml,application/json,text/html;q=0.8,*/*;q=0.5",
			},
		]
		last_status = 0
		last_err = None
		had_ssl_failure = False
		last_200_text = ""
		resolve_entry = None
		try:
			resolve_entry = subscription_resolve_entry(url)
			if resolve_entry:
				logger.info("Subscription host pinned via DoH (--resolve / CURLOPT_RESOLVE): %s", resolve_entry)
		except Exception:
			logger.debug("Subscription DoH resolve skipped.", exc_info=True)
		perf = config.get("performance") or {}
		sub_http_to = int(perf.get("subscription_http_timeout", 22))

		for verify in (True, False):
			if not verify:
				if not had_ssl_failure:
					break
				logger.warning(
					"Retrying subscription HTTPS with verify=False after SSL handshake/verify errors."
				)
			if resolve_entry:
				curl_first = self.__fetch_subscription_via_system_curl(url, verify, resolve_entry)
				if curl_first.strip():
					last_200_text = curl_first
					if not self.__looks_like_html_response(curl_first):
						logger.info(
							"Subscription fetched early via system curl + DoH resolve (%d chars).",
							len(curl_first),
						)
						return curl_first
			br_text = self.__fetch_subscription_via_browser_tls(url, verify=verify, resolve_entry=resolve_entry)
			if br_text.strip():
				last_200_text = br_text
				if not self.__looks_like_html_response(br_text):
					return br_text
				logger.warning(
					"Browser-TLS body still looks like HTML/WAF (len=%d); trying requests/curl.",
					len(br_text),
				)
			for header in header_candidates:
				try:
					if PROXY_SETTINGS["enabled"]:
						auth = ""
						if PROXY_SETTINGS["username"]:
							auth = "{}:{}@".format(
								PROXY_SETTINGS["username"],
								PROXY_SETTINGS["password"]
							)
						proxy = "socks5://{}{}:{}".format(
							auth,
							PROXY_SETTINGS["address"],
							PROXY_SETTINGS["port"]
						)
						proxies = {
							"http": proxy,
							"https": proxy
						}
						logger.info("Reading subscription via {}".format(proxy))
						rep = requests.get(
							url, headers=header, timeout=sub_http_to, proxies=proxies, verify=verify
						)
					else:
						rep = requests.get(url, headers=header, timeout=sub_http_to, verify=verify)
					last_status = rep.status_code
					rep.encoding = "utf-8"
					text = rep.text or ""
					if rep.status_code == 200 and text.strip():
						last_200_text = text
						if self.__looks_like_html_response(text):
							logger.warning(
								"Subscription returned HTML/challenge with UA [%s] (len=%d); trying next UA.",
								header["User-Agent"],
								len(text),
							)
							continue
						return text
					logger.warning(
						"Subscription request not usable with UA [%s], status=%s, length=%d",
						header["User-Agent"],
						rep.status_code,
						len(text),
					)
				except Exception as e:
					last_err = e
					if verify and self.__looks_like_ssl_failure(e):
						had_ssl_failure = True
					logger.warning(
						"Subscription request failed with UA [%s]: %s",
						header["User-Agent"],
						e.__class__.__name__,
					)
			curl_text = self.__fetch_subscription_via_system_curl(url, verify, resolve_entry)
			if curl_text.strip():
				last_200_text = curl_text
				if not self.__looks_like_html_response(curl_text):
					logger.info("System curl returned usable subscription body (%d bytes).", len(curl_text))
					return curl_text
				logger.warning(
					"System curl body still looks like HTML/WAF (%d bytes).",
					len(curl_text),
				)
			sess_text = self.__fetch_subscription_via_session(url, verify=verify)
			if sess_text.strip():
				last_200_text = sess_text
				if not self.__looks_like_html_response(sess_text):
					logger.info("Session warm-up fetch returned usable subscription body (%d bytes).", len(sess_text))
					return sess_text
				logger.warning(
					"Session warm-up fetch still looks like HTML/WAF (%d bytes); continuing.",
					len(sess_text),
				)
		if last_200_text.strip():
			logger.warning(
				"Using last HTTP 200 body (%d bytes) despite HTML/WAF heuristics; will try parse / HTML unwrap.",
				len(last_200_text),
			)
			return last_200_text
		if last_err:
			logger.error("Subscription request failed after retries: %s", str(last_err))
		else:
			logger.error("Subscription request failed after retries, last status: %s", last_status)
		return ""
	
	def filter_nodes(self, fk=[], fgk=[], frk=[], ek=[], egk=[], erk=[]):
		nf = NodeFilter()
		self.__nodes = nf.filter_node(self.__nodes, fk, fgk, frk, ek, egk, erk)

	def print_nodes(self):
		for item in self.nodes:
			cfg = item.config
			logger.info(
				"{} - {}".format(
					cfg.get("group", "N/A"),
					cfg.get("remarks", cfg.get("name", "N/A"))
				)
			)
		#logger.info(f"{len(self.__nodes)} node(s) in list.")

	def read_subscription(self, urls: list):
		for url in urls:
			if not url:
				continue

			if (
				url.startswith("ss://") or
				url.startswith("ssr://") or
				url.startswith("vmess://") or
				url.startswith("trojan://") or
				url.startswith("vless://") or
				url.startswith("hy://") or
				url.startswith("hy2://") or
				url.startswith("hysteria://") or
				url.startswith("hysteria2://") or
				url.startswith("anytls://") or
				url.startswith("tuic://") or
				url.startswith("reality://")
			):
				self.__nodes.extend(self.parse_links([url]))
				continue

			logger.info("Reading {}".format(url))
			rep = self.__fetch_subscription_text(url)
			if not rep.strip():
				logger.error("Subscription response is empty.")
				continue

			rep = rep.strip()
			if rep.lstrip("\ufeff").startswith("<"):
				extracted = self.__extract_subscription_from_html_blob(rep)
				if extracted:
					logger.info("Extracted subscription payload from HTML wrapper (%d bytes).", len(extracted))
					rep = extracted

			unwrapped = self.__unwrap_json_subscription_text(rep)
			if unwrapped and unwrapped != rep:
				logger.info("Unwrapped subscription from JSON envelope (%d chars).", len(unwrapped))
				rep = unwrapped

			if self.__looks_like_html_response(rep):
				scraped = self.__extract_share_links_from_html(rep)
				if scraped:
					logger.info("Extracted %d share link(s) from HTML source.", scraped.count("\n") + 1)
					rep = scraped

			parsed = False
			#Try ShadowsocksD Parser
			if rep[:6] == "ssd://":
				parsed = True
				logger.info("Try ShadowsocksD Parser.")
				pssd = ParserShadowsocksD(shadowsocks_get_config(LOCAL_ADDRESS, LOCAL_PORT, TIMEOUT))
				cfgs = pssd.parseSubsConfig(b64plus.decode(rep[6:]).decode("utf-8"))
				for cfg in cfgs:
					self.__nodes.append(NodeShadowsocks(cfg))
			if parsed: continue

			#Try base64 decode
			try:
				rep = rep.strip()
				links = (b64plus.decode(rep).decode("utf-8")).split("\n")
				logger.debug("Base64 decode success.")
				self.__nodes.extend(self.parse_links(links))
				parsed = True
			except ValueError:
				logger.info("Base64 decode failed.")
			if parsed: continue

			# 部分订阅为「每行一段 Base64」，整段拼接后不是合法 Base64
			if not parsed:
				line_links = []
				for raw_line in rep.splitlines():
					line = raw_line.strip()
					if not line or line.startswith("#"):
						continue
					if " " in line or "\t" in line:
						continue
					if len(line) < 32:
						continue
					try:
						dec = b64plus.decode(line).decode("utf-8")
						if "://" in dec:
							for piece in dec.splitlines():
								piece = piece.strip()
								if piece:
									line_links.append(piece)
					except ValueError:
						continue
				if line_links:
					logger.info("Per-line Base64 subscription: %d link(s) decoded.", len(line_links))
					self.__nodes.extend(self.parse_links(line_links))
					parsed = True
			if parsed: continue

			# 少数面板把整份 Base64 当作 YAML 无引号标量，yaml.load 得到 str 而非 dict
			if not parsed:
				try:
					yv = yaml.safe_load(rep)
					if isinstance(yv, str) and yv.strip():
						ys = yv.strip()
						if "://" in ys:
							logger.info("YAML scalar subscription: share link text.")
							self.__nodes.extend(self.parse_links([x.strip() for x in ys.splitlines() if x.strip()]))
							parsed = True
						else:
							try:
								dec = b64plus.decode(ys).decode("utf-8")
								if "://" in dec:
									logger.info("YAML scalar subscription: Base64 inside scalar.")
									self.__nodes.extend(self.parse_links([x.strip() for x in dec.splitlines() if x.strip()]))
									parsed = True
							except ValueError:
								pass
				except yaml.YAMLError:
					pass
			if parsed: continue

			#Try Clash Parser
			clash_nodes = self.__parse_clash(rep)
			if clash_nodes:
				self.__nodes.extend(clash_nodes)
				continue
			rep_l = rep.lower()
			if "<html" in rep_l or "<!doctype html" in rep_l or "<head" in rep_l or self.__looks_like_html_response(rep):
				logger.error(
					"Subscription response looks like HTML/WAF page, not node list. "
					"Same URL often works in Clash (different TLS stack) but is blocked for this machine's plain HTTP client."
				)
				logger.error(
					"【说明】该订阅在你当前网络下返回的是网页/风控页，不是节点数据，本程序无法像浏览器一样过盾。"
					"可行做法：在浏览器中打开订阅链接，另存为 .yaml / .txt 后本地测速；或换网络/系统代理后再试。"
				)
				# Fallback: some subconverter URLs embed a real subscription in query param `url=`.
				# If converter/CDN returns HTML challenge, retry with the nested raw URL directly.
				try:
					q = parse_qs(urlsplit(url).query)
					nested_urls = q.get("url", [])
					if nested_urls:
						retry_urls = []
						for nested in nested_urls:
							for maybe_url in str(nested).replace("\n", "|").split("|"):
								maybe_url = maybe_url.strip()
								if maybe_url and maybe_url != url:
									retry_urls.append(maybe_url)
						if retry_urls:
							logger.warning("Retry parsing with nested subscription URL(s) from query param.")
							before = len(self.__nodes)
							self.read_subscription(retry_urls)
							after = len(self.__nodes)
							if after > before:
								logger.info("Nested subscription fallback succeeded: %d node(s) loaded.", after - before)
							else:
								logger.error("Nested subscription fallback failed: still no nodes loaded.")
				except Exception:
					logger.exception("Nested subscription fallback failed.")
			else:
				logger.error("Subscription format not recognized (not base64 links / SSD / Clash YAML).")


	def read_gui_config(self, filename: str):
		raw_data = ""
		with open(filename, "r", encoding="utf-8") as f:
			raw_data = f.read()
		try:
			#Try Load as Json
			data = json.loads(raw_data)
			#Identification of proxy type
			#Shadowsocks(D)
			if (
				"subscriptions" in data
				or
				(
					"subscriptions" not in data
					and "serverSubscribes" not in data
					and "vmess" not in data
				)
			):
				pssb = ParserShadowsocksBasic(self.__get_ss_base_config())
				for cfg in pssb.parse_gui_data(data):
					self.__nodes.append(NodeShadowsocks(cfg))
			#ShadowsocksR
			elif "serverSubscribes" in data:
				pssr = ParserShadowsocksR(self.__get_ss_base_config())
				for cfg in pssr.parse_gui_data(data):
					self.__nodes.append(NodeShadowsocksR(cfg))
			#V2RayN
			elif "vmess" in data:
				pv2n = ParserV2RayN()
				cfgs = pv2n.parse_gui_data(data)
				for cfg in cfgs:
					self.__nodes.append(
						NodeV2Ray(
							V2RayBaseConfigs.generate_config(cfg, LOCAL_ADDRESS, LOCAL_PORT)
						)
					)
		except json.JSONDecodeError:
			#Try Load as Yaml
			self.__nodes = self.__parse_clash(raw_data)

