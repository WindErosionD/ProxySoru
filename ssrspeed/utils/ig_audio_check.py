# coding: utf-8
"""
检测「Instagram 带版权音乐 / 授权音频」是否可用（与仅打开 instagram.com 不同）。

经 SOCKS 访问官网取 csrftoken，再请求 GraphQL 取公开帖元数据中的 should_mute_audio。
Instagram 会轮换 doc_id / 端点：优先使用网页仍在用的 graphql/query GET（与 yt-dlp 等一致），
失败时再尝试 api/graphql + PolarisPostRootQuery，最后回退旧 PolarisPostActionLoadPostQueryQuery。

返回 True=可播授权音乐, False=应静音/地区无授权, None=无法完成检测（网络、风控 HTML、接口变更等）。
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("Sub")

try:
	from curl_cffi import requests as _http

	_CFFI = True
except Exception:
	# 缺依赖、缺 VC++ 运行库、或 DLL 初始化失败时，curl_cffi 可能抛出非 ImportError；须降级为 requests
	import requests as _http

	_CFFI = False

GRAPHQL_POST = "https://www.instagram.com/api/graphql"
GRAPHQL_GET = "https://www.instagram.com/graphql/query/"
ORIGIN = "https://www.instagram.com"

# 公开帖 shortcode（社区常用探测帖；多试几个以防单个失效）
PROBE_SHORTCODES = (
	"C2YEAdOh9AB",
	"Cx_DE0ZI1xc",
	"CyERUKpIS7Q",
)

# yt-dlp / instagrapi 等仍在用的短帖查询（GET graphql/query）
DOC_ID_SHORTCODE_MEDIA = "8845758582119845"

# 网页 bundle 中的 PolarisPostRootQuery（POST api/graphql）
DOC_ID_POST_ROOT = "26544629655158927"

# 历史兼容：旧 PolarisPostActionLoadPostQueryQuery
DOC_ID_POST_ACTION_LEGACY = "10015901848480474"

_VARS_LEGACY = (
	'{"shortcode":"%s","fetch_comment_count":40,'
	'"fetch_related_profile_media_count":3,"parent_comment_count":24,'
	'"child_comment_count":3,"fetch_like_count":10,"fetch_tagged_user_count":null,'
	'"fetch_preview_comment_count":2,"has_threaded_comments":true,'
	'"hoisted_comment_id":null,"hoisted_reply_id":null}'
)


def _proxies(local_port: int):
	p = "socks5h://127.0.0.1:{}".format(local_port)
	return {"http": p, "https": p}


def _decode_graphql_body(text: str) -> Optional[dict]:
	if not text or not text.strip():
		return None
	s = text.strip()
	if s.startswith("for (;;);"):
		s = s[9:].strip()
	if s.startswith("<!") or s.startswith("<html"):
		return None
	try:
		return json.loads(s)
	except Exception:
		return None


def _mute_from_media_tree(obj: Any) -> Optional[bool]:
	"""在 xdt_shortcode_media / shortcode_media 子树中查找 should_mute_audio。"""
	if isinstance(obj, dict):
		if "should_mute_audio" in obj and isinstance(obj["should_mute_audio"], bool):
			return obj["should_mute_audio"]
		for v in obj.values():
			r = _mute_from_media_tree(v)
			if r is not None:
				return r
	elif isinstance(obj, list):
		for v in obj[:300]:
			r = _mute_from_media_tree(v)
			if r is not None:
				return r
	return None


def _mute_from_response_payload(data: dict) -> Optional[bool]:
	if not isinstance(data, dict):
		return None
	# 标准 GraphQL: data.xdt_shortcode_media
	root = data.get("data")
	if isinstance(root, dict):
		media = root.get("xdt_shortcode_media") or root.get("shortcode_media")
		if media is None:
			return None
		if not isinstance(media, dict):
			return None
		return _mute_from_media_tree(media)
	return None


def _mute_from_text_regex(text: str) -> Optional[bool]:
	"""兼容非标准 JSON 文本中的布尔。"""
	m = re.search(r'"should_mute_audio"\s*:\s*(true|false|null)', text, re.I)
	if not m:
		return None
	val = m.group(1).lower()
	if val == "null":
		return None
	return val == "true"


def _session():
	if _CFFI:
		for imp in ("chrome131", "chrome124", "chrome120", "chrome110"):
			try:
				return _http.Session(impersonate=imp)
			except Exception:
				continue
	return _http.Session()


def _graphql_get_shortcode_media(sess, proxies: dict, headers: dict, shortcode: str, timeout: float) -> Optional[dict]:
	variables = {
		"shortcode": shortcode,
		"child_comment_count": 3,
		"fetch_comment_count": 40,
		"parent_comment_count": 24,
		"has_threaded_comments": True,
	}
	params = {
		"doc_id": DOC_ID_SHORTCODE_MEDIA,
		"variables": json.dumps(variables, separators=(",", ":")),
	}
	try:
		r = sess.get(
			GRAPHQL_GET,
			params=params,
			headers=headers,
			proxies=proxies,
			timeout=timeout,
		)
	except Exception as exc:
		logger.debug("IG audio probe: GET graphql/query %s: %s", shortcode, exc)
		return None
	return _decode_graphql_body(r.text or "")


def _graphql_post_api(
	sess,
	proxies: dict,
	headers: dict,
	shortcode: str,
	timeout: float,
	*,
	doc_id: str,
	friendly_name: str,
	variables_obj: dict,
	legacy_string: Optional[str] = None,
) -> Optional[dict]:
	if legacy_string is not None:
		variables_str = legacy_string % (shortcode,)
	else:
		variables_str = json.dumps(variables_obj, separators=(",", ":"))
	form = {
		"variables": variables_str,
		"doc_id": doc_id,
		"fb_api_req_friendly_name": friendly_name,
		"fb_api_caller_class": "RelayModern",
	}
	try:
		r = sess.post(
			GRAPHQL_POST,
			data=form,
			headers=headers,
			proxies=proxies,
			timeout=timeout,
		)
	except Exception as exc:
		logger.debug(
			"IG audio probe: POST api/graphql %s %s: %s",
			friendly_name,
			shortcode,
			exc,
		)
		return None
	return _decode_graphql_body(r.text or "")


def probe_instagram_licensed_audio(local_port: int, timeout: float = 12.0) -> Optional[bool]:
	"""
	经本地 SOCKS(local_port) 探测：当前出口是否允许播放带 IG 授权音乐的帖子音频。
	返回 True=可用, False=不可用(已测得应静音/地区限制等), None=无法完成检测。
	"""
	sess = _session()
	proxies = _proxies(local_port)
	headers0 = {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
		),
		"Accept": "*/*",
		"Accept-Language": "en-US,en;q=0.9",
		"X-IG-App-ID": "936619743392459",
		"X-ASBD-ID": "129477",
		"Sec-Fetch-Site": "same-origin",
		"Sec-Fetch-Mode": "cors",
		"Sec-Fetch-Dest": "empty",
	}
	try:
		r0 = sess.get(ORIGIN + "/", headers=headers0, proxies=proxies, timeout=timeout)
	except Exception as exc:
		logger.debug("IG audio probe: GET / failed: %s", exc)
		return None
	if r0.status_code != 200:
		logger.debug("IG audio probe: GET / status %s", r0.status_code)
		return None
	html = r0.text or ""
	csrf = sess.cookies.get("csrftoken") or ""
	if not csrf:
		m = re.search(r'"csrfToken":"([^"]+)"', html) or re.search(r'"csrf_token":"([^"]+)"', html)
		if m:
			csrf = m.group(1)
	if not csrf:
		logger.debug("IG audio probe: csrftoken not found")
		return None
	lsd_m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
	lsd = lsd_m.group(1) if lsd_m else ""

	post_headers = {
		**headers0,
		"Content-Type": "application/x-www-form-urlencoded",
		"Origin": ORIGIN,
		"X-CSRFToken": csrf,
		"X-Requested-With": "XMLHttpRequest",
	}
	if lsd:
		post_headers["X-FB-LSD"] = lsd

	vars_post_root = {
		"shortcode": "",
		"child_comment_count": 3,
		"fetch_comment_count": 40,
		"parent_comment_count": 24,
		"has_threaded_comments": True,
	}

	for sc in PROBE_SHORTCODES:
		vars_post_root = {**vars_post_root, "shortcode": sc}
		get_headers = {
			**headers0,
			"X-CSRFToken": csrf,
			"X-Requested-With": "XMLHttpRequest",
			"Referer": "{}/p/{}/".format(ORIGIN, sc),
		}
		# 1) GET graphql/query（与当前网页 / yt-dlp 一致）
		payload = _graphql_get_shortcode_media(sess, proxies, get_headers, sc, timeout)
		if payload is not None:
			muted = _mute_from_response_payload(payload)
			if muted is True:
				logger.debug("IG audio probe: %s GET should_mute_audio=true", sc)
				return False
			if muted is False:
				return True
			# data 存在但 media 为 null：换 shortcode 或走 POST
			root = payload.get("data") if isinstance(payload, dict) else None
			if isinstance(root, dict) and root.get("xdt_shortcode_media") is None:
				logger.debug("IG audio probe: %s GET xdt_shortcode_media null", sc)

		post_h = {**post_headers, "Referer": "{}/p/{}/".format(ORIGIN, sc)}

		# 2) POST PolarisPostRootQuery
		payload = _graphql_post_api(
			sess,
			proxies,
			post_h,
			sc,
			timeout,
			doc_id=DOC_ID_POST_ROOT,
			friendly_name="PolarisPostRootQuery",
			variables_obj=vars_post_root,
			legacy_string=None,
		)
		if payload is not None:
			muted = _mute_from_response_payload(payload)
			if muted is None:
				muted = _mute_from_text_regex(json.dumps(payload) if isinstance(payload, dict) else "")
			if muted is True:
				logger.debug("IG audio probe: %s POST(root) should_mute_audio=true", sc)
				return False
			if muted is False:
				return True

		# 3) 旧 PolarisPostActionLoadPostQueryQuery
		payload = _graphql_post_api(
			sess,
			proxies,
			post_h,
			sc,
			timeout,
			doc_id=DOC_ID_POST_ACTION_LEGACY,
			friendly_name="PolarisPostActionLoadPostQueryQuery",
			variables_obj={},
			legacy_string=_VARS_LEGACY,
		)
		if payload is not None:
			raw = json.dumps(payload) if isinstance(payload, dict) else ""
			muted = _mute_from_response_payload(payload)
			if muted is None:
				muted = _mute_from_text_regex(raw)
			if muted is True:
				logger.debug("IG audio probe: %s POST(legacy) should_mute_audio=true", sc)
				return False
			if muted is False:
				return True

	logger.warning(
		"IG audio probe: could not determine should_mute_audio (blocked HTML, null media, or API changed). "
		"If this persists, update DOC_ID constants in ssrspeed/utils/ig_audio_check.py from a current web bundle."
	)
	return None
