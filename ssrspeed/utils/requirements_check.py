# coding: utf-8

import logging
import os
import shutil
import subprocess
import sys

from config import PROJECT_ROOT

from .platform_check import check_platform

logger = logging.getLogger("Sub")


class RequirementsCheck(object):
	"""仅校验 Mihomo / Clash Meta 内核是否可用。"""

	def __init__(self):
		self._root = PROJECT_ROOT

	def _candidates_windows(self):
		return [
			os.path.join(self._root, "clients", "mihomo", "mihomo.exe"),
			os.path.join(self._root, "clients", "clash-meta", "mihomo.exe"),
			os.path.join(self._root, "clients", "clash", "mihomo.exe"),
		]

	def _candidates_unix(self):
		return [
			os.path.join(self._root, "clients", "mihomo", "mihomo"),
			os.path.join(self._root, "clients", "clash-meta", "mihomo"),
			os.path.join(self._root, "clients", "clash", "mihomo"),
		]

	def _which_names(self):
		if check_platform() == "Windows":
			return ("mihomo.exe", "mihomo", "clash-meta.exe", "clash-meta", "clash.exe", "clash")
		return ("mihomo", "clash-meta", "clash")

	def _find_mihomo_binary(self):
		if check_platform() == "Windows":
			cands = self._candidates_windows()
		elif check_platform() in ("Linux", "MacOS"):
			cands = self._candidates_unix()
		else:
			return None
		for p in cands:
			if os.path.isfile(p):
				return os.path.normpath(p)
		for name in self._which_names():
			w = shutil.which(name)
			if w:
				return w
		return None

	def _probe_version(self, binary: str) -> str:
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
					return out.splitlines()[0][:200]
			except (OSError, subprocess.TimeoutExpired):
				continue
		return ""

	def check(self):
		pf = check_platform()
		if pf == "Unknown":
			logger.critical("Unsupported platform.")
			sys.exit(1)
		bin_path = self._find_mihomo_binary()
		if not bin_path:
			logger.error(
				"未找到 Mihomo / Clash Meta 可执行文件。请将内核放入 clients/mihomo/ 或加入 PATH，"
				"详见 clients/mihomo/README.md"
			)
			return
		logger.info("Mihomo 内核路径: %s", bin_path)
		ver = self._probe_version(bin_path)
		if ver:
			logger.info("Mihomo 版本信息: %s", ver)
		else:
			logger.warning("无法读取 Mihomo 版本（可忽略）；可手动执行: \"%s -v\"", bin_path)
