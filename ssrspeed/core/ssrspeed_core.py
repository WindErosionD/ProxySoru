# coding:utf-8

import re
import time
import requests
import logging
import json
import threading
import socket
import sys
import os
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger("Sub")

from ..config_parser import UniversalParser

from ..result import ExportResult
from ..result import importResult
from ..result import Sorter

from ..speed_test import SpeedTest
from ..utils import check_platform
from ..utils.port_checker import check_port

from config import config

lsa = [19, 5, 23, 1, 11, 25, 15, 21, 3, 17, 9, 7]
lsn = [7, 3, 1, 9]
# 旧版 SSRSpeed 对部分订阅路径做 rot 解密；勿包含 token=（V2Board 等 ?token=hex 会被误解密）
domainls = ['/link/', '/sub/', '/1759/', '/v2/']

try:
	check_port(config["localPort"])
except (ConnectionRefusedError, TimeoutError, socket.timeout):
	pass
except OSError as exc:
	# Windows 上常见 WinError 10061；其余 OSError 仍抛出，避免吞掉真实故障
	if getattr(exc, "winerror", None) == 10061:
		pass
	else:
		raise
else:
	print(
		"Port {} already in use,".format(config["localPort"])
		+ " please change the local port in ssrspeed_config.json or terminate the application."
	)
	sys.exit(0)


def EX_GCD(a, b, arr):
	if b == 0:
		arr[0] = 1
		arr[1] = 0
		return a
	g = EX_GCD(b, a % b, arr)
	t = arr[0]
	arr[0] = arr[1]
	arr[1] = t - int(a / b) * arr[1]
	return g


def ModReverse(a, n):
	arr = [0, 1, ]
	gcd = EX_GCD(a, n, arr)
	if gcd == 1:
		return (arr[0] % n + n) % n
	else:
		return -1


def decrypt(sublink):
	for i in domainls:
		if i in sublink:
			origin = sublink[:sublink.find(i) + len(i)]
			key1 = sublink[sublink.find(i) + len(i):]
			key2 = ""
			ka = 0
			kn = 0
			flag = 1
			for j in range(0, len(key1)):
				o = key1[j]
				if (o.isupper() and flag):
					key2 += chr(ord("A") + ((ord(key1[j]) - ord("A")) * ModReverse(lsa[ka], 26)) % 26)
					ka = (ka + 1) % 12
				if (o.islower() and flag):
					key2 += chr(ord("a") + ((ord(key1[j]) - ord("a")) * ModReverse(lsa[ka], 26)) % 26)
					ka = (ka + 1) % 12
				if (o.isdigit() and flag):
					key2 += chr(ord("0") + ((ord(key1[j]) - ord("0")) * ModReverse(lsn[kn], 10)) % 10)
					kn = (kn + 1) % 4
				if (not o.isalnum()) or (not flag):
					flag = 0
					key2 += o

			return origin + key2
	return sublink


def subscription_url_needs_decrypt(url: str) -> bool:
	"""仅对旧式 rot 加密订阅链接触发 decrypt；标准 ?token= 十六进制面板链接除外。"""
	if not url:
		return False
	parsed = urlsplit(url.strip())
	q_token = (parse_qs(parsed.query).get("token") or [""])[0]
	if q_token and re.fullmatch(r"[0-9a-fA-F]{16,128}", q_token.strip()):
		return False
	if any(m in url for m in ("/link/", "/1759/", "/v2/")):
		return True
	return bool(re.search(r"/sub/[^/?#]+", url, flags=re.I))


class SSRSpeedCore(object):
	def __init__(self):

		self.testMethod = "ST_ASYNC"
		self.proxyType = "SSR"
		self.colors = "origin"
		self.sortMethod = "SPEED"
		self.testMode = "TCP_PING"

		self.__timeStampStart = -1
		self.__timeStampStop = -1
		self.__parser = UniversalParser()
		self.__stc = None
		self.__results = []
		self.__status = "stopped"
		self.enableTopology = False

	def set_group(self, group: str):
		self.__parser.set_group(group)

	# Console Methods
	def console_setup(self,
					  test_mode: str,
					  test_method: str,
					  color: str = "origin",
					  sort_method: str = "SPEED",
					  url: str = "",
					  cfg_filename: str = ""
					  ):
		self.testMethod = test_method
		self.testMode = test_mode
		self.sortMethod = sort_method or "SPEED"
		self.colors = color
		if url:
			# Match subscription fetch style: bare python-requests TLS/UA is often dropped mid-handshake.
			probe_headers = {
				"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
				"Accept": "*/*",
			}
			try:
				r = requests.get(url, headers=probe_headers, timeout=15)
				if len(r.content) < 200 and subscription_url_needs_decrypt(url):
					decoded = decrypt(url)
					if decoded and decoded != url:
						logger.info(
							"Subscription URL treated as rot-encoded (probe body %d bytes); using decoded link.",
							len(r.content),
						)
						url = decoded
			except requests.RequestException as e:
				logger.warning(
					"Subscription URL probe failed (%s: %s); parser will fetch with retries.",
					e.__class__.__name__,
					e,
				)
				# 探测超时/失败不代表链接是加密的，禁止 decrypt，否则会改坏 ?token= 明文订阅
		if self.__parser:
			if cfg_filename:
				self.__parser.read_gui_config(cfg_filename)
			elif url:
				self.__parser.read_subscription(url.split(" "))
			else:
				raise ValueError("Subscription URL or configuration file must be set !")

	def start_test(self, enable_topology=False):
		self.__timeStampStart = time.time()
		self.enableTopology = enable_topology
		self.__stc = SpeedTest(self.__parser, self.testMethod, enable_topology)
		self.__status = "running"
		if (self.testMode == "TCP_PING"):
			self.__stc.tcpingOnly()
		elif (self.testMode == "ALL"):
			self.__stc.fullTest()
		elif (self.testMode == "WEB_PAGE_SIMULATION"):
			self.__stc.webPageSimulation()
		self.__status = "stopped"
		self.__results = self.__stc.getResult()
		self.__timeStampStop = time.time()
		self.__exportResult()

	def clean_result(self):
		self.__results = []
		if (self.__stc):
			self.__stc.resetStatus()

	def get_results(self):
		return self.__results

	def filter_nodes(self, fk=[], fgk=[], frk=[], ek=[], egk=[], erk=[]):
		#	self.__parser.excludeNode([],[],config["excludeRemarks"])
		self.__parser.filter_nodes(fk, fgk, frk, ek, egk, erk + config["excludeRemarks"])
		self.__parser.print_nodes()
		logger.info("{} node(s) will be test.".format(len(self.__parser.nodes)))

	def import_and_export(self, filename, split=0):
		self.__results = importResult(filename)
		self.__exportResult(split, 2)
		self.__results = []

	def __exportResult(self, split=0, exportType=0):
		er = ExportResult()
		er.setTimeUsed(self.__timeStampStop - self.__timeStampStart)
		er.setTopologyEnabled(self.enableTopology)
		if self.testMode == "WEB_PAGE_SIMULATION":
			er.exportWpsResult(self.__results, exportType)
		else:
			er.setColors(self.colors)
			er.export(self.__results, split, exportType, self.sortMethod)



