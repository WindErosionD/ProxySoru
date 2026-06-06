# coding: utf-8
"""
IG 音频解锁：模拟手机端 Instagram 客户端打开探针帖子，
读取版权音乐元数据（should_mute_audio），并对 CDN 媒体流 Range 抽样确认是否含可播音轨。

返回 True=可播, False=不可播/应静音, None=无法完成检测。
"""

import html as html_lib
import json
import logging
import re
from typing import Any, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

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

ORIGIN = "https://www.instagram.com"
GRAPHQL_GET = "https://www.instagram.com/graphql/query/"
DOC_ID_SHORTCODE_MEDIA = "8845758582119845"

# 模拟 Instagram Android 客户端（与手机 App 请求特征一致）
_MOBILE_UA = (
	"Instagram 359.0.0.0.45 Android (31/12; 420dpi; 1080x2340; "
	"Google/Google; Pixel 6; oriole; oriole; en_US; 563492816)"
)
_IG_APP_ID_ANDROID = "567067343352427"
_IG_WEB_APP_ID = "936619743392459"

# DH56yy7p3lZ：社区常用公开 Reel（NatGeo），API 可拿 video_url + 音轨；DOvzTywjPGN 备用
_DEFAULT_PROBE_SHORTCODES = (
	"DH56yy7p3lZ",
	"DOvzTywjPGN",
)

_RANGE_BYTES = 65536
_MIN_RANGE_GOT = 8192
_MAX_CDN_PROBES = 2


def _perf_timeout(default: float) -> float:
	perf = _app_config.get("performance") or {}
	try:
		v = float(perf.get("ig_audio_timeout_seconds", default))
	except (TypeError, ValueError):
		v = default
	return max(4.0, min(v, 12.0))


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


def _session():
	if _CFFI:
		for imp in (
			"chrome131_android",
			"chrome120_android",
			"safari18_0_ios",
			"chrome131",
			"chrome124",
		):
			try:
				return _http.Session(impersonate=imp)
			except Exception:
				continue
	return _http.Session()


def _nav_headers(referer: str = ORIGIN + "/") -> dict:
	return {
		"User-Agent": _MOBILE_UA,
		"Accept": (
			"text/html,application/xhtml+xml,application/xml;q=0.9,"
			"image/avif,image/webp,image/apng,*/*;q=0.8"
		),
		"Accept-Language": "en-US,en;q=0.9",
		"Accept-Encoding": "gzip, deflate, br",
		"Referer": referer,
		"X-IG-App-ID": _IG_APP_ID_ANDROID,
		"X-ASBD-ID": "129477",
		"Sec-Fetch-Site": "same-origin",
		"Sec-Fetch-Mode": "navigate",
		"Sec-Fetch-Dest": "document",
		"Sec-Fetch-User": "?1",
		"Upgrade-Insecure-Requests": "1",
	}


def _api_headers(referer: str, csrf: str) -> dict:
	return {
		"User-Agent": _MOBILE_UA,
		"Accept": "*/*",
		"Accept-Language": "en-US,en;q=0.9",
		"Referer": referer,
		"X-IG-App-ID": _IG_APP_ID_ANDROID,
		"X-ASBD-ID": "129477",
		"X-CSRFToken": csrf,
		"X-Requested-With": "XMLHttpRequest",
		"X-IG-Capabilities": "3brTv10=",
		"Sec-Fetch-Site": "same-origin",
		"Sec-Fetch-Mode": "cors",
		"Sec-Fetch-Dest": "empty",
	}


def _cdn_headers(referer: str) -> dict:
	return {
		"User-Agent": _MOBILE_UA,
		"Accept": "*/*",
		"Accept-Language": "en-US,en;q=0.9",
		"Referer": referer,
		"Origin": ORIGIN,
		"Range": "bytes=0-{}".format(_RANGE_BYTES - 1),
	}


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


def _json_blobs_from_html(page_html: str) -> List[dict]:
	blobs: List[dict] = []
	if not page_html:
		return blobs
	for m in re.finditer(
		r'<script type="application/json"[^>]*>(\{.*?\})</script>',
		page_html,
		re.S,
	):
		try:
			blobs.append(json.loads(m.group(1)))
		except Exception:
			continue
	return blobs


def _find_media_node(obj: Any) -> Optional[dict]:
	if isinstance(obj, dict):
		typ = obj.get("__typename") or ""
		if typ.endswith("Media") or typ in ("GraphVideo", "GraphSidecar", "XDTGraphVideo", "XDTGraphSidecar"):
			if obj.get("shortcode") or obj.get("video_url") or obj.get("video_dash_manifest"):
				return obj
		for key in ("xdt_shortcode_media", "shortcode_media", "media"):
			val = obj.get(key)
			if isinstance(val, dict):
				return val
		for val in obj.values():
			found = _find_media_node(val)
			if found is not None:
				return found
	elif isinstance(obj, list):
		for item in obj:
			found = _find_media_node(item)
			if found is not None:
				return found
	return None


