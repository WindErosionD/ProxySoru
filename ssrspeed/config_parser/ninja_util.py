# -*- coding: utf-8 -*-
"""Ninja 协议辅助：订阅特征识别、pass-info 提取、节点字段规范化。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

# 混淆订阅里常见的 pass-info 注释标记（由 ninja 内核 ProcessNinjaObfuscation 消费）
_PASS_INFO_LINE = re.compile(
	r"^(?:#\s*)?(#!?PASS(?:2|3)?-INFO\b.*)$",
	re.I,
)
_PASS_INFO_ANY = re.compile(r"#!?PASS(?:2|3)?-INFO\b", re.I)


def url_looks_like_ninja(url: str) -> bool:
	u = (url or "").strip().lower()
	if not u:
		return False
	if "/ninja/" in u or u.startswith("ninja://"):
		return True
	return "flag=ninja" in u or "format=ninja" in u


# Clash Verge Ninja 2.5.2 实际订阅 UA（clash-verge.exe 内嵌：clash-ninja/2.5.2 pass 1.0）
# 错误 UA（如 Clash.Verge.Ninja/...）常被源站直接 Access denied。
NINJA_SUB_USER_AGENTS = (
	"clash-ninja/2.5.2 pass 1.0",
	"clash-ninja/2.5.2",
	"Clash.Verge.Ninja/2.5.2",
	"clash-verge-ninja/2.5.2",
)

# 面板常用：裸 IP 源站 + Cloudflare 域名（biteb / as10086）
_NINJA_IP_HOSTS = {"45.137.181.73"}
_NINJA_CANONICAL_HOSTS = ("biteb.ninja", "www.biteb.ninja", "as10086.vip")


def ninja_subscription_url_candidates(url: str) -> list:
	"""为 Ninja 订阅生成候选 URL：保留原链，并把裸 IP 改写到常见面板域名。"""
	u = (url or "").strip()
	if not u:
		return []
	out = [u]
	if not url_looks_like_ninja(u):
		return out
	try:
		parts = urlsplit(u)
	except Exception:
		return out
	host = (parts.hostname or "").strip().lower()
	if host not in _NINJA_IP_HOSTS:
		return out
	# 保留 path/query/fragment，仅替换 host
	for h in _NINJA_CANONICAL_HOSTS:
		netloc = h
		if parts.port and parts.port not in (80, 443):
			netloc = f"{h}:{parts.port}"
		rewritten = parts._replace(netloc=netloc).geturl()
		if rewritten not in out:
			out.append(rewritten)
	return out


def looks_like_access_denied(text: str) -> bool:
	if not text:
		return False
	t = text.strip()
	if len(t) > 500:
		return False
	low = t.lower()
	if '"error"' in low and "access denied" in low:
		return True
	if low in ("access denied", "access denied.", '{"error":"access denied."}'):
		return True
	return False


def looks_like_cf_challenge(text: str) -> bool:
	if not text:
		return False
	low = text[:4000].lower()
	return (
		"just a moment" in low
		or "cf-browser-verification" in low
		or "challenge-platform" in low
		or ("正在进行安全验证" in text[:4000])
		or ("请稍候" in text[:800] and "cloudflare" in low)
	)


def looks_like_cf_520(text: str) -> bool:
	if not text:
		return False
	t = text.strip()
	if t.lower() == "error code: 520":
		return True
	low = t[:4000].lower()
	return "error code: 520" in low or (">520<" in low and "cloudflare" in low)


def diagnose_ninja_subscription_failure(status: int, text: str) -> str:
	"""给人读的失败原因（用于日志）。"""
	body = text or ""
	if looks_like_access_denied(body) or (status == 403 and "access denied" in body.lower()):
		return (
			"源站拒绝（Access denied）：User-Agent 不是官方 clash-ninja/...，"
			"或订阅令牌无效。"
		)
	if status == 520 or looks_like_cf_520(body):
		return (
			"Cloudflare 520：官方 UA 已放行到源站，但源站生成 Ninja 订阅失败。"
			"请在 Clash Verge Ninja 客户端内更新订阅并导出 YAML，或让机场重开 Ninja 订阅。"
		)
	if looks_like_cf_challenge(body) or (status == 403 and looks_like_cf_challenge(body)):
		return (
			"Cloudflare 人机验证拦截：请换可用代理出口，或改用官方客户端拉订阅后导出本地文件。"
		)
	if status and status >= 400:
		return "HTTP %s，响应长度 %d。" % (status, len(body))
	return "响应不可用（长度 %d）。" % len(body)


def text_looks_like_ninja(text: str) -> bool:
	if not text:
		return False
	head = text[:8000]
	if _PASS_INFO_ANY.search(head):
		return True
	if re.search(r"(?im)^\s*type:\s*['\"]?ninja['\"]?\s*$", head):
		return True
	if "type: ninja" in head.lower() or "type: 'ninja'" in head.lower() or 'type: "ninja"' in head.lower():
		return True
	return False


def extract_ninja_pass_info(text: str) -> str:
	"""从订阅正文提取 #!PASS-INFO 等注释行，供写入运行时 YAML。"""
	if not text:
		return ""
	out: List[str] = []
	seen = set()
	for line in text.splitlines():
		m = _PASS_INFO_LINE.match(line.strip())
		if not m:
			# 允许 YAML 注释里夹带 pass-info
			if _PASS_INFO_ANY.search(line):
				s = line.strip()
				if not s.startswith("#"):
					s = "# " + s
				key = s
			else:
				continue
		else:
			s = m.group(1).strip()
			if not s.startswith("#"):
				s = "#" + s
			key = s
		if key in seen:
			continue
		seen.add(key)
		out.append(key)
	return "\n".join(out)


