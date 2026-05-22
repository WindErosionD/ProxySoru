#coding:utf-8

import os
import shutil
import subprocess
import sys
import time
import logging

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
		self._hard_restart_every = int(_perf().get("mihomo_hard_restart_every", 0) or 0)
		self._settle_after_reload = float(_perf().get("mihomo_settle_seconds", 0.12))
		self._api_timeout = float(_perf().get("mihomo_api_timeout", 5))

	def __api_headers(self):
		headers = {"Content-Type": "application/json"}
		secret = _api_secret()
		if secret:
			headers["Authorization"] = "Bearer {}".format(secret)
		return headers

	def __build_runtime_config(self, proxy_cfg: dict) -> dict:
		proxy_name = proxy_cfg.get("name", proxy_cfg.get("server", "SSRSpeedNode"))
		return {
			"mixed-port": 0,
			"port": 0,
			"socks-port": config["localPort"],
			"allow-lan": False,
			"mode": "rule",
			"log-level": "warning",
			"external-controller": "{}:{}".format(_api_host(), _api_port()),
			"secret": _api_secret(),
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

	def __write_runtime_config(self, runtime_cfg: dict):
		with open(self._cfg_path, "w+", encoding="utf-8") as f:
			f.write(yaml.safe_dump(runtime_cfg, allow_unicode=True, sort_keys=False))

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

	def __reload_config_file(self) -> bool:
		if requests is None:
			return False
		body = {"path": self._cfg_path}
		endpoints = [
			("PUT", "{}/configs".format(self._api_base), {"force": "true"}),
			("PATCH", "{}/configs".format(self._api_base), {"force": "true"}),
			("PUT", "{}/configs".format(self._api_base), None),
		]
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
					logger.debug("Mihomo config reloaded via %s.", method)
					return True
			except requests.RequestException as e:
				logger.debug("Mihomo reload %s failed: %s", method, e)
		return False

	def __spawn_process(self, binary: str):
		cmd = [binary, "-f", self._cfg_path]
		pop_kw = {"cwd": PROJECT_ROOT}
		if logger.level == logging.DEBUG:
			self._process = subprocess.Popen(cmd, **pop_kw)
		else:
			self._process = subprocess.Popen(
				cmd,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				**pop_kw,
			)
		self._nodes_since_start = 0

	def __hard_restart(self, binary: str, proxy_cfg: dict):
		logger.info("Mihomo hard restart (reload fallback or periodic refresh).")
		self.stopClient()
		self.__spawn_process(binary)
		logger.info(
			"Starting mihomo with server %s:%d",
			proxy_cfg.get("server", "N/A"),
			int(proxy_cfg.get("port", 0)),
		)
		deadline = time.time() + max(self._api_timeout * 3, 8.0)
		while time.time() < deadline:
			if self._process and self._process.poll() is not None:
				raise OSError("Mihomo exited during startup.")
			if self.__api_ready():
				return
			time.sleep(0.1)
		logger.warning("Mihomo API not ready after hard restart; continuing with port check.")

	def __apply_node_config(self, proxy_cfg: dict, force_restart: bool = False):
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
			return

		if force_restart:
			self.__hard_restart(binary, proxy_cfg)
			return

		self.flush_connections()
		if not self.__reload_config_file():
			logger.warning("Mihomo hot reload failed; falling back to process restart.")
			self.__hard_restart(binary, proxy_cfg)
			return

		if self._settle_after_reload > 0:
			time.sleep(self._settle_after_reload)
		self.flush_connections()

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

		force_restart = False
		if self._hard_restart_every > 0 and self._process is not None:
			self._nodes_since_start += 1
			if self._nodes_since_start >= self._hard_restart_every:
				force_restart = True
				self._nodes_since_start = 0

		try:
			self.__apply_node_config(proxy_cfg, force_restart=force_restart)
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
			logger.info("Client terminated.")
