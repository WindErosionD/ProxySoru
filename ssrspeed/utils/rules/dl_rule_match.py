# -*- coding: utf-8 -*-

from copy import deepcopy
import logging
import re

from config import config

logger = logging.getLogger("Sub")

# Cloudflare __down 超过约 100MB 会返回 HTTP 403（2026 年起常见）
_CF_DOWN_RE = re.compile(
	r"^(https://speed\.cloudflare\.com/__down\?bytes=)(\d+)(.*)$",
	re.I,
)
_CF_MAX_BYTES = 52_428_800  # 50 MiB


def normalize_download_url(link: str) -> str:
	"""将 Cloudflare 测速 URL 的 bytes 参数限制在可用上限内。"""
	link = (link or "").strip()
	m = _CF_DOWN_RE.match(link)
	if not m:
		return link
	n = int(m.group(2))
	if n <= _CF_MAX_BYTES:
		return link
	logger.info(
		"Cloudflare speed URL bytes=%d exceeds limit (%d); using %d.",
		n,
		_CF_MAX_BYTES,
		_CF_MAX_BYTES,
	)
	return "{}{}{}".format(m.group(1), _CF_MAX_BYTES, m.group(3))


class DownloadRuleMatch:
	def __init__(self):
		self._config = deepcopy(config["fileDownload"])
		self._download_links = deepcopy(self._config["downloadLinks"])
	
	def _link_pair(self, entry: dict) -> tuple:
		return (
			normalize_download_url(entry["link"]),
			entry["fileSize"],
		)

	def _get_download_link(self, tag: str = "") -> tuple:
		default = tuple()
		for link in self._download_links:
			if link["tag"] == "Default":
				default = self._link_pair(link)
		if not tag:
			logger.info("No tag, using default.")
			return default
		for link in self._download_links:
			if link["tag"] == tag:
				logger.info(f"Tag matched: {tag}")
				return self._link_pair(link)
		logger.info(f"Tag {tag} not matched,using default.")
		return default

	def get_fallback_links(self, primary: tuple = None) -> list:
		"""除主链接外，按配置顺序返回备用测速 URL（已 normalize）。"""
		seen = set()
		out = []
		if primary:
			seen.add(primary[0])
		for entry in self._download_links:
			pair = self._link_pair(entry)
			if pair[0] not in seen:
				seen.add(pair[0])
				out.append(pair)
		return out
	
	def _check_rule(self, data: dict):
		isp = str(data.get("organization", "N/A")).strip()
		country_code = str(data.get("country_code", "N/A")).strip()
		continent = str(data.get("continent_code", "")).strip()
		rules = self._config["rules"]
		for rule in rules:
			if rule["mode"].lower() == "match_isp":
				logger.debug("Match mode: ISP")
				if isp and isp != "N/A" and isp in str(rule.get("ISP", "")).strip():
					logger.info(f"ISP {isp} matched.")
					return self._get_download_link(rule["tag"])
			elif rule["mode"].lower() == "match_location":
				logger.debug("Match mode: Location")
				for code in rule.get("countries",[]): 
					if country_code == str(code).strip():
						logger.info(f"Country {code} matched.")
						return self._get_download_link(rule["tag"])
				rule_continent = str(rule.get("continent", "")).strip()
				if rule_continent and continent and rule_continent in continent:
					logger.info(f"Continent {continent} matched.")
					return self._get_download_link(rule["tag"])
		logger.info("Rule not matched, using default.")
		return self._get_download_link()

	def get_url(self, data: dict) -> str:
		try:

			if data and not self._config["skipRuleMatch"]:
				return self._check_rule(data)
			else:
				return self._get_download_link()
		except:
			logger.exception("\n")
			return self._get_download_link()
	

