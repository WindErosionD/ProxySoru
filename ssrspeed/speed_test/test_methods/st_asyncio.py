# -*- coding: utf-8 -*-

__all__ = ["start"]

import time
import copy
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import aiohttp
import requests
from aiohttp import ClientError
from aiohttp.client_exceptions import ClientConnectorError, ClientOSError
from aiohttp_socks import SocksVer, SocksError, SocksConnector, SocksConnectionError

from ...utils.geo_ip import IPLoc
from ...utils.rules import DownloadRuleMatch
from ...utils.platform_check import check_platform

from config import config

logger = logging.getLogger("Sub")

WORKERS = config["fileDownload"]["maxWorkers"]
BUFFER = config["fileDownload"]["buffer"]
_PERF = config.get("performance", {}) or {}
_AIO_TOTAL = float(_PERF.get("aio_timeout_seconds", 12))
_AIO_CONNECT = float(_PERF.get("aio_connect_timeout_seconds", 6))
_AIO_READ = float(_PERF.get("aio_read_timeout_seconds", 10))
SPEED_TEST_SECONDS = float(_PERF.get("speed_test_seconds", 8))
_ST_FAST_FAIL = bool(_PERF.get("st_async_fast_fail", True))
_ST_FAST_FAIL_SEC = float(_PERF.get("st_async_fast_fail_seconds", 2.0))
_ST_ZERO_QUICK = bool(_PERF.get("st_zero_retry_quick", True))
_ST_ZERO_CONNECT = float(_PERF.get("st_zero_retry_connect_seconds", 4))
_ST_ZERO_READ = float(_PERF.get("st_zero_retry_read_seconds", 6))


def _client_timeout(quick: bool = False):
	if quick:
		return (_ST_ZERO_CONNECT, _ST_ZERO_READ)
	return (_AIO_CONNECT, _AIO_READ)


def _fetch_timeout(quick: bool = False):
	if quick:
		return aiohttp.ClientTimeout(
			total=_ST_ZERO_CONNECT + _ST_ZERO_READ + 2,
			connect=_ST_ZERO_CONNECT,
			sock_read=_ST_ZERO_READ,
		)
	return aiohttp.ClientTimeout(
		total=_AIO_TOTAL,
		connect=_AIO_CONNECT,
		sock_read=_AIO_READ,
	)


class Statistics:
	def __init__(self):
		self._stopped = False
		self._total_red = 0
		self._delta_red = 0
		self._start_time = 0
		self._statistics_time = 0
		self._time_used = 0
		self._count = 0
		self._speed_list = []
		self._lock = threading.Lock()

	@property
	def stopped(self):
		return self._stopped

	@property
	def time_used(self):
		return self._time_used

	@property
	def total_red(self):
		return self._total_red

	@property
	def speed_list(self):
		return copy.deepcopy(self._speed_list)

	@property
	def max_speed(self):
		tmp_speed_list = self.speed_list
		tmp_speed_list.sort()
		max_speed = 0
		if len(tmp_speed_list) > 12:
			msum = 0
			for i in range(12, len(tmp_speed_list) - 2):
				msum += tmp_speed_list[i]
				max_speed = msum / (len(tmp_speed_list) - 2 - 12)
		elif self._time_used > 0:
			max_speed = self._total_red / self._time_used
		return max_speed

	def _record(self, received: int):
		cur_time = time.time()
		if not self._start_time:
			self._start_time = cur_time
		delta_time = cur_time - self._statistics_time
		self._time_used = cur_time - self._start_time
		self._total_red += received
		if delta_time > 0.5:
			self._statistics_time = cur_time
			try:
				self._show_progress(delta_time)
			except StopIteration:
				pass
		if self.time_used > SPEED_TEST_SECONDS:
			self._stopped = True

	async def record(self, received: int):
		with self._lock:
			self._record(received)

	def record_sync(self, received: int):
		with self._lock:
			self._record(received)

	def show_progress_full(self):
		mb_red = self._total_red / 1024 / 1024
		if self._time_used > 0:
			avg_mbps = mb_red / self._time_used
		else:
			avg_mbps = 0.0
		print("\r[" + "=" * self._count + "] [{:.2f} MB/s]".format(avg_mbps), end='\n')
		logger.info("Fetched {:.2f} MB in {:.2f}s".format(mb_red, self._time_used))

	def _show_progress(self, delta_time: int):
		speed = (self._total_red - self._delta_red) / delta_time
		speed_mb = speed / 1024 / 1024
		self._delta_red = self._total_red
		self._count += 1
		print("\r[" + "=" * self._count + "> [{:.2f} MB/s]".format(speed_mb), end='')


async def _fetch(
	url: str,
	sta: Statistics,
	host: str = "127.0.0.1",
	port: int = 1087,
	rdns: bool = True,
	quick: bool = False,
):
	connector = SocksConnector(
		socks_ver=SocksVer.SOCKS5,
		host=host,
		port=port,
		rdns=rdns,
	)
	logger.info("Fetching %s via %s:%s (aiohttp rdns=%s).", url, host, port, rdns)
	try:
		async with aiohttp.ClientSession(
			connector=connector,
			headers={"User-Agent": "curl/11.45.14"},
			timeout=_fetch_timeout(quick),
		) as session:
			async with session.get(url) as response:
				while not sta.stopped:
					chunk = await response.content.read(BUFFER)
					if not chunk:
						break
					await sta.record(len(chunk))
	except (ClientOSError, ClientConnectorError, SocksError, SocksConnectionError, asyncio.TimeoutError, ConnectionResetError, OSError) as e:
		detail = str(e).strip() or e.__class__.__name__
		logger.warning("aiohttp worker failed via %s:%s: %s", host, port, detail)
	except ClientError as e:
		detail = str(e).strip() or e.__class__.__name__
		logger.warning("aiohttp HTTP error via %s:%s: %s", host, port, detail)
	except Exception:
		logger.exception("aiohttp worker unexpected error via %s:%s", host, port)


