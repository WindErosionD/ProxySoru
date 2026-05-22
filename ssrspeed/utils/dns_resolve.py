# coding: utf-8
"""多源 DoH 解析 IPv4，直连、不经订阅代理，用于缓解 DNS 污染。

用于：订阅域名、节点 config['server'] 域名（inbound）。"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from config import config

logger = logging.getLogger("Sub")

_DOH_TEMPLATES = {
	"cloudflare": "https://cloudflare-dns.com/dns-query?name={name}&type=A",
	"google": "https://dns.google/resolve?name={name}&type=A",
	"quad9": "https://dns.quad9.net:5053/dns-query?name={name}&type=A",
}


def _dns_config() -> dict:
	return config.get("dns") or {}


def dns_enabled() -> bool:
	return bool(_dns_config().get("enabled", True))


def dns_subscription_enabled() -> bool:
	cfg = _dns_config()
	return dns_enabled() and bool(cfg.get("subscription", True))


def dns_inbound_enabled() -> bool:
	cfg = _dns_config()
	return dns_enabled() and bool(cfg.get("inbound", True))


def _doh_timeout() -> float:
	return float(_dns_config().get("doh_timeout_seconds", 8))


def _provider_order() -> List[str]:
	order = _dns_config().get("providers")
	if isinstance(order, list) and order:
		out = []
		for p in order:
			ps = str(p).lower().strip()
			if ps in _DOH_TEMPLATES and ps not in out:
				out.append(ps)
		return out or ["cloudflare", "google", "quad9"]
	return ["cloudflare", "google", "quad9"]


def _opener_direct() -> urllib.request.OpenerDirector:
	return urllib.request.build_opener(
		urllib.request.ProxyHandler({}),
		urllib.request.HTTPSHandler(),
	)


def _doh_query_one(provider: str, hostname: str, timeout: float) -> Tuple[str, List[str]]:
	"""返回 (provider, ipv4 列表，仅 A 记录)。"""
	url = _DOH_TEMPLATES.get(provider)
	if not url:
		return provider, []
	full = url.format(name=urllib.parse.quote(hostname, safe=""))
	req = urllib.request.Request(
		full,
		headers={"Accept": "application/dns-json"},
	)
	try:
		with _opener_direct().open(req, timeout=timeout) as resp:
			raw = resp.read().decode("utf-8", errors="replace")
		data = json.loads(raw)
	except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
		logger.debug("DoH %s failed for %s: %s", provider, hostname, e.__class__.__name__)
		return provider, []
	except Exception:
		logger.debug("DoH %s failed for %s", provider, hostname, exc_info=True)
		return provider, []

	if isinstance(data, dict):
		st = data.get("Status")
		if st is not None and int(st) != 0:
			return provider, []
		ips: List[str] = []
		for ans in data.get("Answer") or []:
			if not isinstance(ans, dict):
				continue
			if int(ans.get("type", 0)) != 1:
				continue
			d = ans.get("data")
			if isinstance(d, str) and _is_ipv4(d):
				ips.append(d)
		return provider, ips
	return provider, []


def _is_ipv4(s: str) -> bool:
	parts = s.split(".")
	if len(parts) != 4:
		return False
	try:
		return all(0 <= int(p) <= 255 for p in parts)
	except ValueError:
		return False


def resolve_ipv4_multi_doh(hostname: str) -> Optional[str]:
	"""并行查询多个 DoH，按一致性与优先级选出 IPv4；失败返回 None。"""
	if not hostname:
		return None
	if _is_ipv4(hostname):
		return hostname
	timeout = _doh_timeout()
	providers = _provider_order()
	if not _dns_config().get("doh_parallel", True):
		results: List[Tuple[str, List[str]]] = []
		for p in providers:
			results.append(_doh_query_one(p, hostname, timeout))
		return _pick_from_results(hostname, results, providers)

	results = []
	with ThreadPoolExecutor(max_workers=min(6, len(providers) or 1)) as ex:
		futs = {ex.submit(_doh_query_one, p, hostname, timeout): p for p in providers}
		for fut in as_completed(futs):
			try:
				results.append(fut.result())
			except Exception:
				logger.debug("DoH worker failed", exc_info=True)

	return _pick_from_results(hostname, results, providers)


def _pick_from_results(hostname: str, results: List[Tuple[str, List[str]]], provider_order: List[str]) -> Optional[str]:
	per_provider_first: List[Tuple[str, str]] = []
	all_ips: List[str] = []
	for prov, ips in results:
		if not ips:
			continue
		first = ips[0]
		per_provider_first.append((prov, first))
		all_ips.extend(ips)
	if not all_ips:
		return None
	cnt = Counter(all_ips)
	best_ip, best_n = cnt.most_common(1)[0]
	if best_n >= 2:
		logger.info("DoH consensus %s -> %s (%d matches)", hostname, best_ip, best_n)
		return best_ip
	# 无多数：按提供商顺序取第一个有结果的
	rank = {p: i for i, p in enumerate(provider_order)}
	per_provider_first.sort(key=lambda x: rank.get(x[0], 99))
	chosen = per_provider_first[0][1]
	logger.info(
		"DoH picked %s -> %s (provider %s, no strict consensus)",
		hostname,
		chosen,
		per_provider_first[0][0],
	)
	return chosen


def resolve_ipv4_best(hostname: str) -> Optional[str]:
	"""对外主入口：DoH 多源 -> 失败则 None（由调用方决定是否回退系统 DNS）。"""
	if not hostname:
		return None
	hostname = hostname.strip().rstrip(".")
	if not hostname:
		return None
	if _is_ipv4(hostname):
		return hostname
	if not dns_enabled():
		return None
	try:
		ip = resolve_ipv4_multi_doh(hostname)
		if ip:
			return ip
	except Exception:
		logger.warning("DoH resolve chain failed for %s", hostname, exc_info=True)
	return None


def resolve_ipv4_with_fallback(hostname: str) -> str:
	"""DoH + 系统 gethostbyname，用于 inbound 展示等。"""
	if not hostname:
		return "N/A"
	if _is_ipv4(hostname.strip()):
		return hostname.strip()
	if dns_inbound_enabled():
		ip = resolve_ipv4_best(hostname.strip())
		if ip:
			return ip
	try:
		return socket.gethostbyname(hostname.strip())
	except OSError:
		logger.warning("System DNS failed for %s", hostname)
		return "N/A"


def subscription_resolve_entry(url: str) -> Optional[str]:
	"""若启用订阅侧 DNS，返回 curl --resolve 所需的 'host:port:ip'，否则 None。"""
	if not dns_subscription_enabled():
		return None
	try:
		p = urllib.parse.urlsplit(url)
	except Exception:
		return None
	host = (p.hostname or "").strip()
	if not host or _is_ipv4(host):
		return None
	ip = resolve_ipv4_best(host)
	if not ip:
		return None
	if p.scheme == "https":
		port = p.port or 443
	elif p.scheme == "http":
		port = p.port or 80
	else:
		return None
	return f"{host}:{port}:{ip}"
