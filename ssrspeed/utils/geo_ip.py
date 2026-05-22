#coding:utf-8

import os
import sys
import time
import re
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

logger = logging.getLogger("Sub")

from config import config
LOCAL_PORT = config["localPort"]
_PERF = config.get("performance", {}) or {}
HTTP_TIMEOUT = int(_PERF.get("http_timeout", 6))
_GEO_PARALLEL = bool(_PERF.get("geo_parallel", True))
_GEO_CACHE = {}
_DOMAIN_IP_CACHE = {}


def _build_proxies():
	return {
		"http": "socks5h://127.0.0.1:%d" % LOCAL_PORT,
		"https": "socks5h://127.0.0.1:%d" % LOCAL_PORT
	}


def _normalize_geo(data: dict, source: str):
	if not isinstance(data, dict):
		return {}
	if source == "ip.sb":
		return {
			"ip": data.get("ip", "N/A"),
			"country": data.get("country", "N/A"),
			"country_code": data.get("country_code", "N/A"),
			"city": data.get("city", "Unknown City"),
			"organization": data.get("organization", "N/A"),
			"asn": data.get("asn", "N/A"),
		}
	if source == "ipwho.is":
		conn = data.get("connection", {}) if isinstance(data.get("connection", {}), dict) else {}
		return {
			"ip": data.get("ip", "N/A"),
			"country": data.get("country", "N/A"),
			"country_code": data.get("country_code", "N/A"),
			"city": data.get("city", "Unknown City"),
			"organization": conn.get("org", data.get("org", "N/A")),
			"asn": conn.get("asn", "N/A"),
		}
	if source == "ip-api":
		asn_raw = data.get("as", "N/A")
		asn = asn_raw.split(" ")[0] if isinstance(asn_raw, str) and asn_raw else "N/A"
		return {
			"ip": data.get("query", "N/A"),
			"country": data.get("country", "N/A"),
			"country_code": data.get("countryCode", "N/A"),
			"city": data.get("city", "Unknown City"),
			"organization": data.get("org", data.get("isp", "N/A")),
			"asn": asn,
		}
	if source == "ipinfo":
		country_code = data.get("country", "N/A")
		return {
			"ip": data.get("ip", "N/A"),
			"country": country_code,
			"country_code": country_code,
			"city": data.get("city", "Unknown City"),
			"organization": data.get("org", "N/A"),
			"asn": "N/A",
		}
	return {}


def _geo_source_list(ip_path: str):
	sources = [
		{"name": "ip.sb", "url": "https://api.ip.sb/geoip/{}".format(ip_path), "use_proxy": True},
		{"name": "ipwho.is", "url": "https://ipwho.is/{}".format(ip_path), "use_proxy": True},
		{"name": "ip-api", "url": "http://ip-api.com/json/{}?lang=en".format(ip_path), "use_proxy": True},
		{"name": "ipinfo", "url": "https://ipinfo.io/{}/json".format(ip_path), "use_proxy": True},
	]
	if ip_path:
		sources.extend(
			[
				{"name": "ip-api-direct", "url": "http://ip-api.com/json/{}?lang=en".format(ip_path), "use_proxy": False},
				{"name": "ipwho.is-direct", "url": "https://ipwho.is/{}".format(ip_path), "use_proxy": False},
			]
		)
	return sources


def _fetch_geo_one_source(source: dict, headers: dict):
	source_name = source["name"]
	source_url = source["url"]
	proxies = _build_proxies() if source["use_proxy"] else None
	try:
		rep = requests.get(
			source_url,
			proxies=proxies,
			timeout=HTTP_TIMEOUT,
			headers=headers,
		)
		data = rep.json()
		normalize_source = "ipwho.is" if source_name.startswith("ipwho.is") else (
			"ip-api" if source_name.startswith("ip-api") else (
				"ipinfo" if source_name.startswith("ipinfo") else "ip.sb"
			)
		)
		normalized = _normalize_geo(data, normalize_source)
		if normalized and normalized.get("ip", "N/A") != "N/A":
			return source_name, normalized, None
		return source_name, None, None
	except Exception as e:
		return source_name, None, e