def _media_from_html(page_html: str) -> Optional[dict]:
	for blob in _json_blobs_from_html(page_html):
		media = _find_media_node(blob)
		if media is not None:
			return media
	return None


def _graphql_media(sess, proxies: dict, headers: dict, shortcode: str, timeout: float) -> Optional[dict]:
	params = {
		"doc_id": DOC_ID_SHORTCODE_MEDIA,
		"variables": json.dumps(
			{
				"shortcode": shortcode,
				"child_comment_count": 3,
				"fetch_comment_count": 40,
				"parent_comment_count": 24,
				"has_threaded_comments": True,
			},
			separators=(",", ":"),
		),
	}
	for app_id in (_IG_APP_ID_ANDROID, _IG_WEB_APP_ID):
		req_h = dict(headers)
		req_h["X-IG-App-ID"] = app_id
		try:
			r = sess.get(GRAPHQL_GET, params=params, headers=req_h, proxies=proxies, timeout=timeout)
		except Exception as exc:
			logger.debug("IG audio: graphql %s app_id=%s failed: %s", shortcode, app_id, exc)
			continue
		payload = _decode_graphql_body(r.text or "")
		if not payload:
			continue
		root = payload.get("data") or {}
		media = root.get("xdt_shortcode_media") or root.get("shortcode_media")
		if media:
			return media
	return None


def _audio_urls_from_dash(manifest: str) -> List[str]:
	if not manifest or not isinstance(manifest, str):
		return []
	urls: List[str] = []
	text = manifest.strip()
	if not text.startswith("<"):
		return urls
	try:
		root = ElementTree.fromstring(text)
	except ElementTree.ParseError:
		return urls
	ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
	for adp in root.findall(".//mpd:AdaptationSet", ns) or root.findall(".//AdaptationSet"):
		mime = (adp.get("mimeType") or adp.get("contentType") or "").lower()
		codecs = (adp.get("codecs") or "").lower()
		if "audio" not in mime and "mp4a" not in codecs and "audio" not in codecs:
			continue
		for el in adp.findall("mpd:BaseURL", ns):
			if el is not None and el.text:
				u = html_lib.unescape(el.text.strip())
				if u.startswith("http"):
					urls.append(u)
		for el in adp.findall("BaseURL"):
			if el is not None and el.text:
				u = html_lib.unescape(el.text.strip())
				if u.startswith("http"):
					urls.append(u)
	return urls


def _iter_media_nodes(media: dict) -> Iterable[dict]:
	if not isinstance(media, dict):
		return
	yield media
	for edge in (media.get("edge_sidecar_to_children") or {}).get("edges") or []:
		node = edge.get("node") if isinstance(edge, dict) else None
		if isinstance(node, dict):
			yield from _iter_media_nodes(node)


def _iter_music_attribution(media: dict) -> Iterable[dict]:
	for node in _iter_media_nodes(media):
		mai = node.get("clips_music_attribution_info")
		if isinstance(mai, dict):
			yield mai


def _collect_stream_urls(media: dict) -> List[str]:
	if not isinstance(media, dict):
		return []
	seen = set()
	out: List[str] = []

	def add(u):
		if isinstance(u, str) and u.startswith("http") and u not in seen:
			seen.add(u)
			out.append(u)

	for node in _iter_media_nodes(media):
		add(node.get("video_url"))
		for key in ("video_dash_manifest", "dash_info"):
			val = node.get(key)
			if isinstance(val, str):
				for u in _audio_urls_from_dash(val):
					add(u)
			elif isinstance(val, dict):
				for u in _audio_urls_from_dash(str(val.get("manifest") or val.get("video_dash_manifest") or "")):
					add(u)
		mai = node.get("clips_music_attribution_info")
		if isinstance(mai, dict):
			for key in ("progressive_download_url", "fast_start_progressive_download_url", "highlight_reel_url"):
				add(mai.get(key))
	return out


def _chunk_has_audio_track(data: bytes) -> bool:
	if len(data) < 256:
		return False
	if b"mp4a" in data or b"soun" in data or b".mp4a" in data:
		return True
	ct_audio = (b"audio/mp4", b"audio/mp4a", b"M4A ")
	return any(sig in data[:4096] for sig in ct_audio)


def _probe_cdn_stream(sess, url: str, proxies: dict, headers: dict, timeout: float) -> Optional[bool]:
	"""Range 拉流抽样：True=检测到音轨, False=流不可用或无声, None=网络/超时。"""
	try:
		r = sess.get(url, headers=headers, proxies=proxies, timeout=timeout, stream=True)
	except Exception as exc:
		logger.debug("IG audio: CDN GET failed: %s", exc)
		return None
	try:
		if r.status_code not in (200, 206):
			logger.debug("IG audio: CDN status %s for %s", r.status_code, url[:80])
			return False
		buf = bytearray()
		for chunk in r.iter_content(chunk_size=16384):
			if not chunk:
				break
			buf.extend(chunk)
			if len(buf) >= _RANGE_BYTES:
				break
		if len(buf) < _MIN_RANGE_GOT:
			return False
		return _chunk_has_audio_track(bytes(buf))
	finally:
		r.close()


