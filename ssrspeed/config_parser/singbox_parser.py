# -*- coding: utf-8 -*-
"""Parse sing-box JSON subscription / outbound profiles into Mihomo-style node configs."""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Sub")

_PROXY_TYPES = frozenset(
	{
		"vless",
		"vmess",
		"trojan",
		"shadowsocks",
		"hysteria",
		"hysteria2",
		"tuic",
		"anytls",
		"socks",
		"wireguard",
	}
)

_TYPE_TO_MIHOMO = {
	"shadowsocks": "ss",
	"socks": "socks5",
}


def _bool(val: Any, default: bool = False) -> bool:
	if val is None:
		return default
	if isinstance(val, bool):
		return val
	return str(val).lower() in ("1", "true", "yes", "on")


def _name(ob: dict) -> str:
	return str(ob.get("tag") or ob.get("server") or "N/A")


def _port(ob: dict) -> int:
	p = ob.get("server_port")
	if p is None:
		p = ob.get("port")
	return int(p or 0)


def _apply_tls(ob: dict, cfg: dict) -> None:
	tls = ob.get("tls")
	if not isinstance(tls, dict) or not _bool(tls.get("enabled")):
		return
	cfg["tls"] = True
	sni = tls.get("server_name") or tls.get("serverName")
	if sni:
		cfg["servername"] = str(sni)
	if _bool(tls.get("insecure")):
		cfg["skip-cert-verify"] = True
	reality = tls.get("reality")
	if isinstance(reality, dict) and _bool(reality.get("enabled")):
		pbk = reality.get("public_key") or reality.get("public-key") or ""
		sid = reality.get("short_id") or reality.get("short-id") or ""
		cfg["reality-opts"] = {"public-key": str(pbk), "short-id": str(sid)}
		utls = tls.get("utls")
		if isinstance(utls, dict):
			fp = utls.get("fingerprint")
			if fp:
				cfg["client-fingerprint"] = str(fp)
		alpn = tls.get("alpn")
		if alpn:
			cfg["alpn"] = alpn if isinstance(alpn, list) else [str(alpn)]


def _apply_transport(ob: dict, cfg: dict) -> None:
	transport = ob.get("transport")
	if not isinstance(transport, dict):
		cfg.setdefault("network", "tcp")
		return
	net = str(transport.get("type") or "tcp")
	cfg["network"] = net
	if net == "ws":
		headers = dict(transport.get("headers") or {})
		host = transport.get("host")
		if host and "Host" not in headers:
			headers["Host"] = host if isinstance(host, str) else str(host)
		cfg["ws-opts"] = {
			"path": str(transport.get("path") or "/"),
			"headers": headers,
		}
	elif net == "grpc":
		cfg["grpc-opts"] = {
			"grpc-service-name": str(transport.get("service_name") or transport.get("service-name") or ""),
		}
	elif net == "http":
		cfg["network"] = "h2"
		host = transport.get("host")
		if isinstance(host, str):
			host = [host]
		cfg["h2-opts"] = {
			"path": str(transport.get("path") or "/"),
			"host": host or [],
		}


