# -*- coding: utf-8 -*-

from .node_type_base import BaseNode


class NodeMihomo(BaseNode):
	def __init__(self, config: dict):
		super(NodeMihomo, self).__init__(config)
		self._type = "Mihomo"