def _explicit_muted_in_media(media: dict) -> bool:
	for node in _iter_media_nodes(media):
		if node.get("has_audio") is False:
			return True
	for mai in _iter_music_attribution(media):
		if mai.get("should_mute_audio") is True:
			return True
	return False


def _has_music_attribution(media: dict) -> bool:
	for mai in _iter_music_attribution(media):
		if mai.get("song_name") or mai.get("artist_name") or mai.get("audio_id"):
			return True
	return False


def _bootstrap_csrf(sess, proxies: dict, timeout: float) -> Tuple[str, dict]:
	try:
		r = sess.get(ORIGIN + "/", headers=_nav_headers(), proxies=proxies, timeout=timeout)
	except Exception as exc:
		logger.debug("IG audio: GET / failed: %s", exc)
		return "", {}
	if r.status_code != 200:
		return "", {}
	page = r.text or ""
	csrf = sess.cookies.get("csrftoken") or ""
	if not csrf:
		m = re.search(r'"csrfToken":"([^"]+)"', page) or re.search(r'"csrf_token":"([^"]+)"', page)
		if m:
			csrf = m.group(1)
	extra = {}
	lsd_m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', page)
	if lsd_m:
		extra["X-FB-LSD"] = lsd_m.group(1)
	return csrf, extra


def _probe_one_shortcode(
	sess,
	proxies: dict,
	csrf: str,
	extra_headers: dict,
	shortcode: str,
	req_timeout: float,
) -> Optional[bool]:
	post_url = "{}/p/{}/".format(ORIGIN, shortcode)
	api_h = _api_headers(post_url, csrf)
	api_h.update(extra_headers)

	media = _graphql_media(sess, proxies, api_h, shortcode, req_timeout)
	if media is None:
		page_html = ""
		try:
			r = sess.get(
				post_url,
				headers=_nav_headers(referer=ORIGIN + "/"),
				proxies=proxies,
				timeout=req_timeout,
			)
			if r.status_code == 200:
				page_html = r.text or ""
		except Exception as exc:
			logger.debug("IG audio: GET %s failed: %s", post_url, exc)
		media = _media_from_html(page_html)

	if media is None:
		logger.debug("IG audio: no media for shortcode %s", shortcode)
		return None

	if _explicit_muted_in_media(media):
		logger.debug("IG audio: %s muted by region/policy (mobile metadata)", shortcode)
		return False

	stream_urls = _collect_stream_urls(media)
	if not stream_urls:
		if _has_music_attribution(media):
			logger.debug(
				"IG audio: %s has music metadata but no fetchable stream URL",
				shortcode,
			)
		else:
			logger.debug("IG audio: %s has no music/video stream to probe", shortcode)
		return None

	cdn_h = _cdn_headers(post_url)
	any_network_ok = False
	for url in stream_urls[:_MAX_CDN_PROBES]:
		result = _probe_cdn_stream(sess, url, proxies, cdn_h, req_timeout)
		if result is None:
			continue
		any_network_ok = True
		if result is True:
			logger.debug("IG audio: %s playable (mobile CDN audio track detected)", shortcode)
			return True
		logger.debug("IG audio: %s stream ok but no audio track in sample", shortcode)

	if any_network_ok:
		return False
	return None


def probe_instagram_licensed_audio(local_port: int, timeout: float = 12.0) -> Optional[bool]:
	"""
	模拟手机 Instagram 客户端：打开首页 → 打开探针帖子 → 读取 should_mute_audio → CDN 抽样音轨。
	按 ig_audio_probe_shortcodes 顺序尝试，直到得到明确结果或全部用尽。
	"""
	total_budget = _perf_timeout(timeout)
	req_timeout = max(4.0, min(total_budget * 0.45, 8.0))

	sess = _session()
	proxies = _proxies(local_port)

	csrf, extra = _bootstrap_csrf(sess, proxies, req_timeout)
	if not csrf:
		csrf, extra = _bootstrap_csrf(sess, proxies, req_timeout)
	if not csrf:
		logger.warning("IG audio: cannot obtain csrftoken via proxy (Instagram unreachable).")
		return None

	shortcodes = _probe_shortcodes()
	if not shortcodes:
		return None

	last_shortcode = shortcodes[-1]
	for shortcode in shortcodes:
		last_shortcode = shortcode
		result = _probe_one_shortcode(
			sess, proxies, csrf, extra, shortcode, req_timeout,
		)
		if result is not None:
			return result

	logger.warning(
		"IG audio probe inconclusive (last shortcode=%s); check ig_audio_probe_shortcodes or network.",
		last_shortcode,
	)
	return None