def attach_pass_info(cfg: dict, pass_info: str) -> dict:
	if pass_info:
		cfg["_ninja_pass_info"] = pass_info
	return cfg


def normalize_ninja_proxy_fields(cfg: dict) -> dict:
	"""统一 Clash/分享链接字段，满足 ninja 内核必填项。"""
	out = dict(cfg)
	out["type"] = "ninja"
	if "name" not in out or not out.get("name"):
		out["name"] = out.get("remarks") or out.get("server") or "Ninja"
	if "remarks" not in out:
		out["remarks"] = out["name"]
	out.setdefault("group", out.get("group") or "N/A")

	port = out.get("port") or out.get("server_port")
	if port is not None:
		out["port"] = int(port)
		out["server_port"] = int(port)

	# 内核报错字段名为 node-password；结构体 tag 也接受 node_password
	np = out.get("node-password")
	if np in (None, "") and out.get("node_password") not in (None, ""):
		np = out.get("node_password")
	if np not in (None, ""):
		out["node-password"] = str(np)
		out["node_password"] = str(np)

	method = out.get("method") or out.get("cipher")
	if method:
		out["method"] = str(method)
		out.setdefault("cipher", str(method))

	# passversion 别名
	if "passversion" not in out and out.get("pass-version") is not None:
		out["passversion"] = out.get("pass-version")
	if "pass-opts" not in out and out.get("pass_opts") is not None:
		out["pass-opts"] = out.get("pass_opts")

	out.setdefault("udp", True)
	return out


def parse_ninja_link(link: str) -> Optional[Dict[str, Any]]:
	"""解析 ninja://password@host:port?...#name 分享链接。"""
	parsed = urlsplit(link.strip())
	if parsed.scheme.lower() != "ninja":
		return None
	server = parsed.hostname
	port = parsed.port
	if not server or not port:
		return None
	query = parse_qs(parsed.query)
	name = unquote(parsed.fragment) if parsed.fragment else server

	def q(key: str, default: str = "") -> str:
		return (query.get(key, [default])[0] or default)

	def q_bool(key: str, default: bool = False) -> bool:
		v = q(key, "")
		if v == "":
			return default
		return v.lower() in ("1", "true", "yes", "on")

	password = unquote(parsed.username or "") or q("password")
	cfg: Dict[str, Any] = {
		"type": "ninja",
		"name": name,
		"remarks": name,
		"group": "N/A",
		"server": server,
		"port": int(port),
		"server_port": int(port),
		"password": password,
		"udp": True,
	}
	uuid = q("uuid")
	if uuid:
		cfg["uuid"] = uuid
	method = q("method") or q("cipher")
	if method:
		cfg["method"] = method
		cfg["cipher"] = method
	node_pw = q("node-password") or q("node_password") or q("nodepassword")
	if node_pw:
		cfg["node-password"] = node_pw
		cfg["node_password"] = node_pw
	sni = q("sni") or q("servername") or q("peer")
	if sni:
		cfg["servername"] = sni
		cfg["sni"] = sni
	fp = q("fp") or q("client-fingerprint")
	if fp:
		cfg["client-fingerprint"] = fp
	pv = q("passversion") or q("pass-version")
	if pv:
		try:
			cfg["passversion"] = int(pv)
		except ValueError:
			cfg["passversion"] = pv
	if q_bool("tls") or sni or q("security").lower() in ("tls", "reality"):
		cfg["tls"] = True
	if q_bool("allowInsecure") or q_bool("insecure") or q_bool("skip-cert-verify"):
		cfg["skip-cert-verify"] = True
	network = q("type") or q("network")
	if network:
		cfg["network"] = network
	path = q("path")
	host = q("host")
	if network == "ws" or path or host:
		cfg["network"] = cfg.get("network") or "ws"
		cfg["ws-opts"] = {
			"path": path or "/",
			"headers": {"Host": host} if host else {},
		}
	pbk = q("pbk") or q("public-key")
	sid = q("sid") or q("short-id")
	if pbk or sid or q("security").lower() == "reality" or str(cfg.get("passversion")) == "3":
		cfg["reality-opts"] = {"public-key": pbk, "short-id": sid}
		cfg.setdefault("tls", True)
		if str(cfg.get("passversion") or "") == "":
			cfg["passversion"] = 3
	return normalize_ninja_proxy_fields(cfg)


def strip_internal_ninja_keys(proxy: dict) -> dict:
	"""写入内核前去掉本程序内部字段。"""
	out = dict(proxy)
	for k in list(out.keys()):
		if k.startswith("_ninja"):
			out.pop(k, None)
	return out
