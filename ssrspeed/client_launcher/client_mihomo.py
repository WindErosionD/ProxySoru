#coding:utf-8

import os
import shutil
import subprocess
import sys
import time
import logging
from urllib.parse import quote

import yaml

try:
	import requests
except ImportError:
	requests = None

from .base_client import BaseClient
from .mihomo_proxy_adapt import config_summary_for_log, config_to_mihomo_proxy
from config import config, PROJECT_ROOT

logger = logging.getLogger("Sub")

_CFG_PATH = os.path.join(PROJECT_ROOT, "config_mihomo.yaml")


def _perf() -> dict:
	return config.get("performance", {}) or {}


def _api_host() -> str:
	return str(_perf().get("mihomo_api_host", "127.0.0.1"))


def _api_port() -> int:
	return int(_perf().get("mihomo_api_port", 9190))


def _api_secret() -> str:
	return str(_perf().get("mihomo_api_secret", "") or "")


_MIHOMO_DOH = {
	"cloudflare": "https://cloudflare-dns.com/dns-query",
	"google": "https://dns.google/dns-query",
	"quad9": "https://dns.quad9.net/dns-query",
}


def _mihomo_hot_reload_enabled() -> bool:
	return bool(_perf().get("mihomo_hot_reload", True))


def _mihomo_tunnel_check_enabled() -> bool:
	"""默认关闭：避免每节点 delay API 拖慢测速；仅排查 0 速时开启。"""
	return bool(_perf().get("mihomo_tunnel_check", False))


def _build_mihomo_dns() -> dict:
	"""Mihomo DNS：默认 fake-ip（兼容 SOCKS+HTTPS）；off 则完全关闭。"""
	dns_cfg = config.get("dns") or {}
	mode = str(dns_cfg.get("mihomo_dns_mode", "") or "").strip().lower()
	if not mode:
		if dns_cfg.get("mihomo_builtin_dns") is True:
			mode = "redir-host"
		elif dns_cfg.get("mihomo_builtin_dns") is False:
			mode = "fake-ip"
		else:
			mode = "fake-ip"
	if mode in ("off", "false", "0", "none", "disable", "disabled"):
		return {}
	if not dns_cfg.get("enabled", True):
		return {}
	if mode == "redir-host":
		order = dns_cfg.get("providers") or ["cloudflare", "google", "quad9"]
		doh_urls = []
		for name in order:
			url = _MIHOMO_DOH.get(str(name).lower().strip())
			if url and url not in doh_urls:
				doh_urls.append(url)
		if not doh_urls:
			doh_urls = list(_MIHOMO_DOH.values())
		return {
			"dns": {
				"enable": True,
				"ipv6": False,
				"enhanced-mode": "redir-host",
				"default-nameserver": ["223.5.5.5", "119.29.29.29", "1.1.1.1"],
				"nameserver": doh_urls,
				"proxy-server-nameserver": doh_urls + ["223.5.5.5", "119.29.29.29"],
			}
		}
	# fake-ip：与 Clash 客户端一致，SOCKS 远程域名解析更稳
	return {
		"dns": {
			"enable": True,
			"ipv6": False,
			"enhanced-mode": "fake-ip",
			"fake-ip-range": "198.18.0.1/16",
			"fake-ip-filter": ["+.lan", "+.local", "+.localhost"],
			"default-nameserver": ["223.5.5.5", "119.29.29.29", "1.1.1.1"],
			"nameserver": ["223.5.5.5", "119.29.29.29", "https://dns.google/dns-query"],
			"proxy-server-nameserver": ["223.5.5.5", "119.29.29.29", "1.1.1.1"],
		}
	}


def _probe_mihomo_version_line(binary: str) -> str:
	for args in (("-v",), ("version",), ("--version",)):
		try:
			cp = subprocess.run(
				[binary] + list(args),
				capture_output=True,
				timeout=6,
				text=True,
				encoding="utf-8",
				errors="replace",
			)
			out = (cp.stdout or "").strip() or (cp.stderr or "").strip()
			if out:
				return out.splitlines()[0][:240]
		except (OSError, subprocess.TimeoutExpired):
			continue
	return ""


