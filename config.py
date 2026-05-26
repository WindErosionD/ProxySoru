#coding:utf-8

import os
import shutil
import json

_ROOT = os.path.dirname(os.path.abspath(__file__))
# 项目根目录（与当前工作目录无关）；客户端二进制、运行时 yaml/json 应锚定此路径，便携版/快捷方式启动时 cwd 可能不是仓库根。
PROJECT_ROOT = _ROOT

__version__ = "0.33"

config = {
	"VERSION": __version__,
}

LOADED = False

if not LOADED:
	_cfg = os.path.join(_ROOT, "ssrspeed_config.json")
	_cfg_ex = os.path.join(_ROOT, "ssrspeed_config.example.json")
	if os.path.exists(_cfg):
		if os.path.isdir(_cfg):
			shutil.rmtree(_cfg)
			if not os.path.exists(_cfg_ex):
				raise FileNotFoundError("Default configuraton file not found, please download from the official repo and try again.")
			shutil.copy(_cfg_ex, _cfg)
	else:
		if not os.path.exists(_cfg_ex):
			raise FileNotFoundError("Default configuraton file not found, please download from the official repo and try again.")
		shutil.copy(_cfg_ex, _cfg)

	with open(_cfg, "r", encoding = "utf-8") as f:
		try:
			file_config = json.load(f)
			config.update(file_config)
		finally:
			pass
	LOADED = True

