# coding: utf-8
"""
IG 音频解锁：模拟用户打开帖子页，再对 CDN 媒体流做 Range 抽样，确认是否含可播音轨。

返回 True=可播, False=不可播/应静音, None=无法完成检测。
"""

import html as html_lib
import json
import logging
import re
from typing import Any, List, Optional, Tuple
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

_DEFAULT_PROBE_SHORTCODES = ("DHuyNBZSV_1",)

# Range 抽样上限；足够判断 mp4 是否含音轨，又避免拖慢测速
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
		for imp in ("chrome131", "chrome124", "chrome120", "chrome110"):
			try:
				return _http.Session(impersonate=imp)
			except Exception:
				continue
	return _http.Session()


def _nav_headers(referer: str = ORIGIN + "/") -> dict:
	return {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
		),
		"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
		"Accept-Language": "en-US,en;q=0.9",
		"Accept-Encoding": "gzip, deflate, br",
		"Referer": referer,
		"Sec-Fetch-Site": "same-origin",
		"Sec-Fetch-Mode": "navigate",
		"Sec-Fetch-Dest": "document",
		"Sec-Fetch-User": "?1",
		"Upgrade-Insecure-Requests": "1",
	}


def _api_headers(referer: str, csrf: str) -> dict:
	return {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
		),
		"Accept": "*/*",
		"Accept-Language": "en-US,en;q=0.9",
		"Referer": referer,
		"X-IG-App-ID": "936619743392459",
		"X-ASBD-ID": "129477",
		"X-CSRFToken": csrf,
		"X-Requested-With": "XMLHttpRequest",
		"Sec-Fetch-Site": "same-origin",
		"Sec-Fetch-Mode": "cors",
		"Sec-Fetch-Dest": "empty",
	}


def _cdn_headers(referer: str) -> dict:
	return {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
		),
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
	try:
		r = sess.get(GRAPHQL_GET, params=params, headers=headers, proxies=proxies, timeout=timeout)
	except Exception as exc:
		logger.debug("IG audio: graphql %s failed: %s", shortcode, exc)
		return None
	payload = _decode_graphql_body(r.text or "")
	if not payload:
		return None
	root = payload.get("data") or {}
	return root.get("xdt_shortcode_media") or root.get("shortcode_media")


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
		for tag in ("BaseURL", "mpd:BaseURL"):
			for el in adp.findall(".//{}".format(tag)) or adp.findall(tag):
				if el is not None and el.text:
					u = html_lib.unescape(el.text.strip())
					if u.startswith("http"):
						urls.append(u)
	return urls


def _collect_stream_urls(media: dict) -> List[str]:
	if not isinstance(media, dict):
		return []
	seen = set()
	out: List[str] = []

	def add(u):
		if isinstance(u, str) and u.startswith("http") and u not in seen:
			seen.add(u)
			out.append(u)

	add(media.get("video_url"))
	for key in ("video_dash_manifest", "dash_info"):
		val = media.get(key)
		if isinstance(val, str):
			for u in _audio_urls_from_dash(val):
				add(u)
		elif isinstance(val, dict):
			for u in _audio_urls_from_dash(str(val.get("manifest") or val.get("video_dash_manifest") or "")):
				add(u)
	mai = media.get("clips_music_attribution_info")
	if isinstance(mai, dict):
		add(mai.get("audio_asset_id"))  # rarely a URL; harmless if not http
	edges = (media.get("edge_sidecar_to_children") or {}).get("edges") or []
	for edge in edges:
		node = edge.get("node") if isinstance(edge, dict) else None
		if isinstance(node, dict):
			for u in _collect_stream_urls(node):
				add(u)
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
	if not isinstance(media, dict):
		return False
	if media.get("has_audio") is False:
		return True
	mai = media.get("clips_music_attribution_info")
	if isinstance(mai, dict) and mai.get("should_mute_audio") is True:
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
	api_h = _api_headers(post_url, csrf)
	api_h.update(extra_headers)

	if media is None:
		media = _graphql_media(sess, proxies, api_h, shortcode, req_timeout)

	if media is None:
		logger.debug("IG audio: no media for shortcode %s", shortcode)
		return None

	stream_urls = _collect_stream_urls(media)
	if not stream_urls:
		if _explicit_muted_in_media(media):
			return False
		logger.debug("IG audio: %s has no stream URL", shortcode)
		return None

	cdn_h = _cdn_headers(post_url)
	any_network_ok = False
	for url in stream_urls[:_MAX_CDN_PROBES]:
		result = _probe_cdn_stream(sess, url, proxies, cdn_h, req_timeout)
		if result is None:
			continue
		any_network_ok = True
		if result is True:
			logger.debug("IG audio: %s playable (CDN audio track detected)", shortcode)
			return True
		logger.debug("IG audio: %s stream ok but no audio track in sample", shortcode)

	if any_network_ok:
		return False
	if _explicit_muted_in_media(media):
		return False
	return None


def probe_instagram_licensed_audio(local_port: int, timeout: float = 12.0) -> Optional[bool]:
	"""
	模拟用户：打开 IG 首页 → 打开探针帖子 → Range 抽样 CDN 媒体流是否含音轨。
	仅检测第一个 shortcode，控制总耗时。
	"""
	total_budget = _perf_timeout(timeout)
	req_timeout = max(3.0, min(total_budget * 0.42, 6.0))

	sess = _session()
	proxies = _proxies(local_port)

	csrf, extra = _bootstrap_csrf(sess, proxies, req_timeout)
	if not csrf:
		logger.debug("IG audio: csrftoken missing")
		return None

	shortcodes = _probe_shortcodes()
	if not shortcodes:
		return None

	result = _probe_one_shortcode(
		sess, proxies, csrf, extra, shortcodes[0], req_timeout,
	)
	if result is not None:
		return result

	logger.warning(
		"IG audio probe inconclusive (shortcode=%s); check ig_audio_probe_shortcodes or network.",
		shortcodes[0],
	)
	return None
