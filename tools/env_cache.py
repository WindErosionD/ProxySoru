# coding: utf-8
"""环境准备（pip / venv）产生的缓存清理。"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional, Sequence


def pip_subprocess_env() -> dict:
	env = os.environ.copy()
	env["PIP_NO_CACHE_DIR"] = "1"
	env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
	return env


def pip_cache_purge(python_exe: str, cwd: Optional[str] = None) -> None:
	try:
		subprocess.run(
			[python_exe, "-m", "pip", "cache", "purge"],
			cwd=cwd,
			env=pip_subprocess_env(),
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
		)
	except OSError:
		pass


def remove_pycache_trees(*roots: str) -> None:
	for root in roots:
		if not root or not os.path.isdir(root):
			continue
		for dirpath, dirnames, _filenames in os.walk(root):
			if "__pycache__" not in dirnames:
				continue
			cache = os.path.join(dirpath, "__pycache__")
			shutil.rmtree(cache, ignore_errors=True)
			dirnames.remove("__pycache__")


def cleanup_after_pip_install(python_exe: str, *extra_roots: str, cwd: Optional[str] = None) -> None:
	pip_cache_purge(python_exe, cwd=cwd)
	remove_pycache_trees(*extra_roots)