class Mihomo(BaseClient):
	def __init__(self):
		super(Mihomo, self).__init__()
		self._binary = None
		self._cfg_path = os.path.abspath(_CFG_PATH)
		self._api_base = "http://{}:{}".format(_api_host(), _api_port())
		self._nodes_since_start = 0
		self._hard_restart_every = int(_perf().get("mihomo_hard_restart_every", 80) or 0)
		self._settle_after_reload = float(_perf().get("mihomo_settle_seconds", 0.12))
		self._api_timeout = float(_perf().get("mihomo_api_timeout", 5))
		self._delay_timeout_ms = int(_perf().get("mihomo_delay_timeout_ms", 3000))
		self._delay_test_url = str(
			_perf().get("mihomo_delay_test_url", "http://www.gstatic.com/generate_204")
		)
		self._stderr_file = None

	def __api_headers(self):
		headers = {"Content-Type": "application/json"}
		secret = _api_secret()
		if secret:
			headers["Authorization"] = "Bearer {}".format(secret)
		return headers

	def __build_runtime_config(self, proxy_cfg: dict) -> dict:
		proxy_name = proxy_cfg.get("name", proxy_cfg.get("server", "SSRSpeedNode"))
		runtime = {
			"mixed-port": config["localPort"],
			"port": 0,
			"socks-port": 0,
			"allow-lan": False,
			"ipv6": False,
			"mode": "rule",
			"log-level": "info",
			"external-controller": "{}:{}".format(_api_host(), _api_port()),
			"sniffer": {"enable": False},
			"proxies": [proxy_cfg],
			"proxy-groups": [
				{
					"name": "SSRSpeed",
					"type": "select",
					"proxies": [proxy_name],
				}
			],
			"rules": ["MATCH,SSRSpeed"],
		}
		secret = _api_secret()
		if secret:
			runtime["secret"] = secret
		runtime.update(_build_mihomo_dns())
		return runtime

	def __mihomo_stderr_path(self) -> str:
		log_dir = os.path.join(PROJECT_ROOT, "logs")
		os.makedirs(log_dir, exist_ok=True)
		return os.path.join(log_dir, "mihomo_stderr.log")

	def __close_stderr_file(self):
		f = self._stderr_file
		self._stderr_file = None
		if f is not None:
			try:
				f.close()
			except OSError:
				pass

	def __select_proxy(self, proxy_name: str) -> bool:
		if requests is None or not proxy_name:
			return False
		try:
			r = requests.put(
				"{}/proxies/SSRSpeed".format(self._api_base),
				json={"name": proxy_name},
				headers=self.__api_headers(),
				timeout=self._api_timeout,
			)
			if r.status_code in (200, 204):
				logger.debug("Mihomo selected proxy: %s", proxy_name)
				return True
			logger.warning(
				"Mihomo select proxy failed: %s status=%s",
				proxy_name,
				r.status_code,
			)
		except requests.RequestException as e:
			logger.warning("Mihomo select proxy failed: %s (%s)", proxy_name, e)
		return False

	def __measure_delay(self) -> int:
		"""通过 Mihomo API 测延迟；返回毫秒，0 表示不可用。"""
		if requests is None:
			return 0
		try:
			r = requests.get(
				"{}/proxies/{}/delay".format(self._api_base, quote("SSRSpeed", safe="")),
				params={
					"timeout": self._delay_timeout_ms,
					"url": self._delay_test_url,
				},
				headers=self.__api_headers(),
				timeout=max(self._api_timeout, self._delay_timeout_ms / 1000.0 + 2),
			)
			if r.status_code != 200:
				logger.warning("Mihomo delay API status=%s body=%s", r.status_code, (r.text or "")[:200])
				return 0
			data = r.json() if r.content else {}
			delay = int(data.get("delay") or 0)
			return delay if delay > 0 else 0
		except (requests.RequestException, ValueError, TypeError) as e:
			logger.warning("Mihomo delay API failed: %s", e)
			return 0

	def wait_proxy_ready(self, node_config: dict) -> bool:
		"""快速路径：热重载后仅检查进程；可选 delay 探测（mihomo_tunnel_check=true 时）。"""
		if self._process is None or self._process.poll() is not None:
			return False
		if not _mihomo_tunnel_check_enabled():
			return True
		try:
			proxy_cfg = config_to_mihomo_proxy(node_config)
		except Exception:
			logger.exception("wait_proxy_ready: invalid node config")
			return False
		self.__select_proxy(proxy_cfg.get("name", ""))
		delay = self.__measure_delay()
		if delay > 0:
			logger.info("Mihomo tunnel ready (delay %d ms).", delay)
			return True
		logger.warning(
			"Mihomo tunnel not ready (delay=0). server=%s:%s",
			proxy_cfg.get("server"),
			proxy_cfg.get("port"),
		)
		return False

	def __find_binary(self):
		if self._binary:
			return self._binary
		root = PROJECT_ROOT
		if self._checkPlatform() == "Windows":
			candidates = [
				os.path.join(root, "clients", "mihomo", "mihomo.exe"),
				os.path.join(root, "clients", "clash-meta", "mihomo.exe"),
				os.path.join(root, "clients", "clash", "mihomo.exe"),
			]
			which_candidates = ["mihomo.exe", "mihomo", "clash-meta.exe", "clash-meta", "clash.exe", "clash"]
		elif self._checkPlatform() in ("Linux", "MacOS"):
			candidates = [
				os.path.join(root, "clients", "mihomo", "mihomo"),
				os.path.join(root, "clients", "clash-meta", "mihomo"),
				os.path.join(root, "clients", "clash", "mihomo"),
			]
			which_candidates = ["mihomo", "clash-meta", "clash"]
		else:
			logger.critical("Your system does not supported.Please contact developer.")
			sys.exit(1)

		for binary in candidates:
			if os.path.isfile(binary):
				self._binary = os.path.normpath(binary)
				return self._binary
		for binary in which_candidates:
			which_res = shutil.which(binary)
			if which_res:
				self._binary = os.path.normpath(which_res)
				return self._binary
		return None

	def __runtime_yaml_text(self, runtime_cfg: dict) -> str:
		return yaml.safe_dump(runtime_cfg, allow_unicode=True, sort_keys=False)

	def __write_runtime_config(self, runtime_cfg: dict):
		text = self.__runtime_yaml_text(runtime_cfg)
		with open(self._cfg_path, "w", encoding="utf-8") as f:
			f.write(text)
			f.flush()
			try:
				os.fsync(f.fileno())
			except OSError:
				pass

	def __config_reload_path(self) -> str:
		return os.path.abspath(self._cfg_path).replace("\\", "/")

	def __verify_proxy_active(self, proxy_cfg: dict) -> bool:
		"""热重载后核对策略组已选中当前节点（Mihomo API 不返回 server 字段）。"""
		if requests is None:
			return True
		name = proxy_cfg.get("name") or ""
		if not name:
			return False
		try:
			rg = requests.get(
				"{}/proxies/{}".format(self._api_base, quote("SSRSpeed", safe="")),
				headers=self.__api_headers(),
				timeout=min(2.0, self._api_timeout),
			)
			if rg.status_code != 200:
				return False
			now = (rg.json() or {}).get("now")
			if now != name:
				logger.warning(
					"Mihomo group selection mismatch: now=%r want=%r",
					now,
					name,
				)
				return False
			rp = requests.get(
				"{}/proxies/{}".format(self._api_base, quote(name, safe="")),
				headers=self.__api_headers(),
				timeout=min(2.0, self._api_timeout),
			)
			return rp.status_code == 200
		except requests.RequestException as e:
			logger.warning("Mihomo proxy verify failed: %s", e)
			return False

	def __api_ready(self) -> bool:
		if requests is None:
			return False
		try:
			r = requests.get(
				"{}/version".format(self._api_base),
				headers=self.__api_headers(),
				timeout=min(2.0, self._api_timeout),
			)
			return r.status_code == 200
		except requests.RequestException:
			return False

	def flush_connections(self):
		"""关闭 Mihomo 内全部活跃连接，避免上一节点残留影响测速。"""
		if requests is None or self._process is None:
			return
		try:
			r = requests.delete(
				"{}/connections".format(self._api_base),
				headers=self.__api_headers(),
				timeout=self._api_timeout,
			)
			if r.status_code in (200, 204):
				logger.debug("Mihomo connections flushed.")
		except requests.RequestException as e:
			logger.debug("Flush connections skipped: %s", e)

	def __reload_config_file(self, runtime_cfg: dict = None) -> bool:
		if requests is None:
			return False
		payloads = []
		if runtime_cfg is not None:
			payloads.append({"payload": self.__runtime_yaml_text(runtime_cfg)})
		payloads.append({"path": self.__config_reload_path()})
		endpoints = [
			("PUT", "{}/configs".format(self._api_base), {"force": "true"}),
			("PATCH", "{}/configs".format(self._api_base), {"force": "true"}),
		]
		for body in payloads:
			for method, url, params in endpoints:
				try:
					r = requests.request(
						method,
						url,
						params=params,
						json=body,
						headers=self.__api_headers(),
						timeout=self._api_timeout,
					)
					if r.status_code in (200, 204):
						logger.debug(
							"Mihomo config reloaded via %s (%s).",
							method,
							"payload" if "payload" in body else "path",
						)
						return True
				except requests.RequestException as e:
					logger.debug("Mihomo reload %s failed: %s", method, e)
		return False

	def __spawn_process(self, binary: str):
		self.__close_stderr_file()
		cmd = [binary, "-f", self._cfg_path]
		pop_kw = {"cwd": PROJECT_ROOT}
		err_path = self.__mihomo_stderr_path()
		try:
			err_f = open(err_path, "a", encoding="utf-8", errors="replace")
			err_f.write("\n--- Mihomo start {} ---\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
			err_f.flush()
			self._stderr_file = err_f
			self._process = subprocess.Popen(
				cmd,
				stdout=subprocess.DEVNULL,
				stderr=err_f,
				**pop_kw,
			)
		except OSError:
			self.__close_stderr_file()
			self._process = subprocess.Popen(cmd, **pop_kw)
		self._nodes_since_start = 0

	def __wait_api_ready(self, label: str = "startup") -> None:
		deadline = time.time() + max(self._api_timeout * 3, 8.0)
		while time.time() < deadline:
			if self._process and self._process.poll() is not None:
				raise OSError("Mihomo exited during {}.".format(label))
			if self.__api_ready():
				return
			time.sleep(0.1)
		logger.warning("Mihomo API not ready after %s; continuing with port check.", label)

	def __hard_restart(self, binary: str, proxy_cfg: dict):
		logger.info("Mihomo hard restart (reload fallback or periodic refresh).")
		self.stopClient()
		self.__spawn_process(binary)
		logger.info(
			"Starting mihomo with server %s:%d",
			proxy_cfg.get("server", "N/A"),
			int(proxy_cfg.get("port", 0)),
		)
		self.__wait_api_ready("hard restart")
		self.__select_proxy(proxy_cfg.get("name", ""))

	def __apply_node_config(
		self,
		proxy_cfg: dict,
		runtime_cfg: dict = None,
		force_restart: bool = False,
	):
		binary = self.__find_binary()
		if not binary:
			logger.error(
				"未找到 Mihomo / Clash Meta 可执行文件。请将内核放入 clients/mihomo/ 或加入 PATH。"
				"说明见: clients/mihomo/README.md"
			)
			sys.exit(1)

		if self._process is None:
			if not self._binary:
				ver = _probe_mihomo_version_line(binary)
				if ver:
					logger.info("Mihomo 可执行文件: %s | %s", binary, ver)
				else:
					logger.info(
						"Mihomo 可执行文件: %s（未能读取版本，可手动执行 \"%s -v\"）",
						binary,
						binary,
					)
			self.__spawn_process(binary)
			logger.info(
				"Starting mihomo with server %s:%d",
				proxy_cfg.get("server", "N/A"),
				int(proxy_cfg.get("port", 0)),
			)
			self.__wait_api_ready("cold start")
			self.__select_proxy(proxy_cfg.get("name", ""))
			return

		use_restart = force_restart or not _mihomo_hot_reload_enabled()
		if self._hard_restart_every > 0:
			self._nodes_since_start += 1
			if self._nodes_since_start >= self._hard_restart_every:
				use_restart = True
				self._nodes_since_start = 0

		if use_restart:
			self.__hard_restart(binary, proxy_cfg)
			return

		self.flush_connections()
		if not self.__reload_config_file(runtime_cfg):
			logger.warning("Mihomo hot reload failed; falling back to process restart.")
			self.__hard_restart(binary, proxy_cfg)
			return

		if self._settle_after_reload > 0:
			time.sleep(self._settle_after_reload)
		self.flush_connections()
		self.__select_proxy(proxy_cfg.get("name", ""))
		if not self.__verify_proxy_active(proxy_cfg):
			logger.warning("Hot reload did not apply node; hard restart once.")
			self.__hard_restart(binary, proxy_cfg)

	def force_hard_restart(self, config: dict):
		"""0 速重试等场景：跳过热重载，强制冷启动当前节点。"""
		proxy_cfg = config_to_mihomo_proxy(config)
		runtime_cfg = self.__build_runtime_config(proxy_cfg)
		self.__write_runtime_config(runtime_cfg)
		self.__apply_node_config(proxy_cfg, runtime_cfg=runtime_cfg, force_restart=True)

	def startClient(self, config={}):
		self._config = config
		try:
			proxy_cfg = config_to_mihomo_proxy(config)
		except Exception:
			logger.error("节点摘要(脱敏): %s", config_summary_for_log(config))
			logger.exception("节点配置无法转换为 Mihomo 单条 proxy（请核对协议/字段或升级内核）")
			sys.exit(1)

		runtime_cfg = self.__build_runtime_config(proxy_cfg)
		self.__write_runtime_config(runtime_cfg)

		try:
			self.__apply_node_config(proxy_cfg, runtime_cfg=runtime_cfg, force_restart=False)
		except FileNotFoundError:
			logger.error("Mihomo 启动失败：文件不可执行或已被删除: %s", self._binary)
			sys.exit(1)
		except OSError as e:
			logger.error("Mihomo 启动失败: %s | binary=%s", e, self._binary)
			sys.exit(1)

	def afterNode(self):
		"""单节点测速结束：清理连接，保留进程供下一节点热重载。"""
		if self._process is None:
			return
		if self._process.poll() is not None:
			logger.warning("Mihomo process exited; next node will cold-start.")
			self._process = None
			return
		self.flush_connections()

	def _beforeStopClient(self):
		self.flush_connections()

	def stopClient(self):
		"""测速批次结束：终止进程并释放句柄。"""
		self._beforeStopClient()
		proc = self._process
		self._process = None
		self._nodes_since_start = 0
		if proc is None:
			self.__close_stderr_file()
			return
		try:
			if self._checkPlatform() == "Windows":
				proc.terminate()
			else:
				proc.send_signal(__import__("signal").SIGINT)
			try:
				proc.wait(timeout=8)
			except subprocess.TimeoutExpired:
				logger.warning("Mihomo did not exit in time; killing.")
				proc.kill()
				proc.wait(timeout=3)
		except Exception:
			logger.exception("Error while stopping Mihomo.")
		finally:
			self.__close_stderr_file()
			logger.info("Client terminated.")
