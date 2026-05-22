# coding: utf-8
"""
在 Windows 上生成「开箱即用」便携目录：内置 CPython embeddable + 已安装依赖 + 项目文件。

用法（需联网，建议在仓库根目录执行）:
  py -3 tools\\build_portable.py
  py -3 tools\\build_portable.py --out D:\\dist_pack --no-zip

产物默认: dist\\ProxySoru_portable\\ 以及 dist\\ProxySoru_portable.zip
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

EMBED_VERSIONS = ("3.12.8", "3.12.7", "3.12.6", "3.12.5")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
TSINGHUA_SIMPLE = "https://pypi.tuna.tsinghua.edu.cn/simple"

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 便携包目录/zip 基名（dist/ProxySoru_portable、ProxySoru_portable.zip）
PORTABLE_DIR_NAME = "ProxySoru_portable"

from env_cache import remove_pycache_trees
from pip_requirements_install import DEFAULT_INDEX as _PIP_INDEX, install_requirements_check

COPY_DIRS = ("ssrspeed", "clients", "tools", "resources")
COPY_FILES = (
	"main.py",
	"config.py",
	"requirements.txt",
	"ssrspeed_config.example.json",
	"config_mihomo.yaml.example",
	"一键测速.bat",
	"RUN_SPEED_TEST_ASCII.bat",
	"ver.txt",
	"README.md",
)

PORTABLE_MARKER_TEXT = (
	"ProxySoru portable bundle marker.\n"
	"Do not delete this file or the python\\ folder, or the portable launcher will fall back to system Python.\n"
)


def _die(msg: str, code: int = 1) -> None:
	print(msg, file=sys.stderr, flush=True)
	raise SystemExit(code)


def _on_rm_error(func, path, exc_info):
	import stat

	if not os.access(path, os.W_OK):
		os.chmod(path, stat.S_IWRITE)
		func(path)
	else:
		raise exc_info[1]


def _safe_remove_tree(path: str) -> None:
	"""删除输出目录；Windows 上若 python.exe 仍被占用会重试并给出明确提示。"""
	if not os.path.exists(path):
		return
	last_err = None
	for attempt in range(8):
		try:
			shutil.rmtree(path, onerror=_on_rm_error)
			if not os.path.exists(path):
				return
		except OSError as e:
			last_err = e
			time.sleep(0.8 + attempt * 0.4)
	_die(
		"无法删除目录（请先关闭一键测速窗口、任务管理器中的 mihomo.exe / python.exe，再重试）：\n  {}\n  {}".format(
			path, last_err
		)
	)


def _warn_if_path_has_non_ascii(*paths: str) -> None:
	for p in paths:
		if not p:
			continue
		try:
			os.fsencode(p).decode("ascii")
		except (UnicodeDecodeError, UnicodeError):
			print(
				"[警告] 路径含中文等非 ASCII 字符：\n  {}\n"
				"pip 编译/构建子进程可能失败，建议将仓库放到纯英文路径（如 C:\\ProxySoru）后重试。".format(p),
				flush=True,
			)
			break


def _download(url: str, dest: str) -> None:
	print("[下载] {}".format(url), flush=True)
	t0 = time.monotonic()
	urllib.request.urlretrieve(url, dest)
	dt = time.monotonic() - t0
	print("[下载] 完成，约 {:.1f} 秒，保存到 {}".format(dt, dest), flush=True)


def _fix_embed_pth(python_dir: str) -> None:
	for pth in glob.glob(os.path.join(python_dir, "python*._pth")):
		with open(pth, encoding="utf-8", errors="replace") as f:
			lines = f.read().splitlines()
		out = []
		have_site = False
		for line in lines:
			s = line.strip()
			if s in ("#import site", "# import site"):
				out.append("import site")
				have_site = True
			else:
				out.append(line)
				if s == "import site":
					have_site = True
		if not have_site:
			out.append("import site")
		with open(pth, "w", encoding="utf-8", newline="\n") as f:
			f.write("\n".join(out) + "\n")
		print("[配置] 已启用 site-packages：{}".format(os.path.basename(pth)), flush=True)


def _pick_embed_url() -> tuple[str, str]:
	last_err = None
	for ver in EMBED_VERSIONS:
		name = "python-{}-embed-amd64.zip".format(ver)
		url = "https://www.python.org/ftp/python/{}/{}".format(ver, name)
		try:
			req = urllib.request.Request(url, method="HEAD")
			with urllib.request.urlopen(req, timeout=15) as r:
				if r.status == 200:
					return url, ver
		except OSError as e:
			last_err = e
			continue
	if last_err:
		_die("无法从 python.org 解析可用的 embeddable 版本（已尝试 {}）：{}".format(EMBED_VERSIONS, last_err))
	_die("无法从 python.org 解析可用的 embeddable 版本。")


def _copy_runtime_config(out_root: str) -> None:
	"""便携包与仓库根目录使用同一份 ssrspeed_config.json，不做二次改写。"""
	dst_cfg = os.path.join(out_root, "ssrspeed_config.json")
	src_cfg = os.path.join(ROOT_DIR, "ssrspeed_config.json")
	if os.path.isfile(src_cfg):
		shutil.copy2(src_cfg, dst_cfg)
		print("[配置] 已复制 ssrspeed_config.json（与打包前仓库根目录一致）", flush=True)
		return
	example = os.path.join(ROOT_DIR, "ssrspeed_config.example.json")
	if os.path.isfile(example):
		shutil.copy2(example, dst_cfg)
		print(
			"[配置] 仓库根目录无 ssrspeed_config.json，已使用 ssrspeed_config.example.json",
			flush=True,
		)
		return
	print("[配置] 警告：未找到 ssrspeed_config.json / example，便携包将首次运行时由 config.py 生成", flush=True)


def _verify_portable_parity(out_root: str) -> None:
	"""打包后校验：配置与源码目录一致，关键修复已带入。"""
	src_cfg = os.path.join(ROOT_DIR, "ssrspeed_config.json")
	dst_cfg = os.path.join(out_root, "ssrspeed_config.json")
	if os.path.isfile(src_cfg) and os.path.isfile(dst_cfg):
		with open(src_cfg, "rb") as a, open(dst_cfg, "rb") as b:
			if a.read() != b.read():
				_die("便携包 ssrspeed_config.json 与仓库根目录不一致，请重新打包。")
	core_py = os.path.join(out_root, "ssrspeed", "core", "ssrspeed_core.py")
	if os.path.isfile(core_py):
		with open(core_py, encoding="utf-8", errors="replace") as f:
			text = f.read()
		if "subscription_url_needs_decrypt" not in text:
			print("[警告] 便携包 ssrspeed_core.py 可能缺少订阅链接修复，请确认已保存源码后重打。", flush=True)
		if "'token='" in text and "domainls = [" in text and "token=" in text.split("domainls = [", 1)[-1][:80]:
			print("[警告] domainls 仍含 token=，订阅 URL 可能被误解密。", flush=True)
	adapt_py = os.path.join(out_root, "ssrspeed", "client_launcher", "mihomo_proxy_adapt.py")
	if os.path.isfile(adapt_py):
		with open(adapt_py, encoding="utf-8", errors="replace") as f:
			if "_normalize_ss_plugin_for_mihomo" not in f.read():
				print("[警告] mihomo_proxy_adapt 可能缺少 obfs 插件映射，SS 节点或无法测速。", flush=True)
	print("[校验] 便携包内容与仓库源码对齐检查完成。", flush=True)


def _copy_project(out_root: str) -> None:
	for d in COPY_DIRS:
		src = os.path.join(ROOT_DIR, d)
		if not os.path.isdir(src):
			continue
		dst = os.path.join(out_root, d)
		if os.path.exists(dst):
			shutil.rmtree(dst, ignore_errors=True)
		shutil.copytree(
			src,
			dst,
			ignore=shutil.ignore_patterns(
				"__pycache__",
				"*.pyc",
				".git",
			),
		)
		print("[复制] {} -> {}".format(d, dst), flush=True)

	for name in COPY_FILES:
		src = os.path.join(ROOT_DIR, name)
		if not os.path.isfile(src):
			continue
		dst = os.path.join(out_root, name)
		os.makedirs(os.path.dirname(dst), exist_ok=True)
		shutil.copy2(src, dst)
		print("[复制] {}".format(name), flush=True)

	# 以仓库根目录 ssrspeed_config.json 为准覆盖（避免 COPY_FILES 中缺失或旧文件）
	_copy_runtime_config(out_root)

	marker = os.path.join(out_root, "PORTABLE_BUILD")
	with open(marker, "w", encoding="utf-8", newline="\n") as f:
		f.write(PORTABLE_MARKER_TEXT)
		f.write("Built at unix_ts={}\n".format(int(time.time())))
	print("[标记] 已写入 PORTABLE_BUILD", flush=True)

	readme = os.path.join(out_root, "便携版说明.txt")
	with open(readme, "w", encoding="utf-8", newline="\r\n") as f:
		f.write(
			"ProxySoru 便携版使用说明\r\n"
			"\r\n"
			"1. 解压本文件夹（或直接使用本目录）到任意路径，路径中尽量不要含中文或空格。\r\n"
			"2. 双击运行「一键测速.bat」。无需再安装 Python。\r\n"
			"3. 请勿删除「python」目录与「PORTABLE_BUILD」文件，否则将退回需本机 Python 的模式。\r\n"
			"4. 若运行 mihomo / curl-cffi 相关组件报错，请安装微软 VC++ 运行库（Visual C++ Redistributable for VS 2015–2022 x64）。\r\n"
			"5. 使用 Selenium 相关功能需自行安装 Chrome 浏览器。\r\n"
			"6. 测速配置与打包前仓库根目录 ssrspeed_config.json 完全相同（仅内置 Python，不改参数）。\r\n"
			"7. 若结果图全为 N/A，请查看 logs 下日志是否出现 Starting speed test。\r\n"
		)
	print("[说明] 已写入 便携版说明.txt", flush=True)


def build(out_root: str, make_zip: bool) -> None:
	if os.name != "nt":
		_die("便携包构建仅支持 Windows（需 embeddable amd64）。")
	if not os.path.isfile(os.path.join(ROOT_DIR, "requirements.txt")):
		_die("未找到 requirements.txt，请在仓库根目录运行本脚本。")
	_warn_if_path_has_non_ascii(ROOT_DIR, out_root)

	if os.path.isdir(out_root):
		print("[清理] {}".format(out_root), flush=True)
		_safe_remove_tree(out_root)

	url, ver = _pick_embed_url()
	print("[信息] 使用 Python embeddable {}".format(ver), flush=True)

	with tempfile.TemporaryDirectory() as td:
		embed_dir = os.path.join(td, "embed")
		os.makedirs(embed_dir, exist_ok=True)
		zip_path = os.path.join(td, "embed.zip")
		_download(url, zip_path)
		with zipfile.ZipFile(zip_path, "r") as zf:
			zf.extractall(embed_dir)
		_fix_embed_pth(embed_dir)
		py_exe = os.path.join(embed_dir, "python.exe")
		if not os.path.isfile(py_exe):
			_die("解压后未找到 python.exe：{}".format(embed_dir))

		get_pip = os.path.join(td, "get-pip.py")
		_download(GET_PIP_URL, get_pip)
		print("[pip] 安装 pip 到 embed 环境…", flush=True)
		subprocess.check_call([py_exe, get_pip], cwd=embed_dir)

		print("[pip] 安装依赖（requirements.txt，镜像：清华）…", flush=True)
		req = os.path.join(ROOT_DIR, "requirements.txt")
		install_requirements_check(py_exe, req, index=_PIP_INDEX, cwd=ROOT_DIR)

		# embed 的 ._pth 里「.」是进程 cwd；用相对 .pth 把便携根目录固定进 sys.path
		sp_anchor = os.path.join(embed_dir, "Lib", "site-packages", "proxysoru_portable_root.pth")
		os.makedirs(os.path.dirname(sp_anchor), exist_ok=True)
		with open(sp_anchor, "w", encoding="ascii", newline="\n") as f:
			f.write("../../../\n")
		print("[配置] 已写入 {}（相对路径指向便携包根目录）".format(os.path.basename(sp_anchor)), flush=True)

		print("[校验] import 关键依赖…", flush=True)
		subprocess.check_call(
			[
				py_exe,
				"-c",
				"import requests, yaml, curl_cffi, PIL, aiohttp, bs4, selenium",
			],
			cwd=embed_dir,
		)

		os.makedirs(out_root, exist_ok=True)
		py_dir = os.path.join(out_root, "python")
		if os.path.isdir(py_dir):
			_safe_remove_tree(py_dir)
		print("[部署] 复制 embed Python 到 {} …".format(py_dir), flush=True)
		shutil.copytree(embed_dir, py_dir)

	_copy_project(out_root)
	remove_pycache_trees(out_root)
	_verify_portable_parity(out_root)

	if make_zip:
		base = os.path.dirname(out_root)
		name = os.path.basename(out_root.rstrip(os.sep))
		arch = os.path.join(base, name + ".zip")
		if os.path.isfile(arch):
			os.remove(arch)
		print("[打包] {}".format(arch), flush=True)
		shutil.make_archive(os.path.join(base, name), "zip", base, name)
		print("[完成] 目录: {}\n[完成] 压缩包: {}".format(out_root, arch), flush=True)
	else:
		print("[完成] 目录: {}".format(out_root), flush=True)


def main() -> None:
	ap = argparse.ArgumentParser(description="Build ProxySoru Windows portable folder.")
	ap.add_argument(
		"--out",
		default=os.path.join(ROOT_DIR, "dist", PORTABLE_DIR_NAME),
		help="输出目录（将先删除再重建）",
	)
	ap.add_argument("--no-zip", action="store_true", help="不生成 zip 压缩包")
	args = ap.parse_args()
	out_abs = os.path.abspath(args.out)
	build(out_abs, make_zip=not args.no_zip)


if __name__ == "__main__":
	main()
