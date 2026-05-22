# -*- coding: utf-8 -*-

from copy import deepcopy
import logging

from config import config

logger = logging.getLogger("Sub")

class DownloadRuleMatch:
	def __init__(self):
		self._config = deepcopy(config["fileDownload"])
		self._download_links = deepcopy(self._config["downloadLinks"])
	
	def _get_download_link(self, tag: str = "") -> str:
		default = tuple()
		for link in self._download_links:
			if link["tag"] == "Default":
				default = (link["link"], link["fileSize"])
		if not tag:
			logger.info("No tag, using default.")
			return default
		for link in self._download_links:
			if(link["tag"] == tag):
				logger.info(f"Tag matched: {tag}")
				return (link["link"],link["fileSize"])
		logger.info(f"Tag {tag} not matched,using default.")
		return default
	
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
	