def _fetch_geo_from_sources(ip=""):
	ip_path = ip if ip else ""
	# 仅缓存固定 IP（节点 server 解析结果）；出口 IP 每节点不同，不可跨节点复用。
	if ip_path and ip_path in _GEO_CACHE:
		return _GEO_CACHE[ip_path]

	headers = {
		"Accept": "application/json,text/plain,*/*",
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
	}
	sources = _geo_source_list(ip_path)
	last_err = None

	if _GEO_PARALLEL and len(sources) > 1:
		with ThreadPoolExecutor(max_workers=min(6, len(sources))) as ex:
			futs = [ex.submit(_fetch_geo_one_source, s, headers) for s in sources]
			for fut in as_completed(futs):
				source_name, normalized, err = fut.result()
				if err:
					last_err = err
					logger.warning("GeoIP source failed: %s (%s)", source_name, err.__class__.__name__)
					continue
				if normalized:
					logger.info("GeoIP source selected: %s", source_name)
					if ip_path:
						_GEO_CACHE[ip_path] = normalized
					return normalized
	else:
		for source in sources:
			source_name, normalized, err = _fetch_geo_one_source(source, headers)
			if err:
				last_err = err
				logger.warning("GeoIP source failed: %s (%s)", source_name, err.__class__.__name__)
				continue
			if normalized:
				logger.info("GeoIP source selected: %s", source_name)
				if ip_path:
					_GEO_CACHE[ip_path] = normalized
				return normalized

	if last_err:
		logger.error("All GeoIP sources failed: %s", str(last_err))
	if ip_path:
		_GEO_CACHE[ip_path] = {}
	return {}

def parseLocation():
	try:
		logger.info("Starting parse location.")
		tmp = _fetch_geo_from_sources("")
		if not tmp:
			return(False,"DEFAULT","DEFAULT","DEFAULT")
		logger.info("Server Country Code : %s,Continent Code : %s,ISP : %s" % (
			tmp.get("country_code", "N/A"),
			tmp.get("continent_code", "DEFAULT"),
			tmp.get("organization", "N/A")
		))
		return (True,tmp.get("country_code", "N/A"),tmp.get("continent_code", "DEFAULT"),tmp.get("organization", "N/A"))
	except requests.exceptions.ReadTimeout:
		logger.error("Parse location timeout.")
	except:
		logger.exception("Parse location failed.")
		try:
			logger.error(rep.content)
		except:
			pass
	return(False,"DEFAULT","DEFAULT","DEFAULT")

def checkIPv4(ip):
	r = re.compile(r"\b((?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?:(?<!\.)\b|\.)){4}")
	rm = r.match(ip)
	if (rm):
		if (rm.group(0) == ip):
			return True
	return False

def domain2ip(domain):
	if domain in _DOMAIN_IP_CACHE:
		return _DOMAIN_IP_CACHE[domain]
	logger.info("Translating {} to ipv4.".format(domain))
	if (checkIPv4(domain)):
		_DOMAIN_IP_CACHE[domain] = domain
		return domain
	try:
		from .dns_resolve import resolve_ipv4_with_fallback
		resolved = resolve_ipv4_with_fallback(domain)
		_DOMAIN_IP_CACHE[domain] = resolved
		return resolved
	except Exception:
		logger.exception("Translate {} to ipv4 failed.".format(domain))
		_DOMAIN_IP_CACHE[domain] = "N/A"
		return "N/A"


def IPLoc(ip = ""):
	try:
		if (ip != "" and not checkIPv4(ip)):
			logger.error("Invalid IP : {}".format(ip))
			return {}
		logger.info("Starting Geo IP.")
		if (ip == "N/A"):
			ip = ""
		tmp = _fetch_geo_from_sources(ip)
		return tmp if tmp else {}
	except requests.exceptions.ReadTimeout:
		logger.error("Geo IP Timeout.")
		return {}
	except:
		logger.exception("Geo IP Failed.")
		try:
			logger.error(rep.content)
		except:
			pass
	return {}