def singbox_outbound_to_mihomo(ob: dict) -> Optional[dict]:
	"""Convert one sing-box outbound dict to Mihomo proxy dict; None if skipped."""
	if not isinstance(ob, dict):
		return None
	raw_type = str(ob.get("type") or "").lower()
	if raw_type not in _PROXY_TYPES:
		return None
	mihomo_type = _TYPE_TO_MIHOMO.get(raw_type, raw_type)
	name = _name(ob)
	server = ob.get("server")
	port = _port(ob)
	if not server or not port:
		logger.debug("Skip sing-box outbound %r: missing server/port.", name)
		return None

	cfg: Dict[str, Any] = {
		"type": mihomo_type,
		"name": name,
		"remarks": name,
		"group": "N/A",
		"server": str(server),
		"server_port": port,
		"port": port,
		"udp": _bool(ob.get("udp"), True),
	}

	if raw_type == "vless":
		cfg["uuid"] = str(ob.get("uuid") or "")
		flow = ob.get("flow")
		if flow:
			cfg["flow"] = str(flow)
		_apply_transport(ob, cfg)
		_apply_tls(ob, cfg)
	elif raw_type == "vmess":
		cfg["uuid"] = str(ob.get("uuid") or "")
		cfg["alterId"] = int(ob.get("alter_id") or ob.get("alterId") or 0)
		cfg["cipher"] = str(ob.get("security") or "auto")
		_apply_transport(ob, cfg)
		_apply_tls(ob, cfg)
	elif raw_type == "trojan":
		cfg["password"] = str(ob.get("password") or "")
		_apply_transport(ob, cfg)
		_apply_tls(ob, cfg)
	elif raw_type == "shadowsocks":
		cfg["cipher"] = str(ob.get("method") or "")
		cfg["password"] = str(ob.get("password") or "")
	elif raw_type == "hysteria":
		cfg["auth-str"] = str(ob.get("auth_str") or ob.get("auth-str") or ob.get("password") or "")
		up = ob.get("up_mbps") or ob.get("up")
		down = ob.get("down_mbps") or ob.get("down")
		if up is not None:
			cfg["up"] = str(up)
		if down is not None:
			cfg["down"] = str(down)
		_apply_tls(ob, cfg)
	elif raw_type == "hysteria2":
		cfg["password"] = str(ob.get("password") or "")
		_apply_tls(ob, cfg)
	elif raw_type == "tuic":
		cfg["uuid"] = str(ob.get("uuid") or "")
		cfg["password"] = str(ob.get("password") or "")
		_apply_tls(ob, cfg)
	elif raw_type == "anytls":
		cfg["password"] = str(ob.get("password") or "")
		_apply_tls(ob, cfg)
	elif raw_type == "socks":
		cfg["username"] = str(ob.get("username") or "")
		cfg["password"] = str(ob.get("password") or "")
	elif raw_type == "wireguard":
		cfg["private-key"] = str(ob.get("private_key") or ob.get("private-key") or "")
		cfg["server"] = str(server)
		cfg["port"] = port
		peers = ob.get("peers")
		if isinstance(peers, list) and peers:
			peer = peers[0]
			if isinstance(peer, dict):
				cfg["public-key"] = str(peer.get("public_key") or peer.get("public-key") or "")
				cfg["allowed-ips"] = peer.get("allowed_ips") or peer.get("allowed-ips") or ["0.0.0.0/0"]
				preshared = peer.get("pre_shared_key") or peer.get("pre-shared-key")
				if preshared:
					cfg["pre-shared-key"] = str(preshared)
				reserved = peer.get("reserved")
				if reserved:
					cfg["reserved"] = reserved

	return cfg


def looks_like_singbox_subscription(obj: Any) -> bool:
	if not isinstance(obj, dict):
		return False
	outbounds = obj.get("outbounds")
	if not isinstance(outbounds, list) or not outbounds:
		return False
	for ob in outbounds:
		if isinstance(ob, dict) and str(ob.get("type") or "").lower() in _PROXY_TYPES:
			return True
	return False


def parse_singbox_subscription(text: str) -> List[dict]:
	"""Return Mihomo-style node dicts from sing-box JSON subscription text."""
	s = (text or "").strip()
	if not s or s[0] not in "{[":
		return []
	try:
		obj = json.loads(s)
	except (ValueError, json.JSONDecodeError):
		return []

	outbounds: List[dict] = []
	if isinstance(obj, dict):
		if not looks_like_singbox_subscription(obj):
			return []
		outbounds = obj.get("outbounds") or []
	elif isinstance(obj, list):
		outbounds = [x for x in obj if isinstance(x, dict)]
	else:
		return []

	result: List[dict] = []
	for ob in outbounds:
		cfg = singbox_outbound_to_mihomo(ob)
		if cfg:
			result.append(cfg)
	if result:
		logger.info("Sing-box subscription: parsed %d proxy node(s).", len(result))
	return result
