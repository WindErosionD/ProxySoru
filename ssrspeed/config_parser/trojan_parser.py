# -*- coding: utf-8 -*-

from urllib.parse import quote, unquote, parse_qsl
from base64 import urlsafe_b64decode, urlsafe_b64encode
import re
import logging

from config import config as ssr_config

logger = logging.getLogger("Sub")


from . import BaseParser


class TrojanParser(BaseParser):
	def __init__(self):
		super(TrojanParser, self).__init__()

	# From: https://github.com/NyanChanMeow/SSRSpeed/issues/105
	def _parseLink(self, link :str):
		if not link.startswith("trojan://"):
			logger.error("Unsupport link: {}".format(link))
			return None

		def percent_decode(s):
			try:
				s = unquote(s, encoding="gb2312", errors="strict")
			except:
				try:
					s = unquote(s, encoding="utf8", errors="strict")
				except:
					# error decoding
					# raise # warning is enough
					pass
			return s

		link = link[len("trojan://"):]
		if not link: return None

		result = {
			"run_type": "client",
			"local_addr": "127.0.0.1",
			"local_port": 10870,
			"remote_addr": "example.com",
			"remote_port": 443,
			"password": [
				"password1"
			],
			"log_level": 1,
			"ssl": {
				"verify": "true",
				"verify_hostname": "true",
				"cert": "",
				"cipher": "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES256-SHA:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA:AES128-SHA:AES256-SHA:DES-CBC3-SHA",
				"cipher_tls13": "TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384",
				"sni": "",
				"alpn": [
					"h2",
					"http/1.1"
				],
				"reuse_session": "true",
				"session_ticket": "false",
				"curves": ""
			},
			"tcp": {
				"no_delay": "true",
				"keep_alive": "true",
				"reuse_port": "false",
				"fast_open": "false",
				"fast_open_qlen": 20
			},
			"websocket": {
				"enabled": "false",
				"path": "",
				"host": ""
			},
			"group":"N/A"
		}

		link = percent_decode(link)
		if "#" in link:
			link, result["remarks"] = link.split("#")
		result["remarks"] = re.sub(r"\s|\n", "", result["remarks"])

		password = ""
		if "@" in link:
			password, link = link.split("@")
		result["password"][0] = password

		if "?" in link:
			host, link = link.split("?", 1)
		else:
			host = link
			link = ""
		result["server"], port_part = host.rsplit(":", 1)
		result["server_port"] = int(re.match(r"^\d+", port_part).group(0))

		result["remote_addr"] = result["server"]
		result["remote_port"] = result["server_port"]

		if link:
			link_args = {k.lower(): v for k, v in parse_qsl(link)}
			allow_insecure = link_args.get("allowinsecure", "0").lower() in ("1", "true", "yes")
			# ssl.verify=true 表示校验证书；allowInsecure=1 时不校验
			result["ssl"]["verify"] = "false" if allow_insecure else "true"
			sni = link_args.get("sni") or link_args.get("peer") or ""
			result["ssl"]["sni"] = sni
			result["tcp"]["fast_open"] = link_args.get("tfo", "") == "1"
			if link_args.get("peer") and link_args.get("peer") != "N/A":
				result["group"] = link_args.get("peer")
			# ws 协议 必传 host 与 path
			if link_args.get("type") == "ws" and link_args.get("host") and link_args.get("path"):
				result["websocket"]["enabled"] = "true"
				result["websocket"]["path"] = link_args["path"]
				result["websocket"]["host"] = link_args["host"]
		result["local_addr"] = ssr_config["localAddress"]
		result["local_port"] = int(ssr_config["localPort"])
		return result
