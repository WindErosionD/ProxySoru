# coding: utf-8
"""
安装 requirements.txt 的共用逻辑。

pynat 的 setup 在构建 wheel 时会 `from six import text_type`，但 PyPI 包未声明
build 依赖，pip 默认构建隔离环境里没有 six，导致「Failed to build pynat」。
"""
from __future__ import annotations

import subprocess
from typing import Callable, List, Optional, Sequence

from env_cache import cleanup_after_pip_install, pip_subprocess_env

DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
PYNAT_SPEC = "pynat>=0.7.0"
TOOLCHAIN = ("pip", "setuptools", "wheel", "six")


def _pip_cmd(python_exe: str, index: Optional[str], *args: str) -> List[str]:
	cmd = [python_exe, "-m", "pip", "install"]
	if index:
		cmd.extend(["-i", index])
	cmd.extend(args)
	return cmd


def upgrade_pip_toolchain(
	python_exe: str,
	index: Optional[str] = DEFAULT_INDEX,
	run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
	"""升级 pip / setuptools / wheel，并预装 six（供 pynat 等构建使用）。"""
	return run(_pip_cmd(python_exe, index, "--upgrade", *TOOLCHAIN))


def install_requirements_file(
	python_exe: str,
	req_path: str,
	index: Optional[str] = DEFAULT_INDEX,
	run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
	"""
	安装完整 requirements.txt。
	先确保工具链与 six，再 pip install -r；若仍失败则对 pynat 与整包使用 --no-build-isolation。
	"""
	upgrade_pip_toolchain(python_exe, index=index, run=run)

	last = run(_pip_cmd(python_exe, index, "-r", req_path))
	if last.returncode == 0:
		return last

	# pynat：setup.py 在隔离构建中 import six
	last = run(_pip_cmd(python_exe, index, PYNAT_SPEC, "--no-build-isolation"))
	if last.returncode != 0:
		return last

	last = run(_pip_cmd(python_exe, index, "-r", req_path))
	if last.returncode == 0:
		return last

	return run(_pip_cmd(python_exe, index, "-r", req_path, "--no-build-isolation"))


def install_requirements_check(
	python_exe: str,
	req_path: str,
	index: Optional[str] = DEFAULT_INDEX,
	cwd: Optional[str] = None,
) -> None:
	"""便携包构建等场景：失败则抛 CalledProcessError。"""

	def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
		return subprocess.run(cmd, cwd=cwd, check=False, env=pip_subprocess_env())

	last = install_requirements_file(python_exe, req_path, index=index, run=_run)
	cleanup_after_pip_install(python_exe, cwd=cwd)
	if last.returncode != 0:
		raise subprocess.CalledProcessError(
			last.returncode,
			getattr(last, "args", None) or _pip_cmd(python_exe, index, "-r", req_path),
		)
