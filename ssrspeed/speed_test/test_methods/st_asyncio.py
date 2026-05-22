# -*- coding: utf-8 -*-

__all__ = ["start"]

import time
import copy
import logging
import asyncio
import aiohttp
from aiohttp import ClientError
from aiohttp.client_exceptions import ClientConnectorError, ClientOSError
from aiohttp_socks import SocksVer, SocksError, SocksConnector, SocksConnectionError

from ...utils.geo_ip import IPLoc
from ...utils.rules import DownloadRuleMatch

from config import config

logger = logging.getLogger("Sub")

WORKERS = config["fileDownload"]["maxWorkers"]
BUFFER = config["fileDownload"]["buffer"]
_PERF = config.get("performance", {}) or {}
_AIO_TOTAL = float(_PERF.get("aio_timeout_seconds", 12))
_AIO_CONNECT = float(_PERF.get("aio_connect_timeout_seconds", 6))
_AIO_READ = float(_PERF.get("aio_read_timeout_seconds", 10))
SPEED_TEST_SECONDS = float(_PERF.get("speed_test_seconds", 8))
FETCH_TIMEOUT = aiohttp.ClientTimeout(
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

	async def record(self, received: int):
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
	#	print("\r[" + "=" * int(self._time_used // delta_time) + "> [{:.2f} MB/s]".format(speed_mb), end='')
		self._count += 1
		print("\r[" + "=" * self._count + "> [{:.2f} MB/s]".format(speed_mb), end='')
		self._speed_list.append(speed)

async def _fetch(url: str, sta: Statistics, host: str = "127.0.0.1", port: int = 1087):
	connector = SocksConnector(
		socks_ver=SocksVer.SOCKS5,
		host=host,
		port=port,
		rdns=True,
	)
	logger.info("Fetching %s via %s:%s.", url, host, port)
	try:
		async with aiohttp.ClientSession(
			connector=connector,
			headers={"User-Agent": "curl/11.45.14"},
			timeout=FETCH_TIMEOUT,
		) as session:
			logger.debug("Session created.")
			async with session.get(url) as response:
				logger.debug("Awaiting response.")
				while not sta.stopped:
					chunk = await response.content.read(BUFFER)
					if not chunk:
						logger.debug("Download stream ended.")
						break
					await sta.record(len(chunk))
	except (ClientOSError, ClientConnectorError, SocksError, SocksConnectionError, asyncio.TimeoutError, ConnectionResetError, OSError) as e:
		logger.warning("Download worker failed via %s:%s: %s", host, port, e)
	except ClientError as e:
		logger.warning("Download worker HTTP error via %s:%s: %s", host, port, e)
	except Exception:
		logger.exception("Download worker unexpected error via %s:%s", host, port)

def start(
	proxy_host = "127.0.0.1",
	proxy_port: int = 1087,
	workers: int = WORKERS
	):
	dlrm = DownloadRuleMatch()
	res = dlrm.get_url(IPLoc())
	url = res[0]
	file_size = res[1]
	logger.debug(f"Url: {url}, file_size: {file_size} MiB")
	logger.info(f"Running st_async, workers: {workers}")
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	_sta = Statistics()
	tasks = [
		loop.create_task(_fetch(url, _sta, proxy_host, proxy_port))
		for _ in range(workers)
	]
	try:
		results = loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
		failed = sum(1 for r in results if isinstance(r, Exception))
		if failed:
			logger.warning(
				"ST_ASYNC: %d/%d download worker(s) failed (proxy or target reset TLS/connection).",
				failed,
				workers,
			)
	finally:
		loop.close()
	_sta.show_progress_full()
	if _sta.time_used:
		return (_sta.total_red / _sta.time_used, _sta.max_speed, _sta.speed_list, _sta.total_red)
	else:
		return (0, 0, [], 0)


