# -*- coding: utf-8 -*-

import re
from copy import deepcopy
import logging

logger = logging.getLogger("Sub")

# 排除信息节点/广告节点：匹配备注名（remarks/name）
_EXCLUDE_REMARK_REGEX = (
	re.compile(r"(流量|套餐|重置|到期|过期|官网|网址|地址|订阅)"),
	re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
	re.compile(r"\d+\s*天"),
	re.compile(r"[A-Za-z0-9-]+\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
)


def remark_matches_exclude_patterns(remark: str, extra_keywords=None) -> bool:
	"""备注命中任一内置正则，或包含 excludeRemarks 中的额外关键字时排除。"""
	text = str(remark or "")
	for pat in _EXCLUDE_REMARK_REGEX:
		if pat.search(text):
			return True
	for kw in extra_keywords or []:
		kw = str(kw or "").strip()
		if kw and kw in text:
			return True
	return False

class NodeFilter:
	def __init__(self):
		self.__node_list = []

	def filter_node(
		self,
		nodes: list,
		kwl:list = [],
		gkwl:list = [],
		rkwl:list = [],
		ekwl:list = [],
		egkwl:list = [],
		erkwl:list = []
	):
		self.__node_list.clear()
		self.__node_list = deepcopy(nodes)
		self.__filter_node(kwl, gkwl, rkwl)
		self.__exclude_nodes(ekwl, egkwl, erkwl)
		return self.__node_list

	def __check_in_list(self,item: dict,_list: list):
		for _item in _list:
			_item = _item.config
			server1 = item.get("server", "")
			server2 = _item.get("server", "")
			port1 = item.get("server_port", item.get("port", 0))
			port2 = _item.get("server_port", _item.get("port", 0))
			if server1 and server2 and port1 and port2:
				if server1 == server2 and port1 == port2:
					logger.warn("{} - {} ({}:{}) already in list.".format(
						item.get("group", "N/A"),
						item.get("remarks", "N/A"),
						item.get("server", "Server EMPTY"),
						item.get("server_port", item.get("port", 0))
					))
					return True
			else:
				return True
		return False

	def __group_text(self, config: dict) -> str:
		return str(config.get("group", "N/A"))

	def __remark_text(self, config: dict) -> str:
		return str(config.get("remarks", config.get("name", "")))

	def __filter_group(self, gkwl):
		_list = []
		if (gkwl == []):return
		for gkw in gkwl:
			for item in self.__node_list:
				config = item.config
				if self.__check_in_list(config, _list): continue
				if (gkw in self.__group_text(config)):
					_list.append(item)
		self.__node_list = _list

	def __filter_remark(self,rkwl):
		_list = []
		if (rkwl == []):return
		for rkw in rkwl:
			for item in self.__node_list:
				config = item.config
				if self.__check_in_list(config, _list): continue
				if (rkw in self.__remark_text(config)):
					_list.append(item)
		self.__node_list = _list

	def __filter_node(self,kwl = [],gkwl = [],rkwl = []):
		_list = []
	#	print(len(self.__node_list))
	#	print(type(kwl))
		if (kwl != []):
			for kw in kwl:
				for item in self.__node_list:
					config = item.config
					if self.__check_in_list(config, _list): continue
					if ((kw in self.__group_text(config)) or (kw in self.__remark_text(config))):
					#	print(item["remarks"])
						_list.append(item)
			self.__node_list = _list
		self.__filter_group(gkwl)
		self.__filter_remark(rkwl)

	def __exclude_group(self,gkwl):
		if (gkwl == []):return
		for gkw in gkwl:
			_list = []
			for item in self.__node_list:
				config = item.config
				if (gkw not in self.__group_text(config)):
					_list.append(item)
			self.__node_list = _list

	def __exclude_remark(self, rkwl):
		_list = []
		for item in self.__node_list:
			config = item.config
			remark = self.__remark_text(config)
			if remark_matches_exclude_patterns(remark, rkwl):
				logger.debug(
					"Excluded {} - {} (remark filter)".format(
						self.__group_text(config), remark
					)
				)
				continue
			_list.append(item)
		self.__node_list = _list

	def __exclude_nodes(self,kwl = [],gkwl = [],rkwl = []):

		if (kwl != []):
			for kw in kwl:
				_list = []
				for item in self.__node_list:
					config = item.config
					if ((kw not in self.__group_text(config)) and (kw not in self.__remark_text(config))):
						_list.append(item)
					else:
						logger.debug("Excluded {} - {}".format(self.__group_text(config), self.__remark_text(config)))
				self.__node_list = _list
		self.__exclude_group(gkwl)
		self.__exclude_remark(rkwl)

