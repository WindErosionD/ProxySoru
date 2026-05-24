# coding: utf-8
"""
检测「Instagram 带版权音乐 / 授权音频」是否可用（与仅打开 instagram.com 不同）。

经 SOCKS 访问官网取 csrftoken，再请求 GraphQL 取 Reels 元数据中的
clips_music_attribution_info（uses_original_audio=false 的授权曲库音乐）。
仅当授权音乐 should_mute_audio=false 且可验证到音视频流时判为可用；
原创音频（uses_original_audio=true）不参与判定，避免误报。

返回 True=可播授权音乐, False=应静音/地区无授权, None=无法完成检测。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Sub")

try:
	from curl_cffi import requests as _http

	_CFFI = True
except Exception:
	import requests as _http

	_CFFI = False

try:
	from config import config as _app_config
except Exception:
	_app_config = {}

GRAPHQL_POST = "https://www.instagram.com/api/graphql"
GRAPHQL_GET = "https://www.instagram.com/graphql/query/"
ORIGIN = "https://www.instagram.com"

# 授权曲库音乐 Reels（uses_original_audio=false）；勿用原创音频帖（易误报可用）
_DEFAULT_PROBE_SHORTCODES = (
	"DCchrGBJFYA",  # Starbucks 2024 holiday / Sam & Dave 授权曲
	"DIJfw-Iu6h4",  # 社区常用 Taylor Swift / Fortnight 相关 Reel
	"ConUVfbgKEl",  # Miley Cyrus - Flowers 授权 Reel
	"DVn-vNTCIfX",  # 带背景音乐的 Reel（yt-dlp 社区样例）
	"DI-stckPoqZ",  # 授权音乐 Reel
)

DOC_ID_SHORTCODE_MEDIA = "8845758582119845"
DOC_ID_POST_ROOT = "26544629655158927"
DOC_ID_POST_ACTION_LEGACY = "10015901848480474"

_VARS_LEGACY = (
	'{"shortcode":"%s","fetch_comment_count":40,'
	'"fetch_related_profile_media_count":3,"parent_comment_count":24,'
	'"child_comment_count":3,"fetch_like_count":10,"fetch_tagged_user_count":null,'
	'"fetch_preview_comment_count":2,"has_threaded_comments":true,'
	'"hoisted_comment_id":null,"hoisted_reply_id":null}'
)

_MIN_LICENSED_UNMUTE = 1


def _probe_shortcodes() -> Tuple[str, ...]:
	custom = _app_config.get("ig_audio_probe_shortcodes")
	if isinstance(custom, (list, tuple)):
		clean = [str(x).strip() for x in custom if str(x).strip()]
		if clean:
			return tuple(clean)
	return _DEFAULT_PROBE_SHORTCODES


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


def _media_node(payload: dict) -> Optional[dict]:
	if not isinstance(payload, dict):
		return None
	root = payload.get("data")
	if not isinstance(root, dict):
		return None
	media = root.get("xdt_shortcode_media") or root.get("shortcode_media")
	return media if isinstance(media, dict) else None


def _music_attribution(media: dict) -> Optional[dict]:
	mai = media.get("clips_music_attribution_info")
	return mai if isinstance(mai, dict) else None


def _licensed_mute_signal(media: dict) -> Optional[bool]:
	"""
	仅解析授权曲库音乐（uses_original_audio=false）的 should_mute_audio。
	原创音频、无 music 字段的帖子返回 None（不参与判定）。
	"""
	mai = _music_attribution(media)
	if not mai or mai.get("uses_original_audio") is not False:
		return None
	mute = mai.get("should_mute_audio")
	if isinstance(mute, bool):
		return mute
	return None


def _licensed_mute_from_payload(payload: dict) -> Optional[bool]:
	media = _media_node(payload)
	if not media:
		return None
	return _licensed_mute_signal(media)


def _licensed_mute_from_text_regex(text: str) -> Optional[bool]:
	"""
	仅在 clips_music_attribution_info 且 uses_original_audio:false 的片段内匹配 mute。
	避免误读 sidecar / 原创音频字段。
	"""
	if not text:
		return None
	for chunk in re.findall(
		r'"clips_music_attribution_info"\s*:\s*(\{.*?\})(?=,\s*"[A-Za-z_]+"\s*:|\})',
		text,
		re.S,
	):
		if re.search(r'"uses_original_audio"\s*:\s*true', chunk, re.I):
			continue
		if not re.search(r'"uses_original_audio"\s*:\s*false', chunk, re.I):
			continue
		m = re.search(r'"should_mute_audio"\s*:\s*(true|false)', chunk, re.I)
		if m:
			return m.group(1).lower() == "true"
	return None


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


def _verify_playable_media(sess, proxies: dict, headers: dict, media: dict, timeout: float) -> bool:
	"""授权音乐未标记 mute 时，尽量确认视频流/音频轨可访问。"""
	if media.get("has_audio") is False:
		return False
	video_url = media.get("video_url")
	if isinstance(video_url, str) and video_url.startswith("http"):
		try:
			r = sess.head(
				video_url,
				headers={**headers, "Referer": ORIGIN + "/"},
				proxies=proxies,
				timeout=min(timeout, 8.0),
				allow_redirects=True,
			)
			if r.status_code < 400:
				return True
		except Exception as exc:
			logger.debug("IG audio probe: video HEAD failed: %s", exc)
	dash = media.get("dash_info") or media.get("video_dash_manifest")
	if isinstance(dash, str) and ("audio" in dash.lower() or "mp4a" in dash.lower()):
		return True
	return media.get("has_audio") is True


def _evaluate_licensed_media(media: dict) -> Optional[bool]:
	mute = _licensed_mute_signal(media)
	if mute is True:
		mai = _music_attribution(media) or {}
		logger.debug(
			"IG audio probe: licensed mute=true song=%r artist=%r reason=%r",
			mai.get("song_name"),
			mai.get("artist_name"),
			(mai.get("should_mute_audio_reason") or "")[:120],
		)
		return False
	if mute is False:
		return True
	return None


def _payload_paths_for_shortcode(
	sess,
	proxies: dict,
	base_headers: dict,
	csrf: str,
	shortcode: str,
	timeout: float,
) -> List[dict]:
	get_headers = {
		**base_headers,
		"X-CSRFToken": csrf,
		"X-Requested-With": "XMLHttpRequest",
		"Referer": "{}/reel/{}/".format(ORIGIN, shortcode),
	}
	post_headers = {
		**get_headers,
		"Content-Type": "application/x-www-form-urlencoded",
		"Origin": ORIGIN,
	}
	vars_post_root = {
		"shortcode": shortcode,
		"child_comment_count": 3,
		"fetch_comment_count": 40,
		"parent_comment_count": 24,
		"has_threaded_comments": True,
	}
	out: List[dict] = []

	payload = _graphql_get_shortcode_media(sess, proxies, get_headers, shortcode, timeout)
	if payload is not None:
		out.append(payload)

	payload = _graphql_post_api(
		sess,
		proxies,
		post_headers,
		shortcode,
		timeout,
		doc_id=DOC_ID_POST_ROOT,
		friendly_name="PolarisPostRootQuery",
		variables_obj=vars_post_root,
	)
	if payload is not None:
		out.append(payload)

	payload = _graphql_post_api(
		sess,
		proxies,
		post_headers,
		shortcode,
		timeout,
		doc_id=DOC_ID_POST_ACTION_LEGACY,
		friendly_name="PolarisPostActionLoadPostQueryQuery",
		variables_obj={},
		legacy_string=_VARS_LEGACY,
	)
	if payload is not None:
		out.append(payload)
	return out


def _probe_one_shortcode(
	sess,
	proxies: dict,
	base_headers: dict,
	csrf: str,
	shortcode: str,
	timeout: float,
) -> Tuple[Optional[bool], Optional[dict]]:
	for payload in _payload_paths_for_shortcode(sess, proxies, base_headers, csrf, shortcode, timeout):
		media = _media_node(payload)
		if media is not None:
			result = _evaluate_licensed_media(media)
			if result is not None:
				return result, media
		mute = _licensed_mute_from_payload(payload)
		if mute is None:
			raw = json.dumps(payload) if isinstance(payload, dict) else ""
			mute = _licensed_mute_from_text_regex(raw)
		if mute is True:
			return False, media
		if mute is False and media is not None:
			return True, media
	return None, None


def probe_instagram_licensed_audio(local_port: int, timeout: float = 12.0) -> Optional[bool]:
	"""
	经本地 SOCKS(local_port) 探测：当前出口是否允许播放带 IG 授权曲库音乐的 Reels 音频。
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
	if lsd_m:
		headers0 = {**headers0, "X-FB-LSD": lsd_m.group(1)}

	licensed_hits = 0
	verified_media: Optional[dict] = None

	for sc in _probe_shortcodes():
		result, media = _probe_one_shortcode(sess, proxies, headers0, csrf, sc, timeout)
		if result is False:
			logger.debug("IG audio probe: %s licensed should_mute_audio=true", sc)
			return False
		if result is True:
			licensed_hits += 1
			if verified_media is None and media is not None:
				verified_media = media
			logger.debug("IG audio probe: %s licensed should_mute_audio=false", sc)
			if licensed_hits >= _MIN_LICENSED_UNMUTE:
				break

	if licensed_hits >= _MIN_LICENSED_UNMUTE:
		if verified_media is not None and not _verify_playable_media(
			sess, proxies, headers0, verified_media, timeout
		):
			logger.debug("IG audio probe: licensed unmuted but media stream check failed")
			return False
		return True

	logger.warning(
		"IG audio probe: no licensed-music probe returned should_mute_audio "
		"(posts removed, API changed, or only original-audio reels matched). "
		"Update ig_audio_probe_shortcodes in ssrspeed_config.json if needed."
	)
	return None
