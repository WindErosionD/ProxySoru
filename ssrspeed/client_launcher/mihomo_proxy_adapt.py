# coding: utf-8
"""将 SS/SSR/V2Ray JSON/Trojan JSON/Clash 式节点配置转为 Mihomo 单条 proxy 字典。"""

import copy
import logging
from typing import Any, Dict

logger = logging.getLogger("Sub")

_MIHOMO_NATIVE_TYPES = frozenset(
	{
		"vless",
		"reality",
		"hysteria",
		"hysteria2",
		"hy",
		"hy2",
		"anytls",
		"tuic",
		"socks5",
		"wireguard",
		"snell",
		"http",
		"ss",
		"ssr",
		"vmess",
		"trojan",
	}
)


def _proxy_name(cfg: dict) -> str:
	name = cfg.get("name") or cfg.get("remarks") or cfg.get("server")
	if not name or name == "N/A":
		return "SSRSpeedNode"
	return str(name)[:128]


def _ensure_port(proxy: dict, cfg: dict) -> None:
	if "port" not in proxy or proxy["port"] in (None, 0):
		p = cfg.get("port") or cfg.get("server_port")
		if p is not None:
			proxy["port"] = int(p)


def _parse_ss_plugin_opts(plugin: str, plugin_opts: str) -> Dict[str, Any]:
	opts: Dict[str, Any] = {}
	if not plugin_opts:
		return opts
	for part in plugin_opts.split(";"):
		part = part.strip()
		if not part or "=" not in part:
			continue
		k, _, v = part.partition("=")
		k, v = k.strip(), v.strip()
		if k == "obfs":
			opts["mode"] = v
		elif k in ("obfs-host", "host"):
			opts["host"] = v
		elif k == "mode":
			opts["mode"] = v
	return opts


def _normalize_ss_plugin_for_mihomo(plugin: str) -> str:
	"""Mihomo/Clash Meta 使用 plugin: obfs，而非 simple-obfs / obfs-local。"""
	p = (plugin or "").strip().lower()
	if p in ("simple-obfs", "obfs-local"):
		return "obfs"
	return (plugin or "").strip()


def _shadowsocks_to_proxy(cfg: dict) -> dict:
	plugin = (cfg.get("plugin") or "").strip()
	if plugin == "v2ray-plugin":
		raise ValueError("v2ray-plugin 节点请改用 Mihomo/Clash 订阅或手动转换，当前未适配。")
	name = _proxy_name(cfg)
	out = {
		"name": name,
		"type": "ss",
		"server": cfg["server"],
		"port": int(cfg["server_port"]),
		"cipher": cfg["method"],
		"password": str(cfg["password"]),
		"udp": True,
	}
	if plugin:
		out["plugin"] = _normalize_ss_plugin_for_mihomo(plugin)
		po = _parse_ss_plugin_opts(plugin, cfg.get("plugin_opts") or "")
		if po:
			out["plugin-opts"] = po
	return out


def _shadowsocksr_to_proxy(cfg: dict) -> dict:
	name = _proxy_name(cfg)
	return {
		"name": name,
		"type": "ssr",
		"server": cfg["server"],
		"port": int(cfg["server_port"]),
		"cipher": cfg["method"],
		"password": str(cfg["password"]),
		"obfs": cfg.get("obfs") or "plain",
		"protocol": cfg.get("protocol") or "origin",
		"obfs-param": str(cfg.get("obfs_param") or cfg.get("obfsparam") or ""),
		"protocol-param": str(cfg.get("protocol_param") or cfg.get("protocolparam") or ""),
		"udp": True,
	}


def _trojan_json_to_proxy(cfg: dict) -> dict:
	name = _proxy_name(cfg)
	pw = cfg.get("password")
	if isinstance(pw, list) and pw:
		pw = pw[0]
	else:
		pw = str(pw or "")
	ssl = cfg.get("ssl") or {}
	sni = str(ssl.get("sni") or "")
	v = ssl.get("verify", "true")
	if isinstance(v, bool):
		skip_cert = bool(v)
	else:
		skip_cert = str(v).lower() not in ("true", "1", "yes")
	out = {
		"name": name,
		"type": "trojan",
		"server": cfg["remote_addr"],
		"port": int(cfg["remote_port"]),
		"password": pw,
		"sni": sni,
		"skip-cert-verify": skip_cert,
		"udp": True,
	}
	ws = cfg.get("websocket") or {}
	if str(ws.get("enabled", "false")).lower() == "true":
		out["network"] = "ws"
		out["ws-opts"] = {
			"path": str(ws.get("path") or "/"),
			"headers": {"Host": str(ws.get("host") or sni or "")},
		}
	return out