def _requests_proxies(host: str, port: int, mode: str) -> dict:
	if mode == "http":
		px = "http://{}:{}".format(host, port)
	elif mode == "socks5h":
		px = "socks5h://{}:{}".format(host, port)
	else:
		px = "socks5://{}:{}".format(host, port)
	return {"http": px, "https": px}


def _requests_fetch(url: str, sta: Statistics, host: str, port: int, mode: str, quick: bool = False):
	logger.info("Fetching %s via %s:%s (requests %s).", url, host, port, mode)
	proxies = _requests_proxies(host, port, mode)
	timeout = _client_timeout(quick)
	headers = {"User-Agent": "curl/11.45.14"}
	try:
		with requests.get(
			url,
			proxies=proxies,
			stream=True,
			timeout=timeout,
			headers=headers,
		) as resp:
			resp.raise_for_status()
			for chunk in resp.iter_content(chunk_size=BUFFER):
				if sta.stopped:
					break
				if chunk:
					sta.record_sync(len(chunk))
	except requests.RequestException as e:
		detail = str(e).strip() or e.__class__.__name__
		logger.warning("requests worker failed via %s:%s (%s): %s", host, port, mode, detail)
	except Exception:
		logger.exception("requests worker unexpected error via %s:%s (%s)", host, port, mode)


def _run_aio_workers(
	url: str,
	proxy_host: str,
	proxy_port: int,
	workers: int,
	rdns: bool,
	quick: bool = False,
) -> Statistics:
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	sta = Statistics()
	tasks = [
		loop.create_task(_fetch(url, sta, proxy_host, proxy_port, rdns=rdns, quick=quick))
		for _ in range(workers)
	]
	try:
		loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
	finally:
		loop.close()
	return sta


def _run_requests_workers(
	url: str,
	proxy_host: str,
	proxy_port: int,
	workers: int,
	mode: str,
	quick: bool = False,
) -> Statistics:
	sta = Statistics()
	with ThreadPoolExecutor(max_workers=workers) as exe:
		futs = [
			exe.submit(_requests_fetch, url, sta, proxy_host, proxy_port, mode, quick)
			for _ in range(workers)
		]
		for fut in as_completed(futs):
			try:
				fut.result()
			except Exception:
				logger.exception("requests worker thread failed.")
	return sta


def _result_tuple(sta: Statistics):
	sta.show_progress_full()
	if sta.time_used:
		return (sta.total_red / sta.time_used, sta.max_speed, sta.speed_list, sta.total_red)
	return (0, 0, [], 0)


def _try_request_modes(
	url: str,
	proxy_host: str,
	proxy_port: int,
	workers: int,
	modes,
	quick: bool = False,
	fast_fail: bool = False,
):
	for mode in modes:
		t0 = time.time()
		sta = _run_requests_workers(url, proxy_host, proxy_port, workers, mode, quick=quick)
		if sta.total_red > 0:
			return sta
		if fast_fail and (time.time() - t0) < _ST_FAST_FAIL_SEC:
			logger.info(
				"ST_ASYNC fast-fail after %s (%.2fs); proxy likely down.",
				mode,
				time.time() - t0,
			)
			break
	return None


def start(
	proxy_host="127.0.0.1",
	proxy_port: int = 1087,
	workers: int = WORKERS,
	quick: bool = False,
):
	"""quick=True：0 速硬重啟後的重测，仅 socks5h、较短超时、不走 aiohttp 多轮 fallback。"""
	dlrm = DownloadRuleMatch()
	res = dlrm.get_url(IPLoc())
	url = res[0]
	file_size = res[1]
	logger.debug("Url: %s, file_size: %s MiB", url, file_size)
	if check_platform() == "Windows":
		workers = min(workers, 4)
	logger.info("Running st_async, workers: %s%s", workers, " (quick retry)" if quick else "")

	request_modes = ("socks5h",) if quick else ("socks5h", "http", "socks5")
	fast_fail = _ST_FAST_FAIL and not quick

	sta = _try_request_modes(
		url, proxy_host, proxy_port, workers, request_modes, quick=quick, fast_fail=fast_fail,
	)
	if sta is not None and sta.total_red > 0:
		return _result_tuple(sta)

	if quick:
		return _result_tuple(Statistics())

	# Windows 上 aiohttp-socks + HTTPS 常失败；优先 requests（与 Clash 系客户端一致）
	if check_platform() == "Windows":
		logger.warning("ST_ASYNC requests got 0 bytes on Windows; trying aiohttp fallback.")

	for rdns in (True, False):
		sta = _run_aio_workers(url, proxy_host, proxy_port, workers, rdns=rdns, quick=False)
		if sta.total_red > 0:
			return _result_tuple(sta)

	for mode in ("socks5h", "http", "socks5"):
		logger.warning("ST_ASYNC aiohttp got 0 bytes; retry with requests (%s).", mode)
		sta = _run_requests_workers(url, proxy_host, proxy_port, workers, mode, quick=False)
		if sta.total_red > 0:
			return _result_tuple(sta)

	return _result_tuple(Statistics())
