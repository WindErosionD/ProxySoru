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
			os.path.join(self._root, "clients", "ninja", "ninja.exe"),
			os.path.join(self._root, "clients", "mihomo", "mihomo.exe"),
			os.path.join(self._root, "clients", "clash-meta", "mihomo.exe"),
			os.path.join(self._root, "clients", "clash", "mihomo.exe"),
		]

	def _candidates_unix(self):
		return [
			os.path.join(self._root, "clients", "ninja", "ninja"),
			os.path.join(self._root, "clients", "mihomo", "mihomo"),
			os.path.join(self._root, "clients", "clash-meta", "mihomo"),
			os.path.join(self._root, "clients", "clash", "mihomo"),
		]

	def _which_names(self):
		if check_platform() == "Windows":
			return (
				"ninja.exe",
				"ninja",
				"mihomo.exe",
				"mihomo",
				"clash-meta.exe",
				"clash-meta",
				"clash.exe",
				"clash",
			)
		return ("ninja", "mihomo", "clash-meta", "clash")

	def _find_mihomo_binary(self):
		if check_platform() == "Windows":
			cands = self._candidates_windows()
		elif check_platform() in ("Linux", "MacOS"):
			cands = self._candidates_unix()
		else:
			return None
		# 公版优先用于常规自检；ninja 存在时额外提示
		stock = None
		ninja = None
		for p in cands:
			if not os.path.isfile(p):
				continue
			name = os.path.basename(p).lower()
			if "ninja" in name and not ninja:
				ninja = os.path.normpath(p)
			elif not stock:
				stock = os.path.normpath(p)
		if stock:
			self._ninja_binary = ninja
			return stock
		if ninja:
			self._ninja_binary = ninja
			return ninja
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
		self._ninja_binary = None
		bin_path = self._find_mihomo_binary()
		if not bin_path:
			logger.error(
				"未找到 Mihomo / Clash Meta / Ninja 可执行文件。请将内核放入 clients/mihomo/ "
				"或 clients/ninja/，或加入 PATH。详见 clients/mihomo/README.md / clients/ninja/README.md"
			)
			return
		logger.info("Mihomo 内核路径: %s", bin_path)
		ver = self._probe_version(bin_path)
		if ver:
			logger.info("Mihomo 版本信息: %s", ver)
		else:
			logger.warning("无法读取 Mihomo 版本（可忽略）；可手动执行: \"%s -v\"", bin_path)
		ninja = getattr(self, "_ninja_binary", None)
		if ninja and os.path.normpath(ninja) != os.path.normpath(bin_path):
			nver = self._probe_version(ninja)
			logger.info("Ninja 内核路径: %s%s", ninja, (" | " + nver) if nver else "")
		elif not ninja:
			# 再扫一遍专用目录，方便提示
			win = os.path.join(self._root, "clients", "ninja", "ninja.exe")
			unix = os.path.join(self._root, "clients", "ninja", "ninja")
			if not (os.path.isfile(win) or os.path.isfile(unix)):
				logger.info(
					"未检测到 Ninja 内核（clients/ninja/）。若需测速 type:ninja 节点，"
					"请按 clients/ninja/README.md 放置官方内核。"
				)