def _v2ray_json_to_vmess(cfg: dict) -> dict:
	outs = cfg.get("outbounds") or []
	if not outs:
		raise ValueError("V2Ray 配置缺少 outbounds")
	ob = outs[0]
	proto = ob.get("protocol")
	if proto != "vmess":
		raise ValueError("仅支持 outbound 为 vmess 的 V2Ray 配置，当前为: %s" % proto)
	vnext = (ob.get("settings") or {}).get("vnext") or []
	if not vnext:
		raise ValueError("V2Ray vmess 缺少 vnext")
	vn = vnext[0]
	users = vn.get("users") or []
	if not users:
		raise ValueError("V2Ray vmess 缺少 users")
	u = users[0]
	ssettings = ob.get("streamSettings") or {}
	network = ssettings.get("network") or "tcp"
	name = _proxy_name(cfg)
	out: Dict[str, Any] = {
		"name": name,
		"type": "vmess",
		"server": vn["address"],
		"port": int(vn["port"]),
		"uuid": u["id"],
		"alterId": int(u.get("alterId", 0)),
		"cipher": u.get("security") or "auto",
		"udp": True,
		"network": network,
	}
	sec = ssettings.get("security") or ""
	if sec == "tls":
		tls = ssettings.get("tlsSettings") or {}
		out["tls"] = True
		out["servername"] = str(tls.get("serverName") or "")
		out["skip-cert-verify"] = bool(tls.get("allowInsecure", False))

	if network == "ws":
		ws = ssettings.get("wsSettings") or {}
		headers = dict(ws.get("headers") or {})
		out["ws-opts"] = {"path": str(ws.get("path") or "/"), "headers": headers}
	elif network == "tcp":
		tcp = ssettings.get("tcpSettings") or {}
		hdr = tcp.get("header") or {}
		if hdr.get("type") == "http":
			req = hdr.get("request") or {}
			paths = req.get("path") or ["/"]
			if isinstance(paths, str):
				paths = [paths]
			hh = {}
			for k, v in (req.get("headers") or {}).items():
				if isinstance(v, list) and v:
					hh[k] = v[0] if len(v) == 1 else v
				else:
					hh[k] = v
			out["http-opts"] = {
				"method": str(req.get("method") or "GET"),
				"path": paths,
				"headers": hh,
			}
	elif network == "h2":
		h2 = ssettings.get("httpSettings") or {}
		host = h2.get("host")
		if isinstance(host, str):
			host = [host]
		out["h2-opts"] = {"path": str(h2.get("path") or "/"), "host": host or []}
	elif network == "grpc":
		gr = ssettings.get("grpcSettings") or {}
		out["grpc-opts"] = {
			"grpc-service-name": str(gr.get("serviceName") or ""),
		}
	elif network == "quic":
		q = ssettings.get("quicSettings") or {}
		out["quic-opts"] = {
			"key": str(q.get("key") or ""),
			"security": str(q.get("security") or "none"),
			"type": str((q.get("header") or {}).get("type") or "none"),
		}
	elif network == "kcp":
		kc = ssettings.get("kcpSettings") or {}
		out["network"] = "kcp"
		hdr = kc.get("header") or {}
		kopts: Dict[str, Any] = {"header": str(hdr.get("type") or "none")}
		if kc.get("seed"):
			kopts["seed"] = str(kc["seed"])
		out["kcp-opts"] = kopts
	return out


def _native_mihomo_strip(cfg: dict) -> dict:
	out = copy.deepcopy(cfg)
	out.pop("local_address", None)
	out.pop("local_port", None)
	out.pop("remarks", None)
	out.pop("group", None)
	if "name" not in out or not out["name"]:
		out["name"] = _proxy_name(cfg)
	_ensure_port(out, cfg)
	return out


def _is_shadowsocks_style(cfg: dict) -> bool:
	return (
		all(k in cfg for k in ("server", "server_port", "method", "password"))
		and "outbounds" not in cfg
		and cfg.get("run_type") != "client"
	)


def _is_ssr_style(cfg: dict) -> bool:
	if not _is_shadowsocks_style(cfg):
		return False
	if (cfg.get("protocol") or "") != "" or (cfg.get("obfs") or "") != "":
		return True
	return bool(cfg.get("protocol_param") or cfg.get("obfs_param"))


def config_summary_for_log(cfg: dict) -> str:
	"""脱敏后的节点关键字段，仅用于日志。"""
	try:
		frag = {
			"type": cfg.get("type"),
			"server": cfg.get("server"),
			"server_port": cfg.get("server_port"),
			"port": cfg.get("port"),
			"remarks": cfg.get("remarks"),
			"name": cfg.get("name"),
			"run_type": cfg.get("run_type"),
			"has_outbounds": bool(cfg.get("outbounds")),
			"protocol": cfg.get("protocol"),
			"obfs": cfg.get("obfs"),
			"method": cfg.get("method"),
			"plugin": cfg.get("plugin"),
		}
		return repr({k: v for k, v in frag.items() if v not in (None, "", False)})
	except Exception:
		return "<unprintable>"


def config_to_mihomo_proxy(cfg: dict) -> dict:
	"""统一入口：返回可写入 Mihomo proxies 列表的单条 dict。"""
	if not isinstance(cfg, dict):
		raise TypeError("config must be dict")

	if "outbounds" in cfg and isinstance(cfg.get("outbounds"), list):
		return _v2ray_json_to_vmess(cfg)

	if cfg.get("run_type") == "client" and "remote_addr" in cfg:
		return _trojan_json_to_proxy(cfg)

	t = (cfg.get("type") or "").lower()
	if t in _MIHOMO_NATIVE_TYPES and "server" in cfg:
		out = _native_mihomo_strip(cfg)
		if t == "ss" and out.get("plugin"):
			out["plugin"] = _normalize_ss_plugin_for_mihomo(str(out["plugin"]))
		return out

	if _is_ssr_style(cfg):
		return _shadowsocksr_to_proxy(cfg)

	if _is_shadowsocks_style(cfg):
		return _shadowsocks_to_proxy(cfg)

	raise ValueError("无法识别节点配置类型，无法交给 Mihomo 运行。")
