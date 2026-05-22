#coding:utf-8

import logging
import copy
import socket
import socks
import time
import json
import requests
import concurrent.futures
from bs4 import BeautifulSoup
try:
	import pynat
except ImportError:
	pynat = None

logger = logging.getLogger("Sub")

from .test_methods import SpeedTestMethods
from ..client_launcher import MihomoClient
from ..utils.geo_ip import domain2ip, parseLocation, IPLoc
from ..utils.port_checker import check_port

from config import config

LOCAL_ADDRESS = config["localAddress"]
LOCAL_PORT = config["localPort"]
PING_TEST = config.get("ping", False)
GOOGLE_PING_TEST = config.get("gping", True)
NETFLIX_TEST = config.get("netflix", True)
HBO_TEST = config.get("hbo", True)
DISNEY_TEST = config.get("disney", True)
YOUTUBE_TEST = config.get("youtube", True)
TVB_TEST = config.get("tvb", False)
ABEMA_TEST = config.get("abema", False)
BAHAMUT_TEST = config.get("bahamut", True)
BILIBILI_TEST = config.get("bilibili", False)
CHATGPT_TEST = config.get("chatgpt", True)
CLAUDE_TEST = config.get("claude", True)
GEMINI_TEST = config.get("gemini", True)
PRIMEVIDEO_TEST = config.get("primevideo", True)
TIKTOK_TEST = config.get("tiktok", True)
SPOTIFY_TEST = config.get("spotify", True)
STEAM_TEST = config.get("steam", True)
IG_AUDIO_TEST = config.get("ig_audio", True)
ntype = "None"
htype = False
dtype = False
ytype = False
ttype = False
atype = False
btype = False
ctype = False
bltype = "N/A"
inboundGeoRES = ""
outboundGeoRES = ""
inboundGeoIP = ""
outboundGeoIP = ""
service_status = {
	"GPT": None,
	"Claude": None,
	"Gemini": None,
	"Youtube": None,
	"Disney": None,
	"PrimeVideo": None,
	"HBO": None,
	"Bahamut": None,
	"Tiktok": None,
	"Spotify": None,
	"Steam": None,
	"IGAudio": None
}


def _ntt_guess_local_ipv4(route_dst=("1.1.1.1", 53)):
	"""本机默认路由 IPv4；绑定在 0.0.0.0 时用于展示与传给 pynat，避免其另开直连 UDP 推断。"""
	t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		t.connect(route_dst)
		return t.getsockname()[0]
	finally:
		t.close()


def _ntt_bind_host(internal_ip):
	if not internal_ip or internal_ip in ("0.0.0.0", "::"):
		return ""
	return internal_ip


class SpeedTest(object):
	def __init__(self, parser, method = "ST_ASYNC", enable_topology = False):
		self.__configs = parser.nodes
		self.__testMethod = method
		self.__enable_topology = enable_topology
		perf_cfg = config.get("performance", {})
		self.__http_timeout = int(perf_cfg.get("http_timeout", 6))
		self.__node_interval = float(perf_cfg.get("node_interval", 0.2))
		self.__service_workers = int(perf_cfg.get("service_workers", 8))
		self.__client_poll = float(perf_cfg.get("client_ready_poll", 0.2))
		self.__client_max_wait = float(perf_cfg.get("client_ready_max_wait", 2.5))
		self.__port_poll = float(perf_cfg.get("port_check_poll", 0.2))
		self.__port_max_wait = float(perf_cfg.get("port_check_max_wait", 2.0))
		self.__speed_zero_retry = bool(perf_cfg.get("speed_zero_retry", False))
		self.__results = []
		self.__current = {}
		self.__baseResult = {
			"group": "N/A",
			"remarks": "N/A",
			"loss": 1,
			"ping": 0,
			"gPingLoss": 1,
			"gPing": 0,
			"dspeed": -1,
			"maxDSpeed": -1,
			"trafficUsed": 0,
			"geoIP":{
				"inbound":{
					"address": "N/A",
					"info": "N/A"
				},
				"outbound":{
					"address": "N/A",
					"info": "N/A"
				}
			},
			"rawSocketSpeed": [],
			"rawTcpPingStatus": [],
			"rawGooglePingStatus": [],
			"webPageSimulation":{
				"results":[]
			},
			"ntt": {
				"type": "",
				"internal_ip": "",
				"internal_port": 0,
				"public_ip": "",
				"public_port": 0
			},
            "Ntype": "None",
			"Htype": False,
			"Dtype": False,
			"Ytype": False,
			"Ttype": False,
			"Atype": False,
			"Btype": False,
			"Ctype": False,
			"Bltype":"N/A",
			"InRes":"N/A",
			"OutRes":"N/A",
			"InIP":"N/A",
			"OutIP":"N/A",
			"InASN":"N/A",
			"OutASN":"N/A",
			"port": 0,
			"Protocol": "N/A",
			"SvcGPT": None,
			"SvcClaude": None,
			"SvcGemini": None,
			"SvcYoutube": None,
			"SvcDisney": None,
			"SvcPrimeVideo": None,
			"SvcHBO": None,
			"SvcBahamut": None,
			"SvcTiktok": None,
			"SvcSpotify": None,
			"SvcSteam": None,
			"SvcIGAudio": None,
		}

	def __getBaseResult(self):
		return copy.deepcopy(self.__baseResult)

	def __get_next_config(self):
		try:
			return self.__configs.pop(0)
		except IndexError:
			return None
	
	def __wait_client_alive(self, client, cfg):
		deadline = time.time() + self.__client_max_wait
		time.sleep(min(0.15, self.__client_poll))
		while time.time() < deadline:
			if client.check_alive():
				return True
			try:
				client.startClient(cfg)
			except Exception:
				logger.exception("Client restart failed.")
			time.sleep(self.__client_poll)
		return False

	def __wait_local_port(self):
		deadline = time.time() + self.__port_max_wait
		while time.time() < deadline:
			try:
				check_port(LOCAL_PORT)
				return True
			except socket.timeout:
				logger.debug("Port %s not ready yet.", LOCAL_PORT)
			except ConnectionRefusedError:
				logger.debug("Port %s connection refused.", LOCAL_PORT)
			except Exception:
				logger.exception("Port check error on %s.", LOCAL_PORT)
			time.sleep(self.__port_poll)
		return False

	def __build_service_check_map(self):
		"""按配置开关组装流媒体/AI 检测项，关闭项不发起 HTTP。"""
		candidates = [
			("GPT", CHATGPT_TEST, "https://chat.openai.com/cdn-cgi/trace", []),
			("Claude", CLAUDE_TEST, "https://claude.ai/login", ["access denied", "forbidden"]),
			("Gemini", GEMINI_TEST, "https://gemini.google.com", ["not available"]),
			("Youtube", YOUTUBE_TEST, "https://music.youtube.com", ["not available"]),
			("Disney", DISNEY_TEST, "https://www.disneyplus.com", ["not available"]),
			("PrimeVideo", PRIMEVIDEO_TEST, "https://www.primevideo.com", ["not available"]),
			("HBO", HBO_TEST, "https://www.hbomax.com", ["not available"]),
			("Bahamut", BAHAMUT_TEST, "https://ani.gamer.com.tw", ["403"]),
			("Tiktok", TIKTOK_TEST, "https://www.tiktok.com", ["unavailable"]),
			("Spotify", SPOTIFY_TEST, "https://open.spotify.com", ["not available"]),
			("Steam", STEAM_TEST, "https://store.steampowered.com", ["unavailable"]),
		]
		return {
			key: (url, kw)
			for key, enabled, url, kw in candidates
			if enabled
		}

	def __get_client(self, client_type: str):
		# 所有协议统一由 Mihomo 内核承载（见 client_mihomo / mihomo_proxy_adapt）。
		if client_type in ("Shadowsocks", "ShadowsocksR", "V2Ray", "Trojan", "Mihomo"):
			return MihomoClient()
		return None

	def resetStatus(self):
		self.__results = []
		self.__current = {}

	def getResult(self):
		return self.__results
	
	def getCurrent(self):
		return self.__current

	def getResponse(self, url):
		response = 0
		try:
			if type(url) == type(""):
				headers = {
					"User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.119 Safari/537.36"}
				response = requests.get(url, proxies={
					"http": "socks5h://127.0.0.1:%d" % LOCAL_PORT,
					"https": "socks5h://127.0.0.1:%d" % LOCAL_PORT
				}, headers=headers, timeout=self.__http_timeout)
			else:
				headers = {
					"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36"}
				response = requests.get(url[0], proxies={
					"http": "socks5h://127.0.0.1:%d" % LOCAL_PORT,
					"https": "socks5h://127.0.0.1:%d" % LOCAL_PORT
				}, headers=headers, timeout=self.__http_timeout, cookies=url[1])

		except Exception as e:
			logger.error('代理服务器连接异常：' + str(e.args))

		return response

	def __service_check(self, url: str, ok_status=(200, 204, 301, 302), forbidden_keywords=None):
		"""返回 True=解锁, False=未解锁, None=无法完成检测（代理/网络异常等）"""
		resp = self.getResponse(url)
		if resp == 0:
			return None
		try:
			if resp.status_code not in ok_status:
				return False
			text = resp.text[:3000] if hasattr(resp, "text") else ""
			for keyword in (forbidden_keywords or []):
				if keyword and keyword.lower() in text.lower():
					return False
			return True
		except Exception:
			return None

	def __netflix_unlock_test(self, outbound_ip: str):
		if outbound_ip == "N/A":
			return "Unknown"
		try:
			with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
				f1 = executor.submit(self.getResponse, "https://www.netflix.com/title/70242311")
				f2 = executor.submit(self.getResponse, "https://www.netflix.com/title/70143836")
				r1 = f1.result()
				r2 = f2.result()
			if r1 == 0 or r2 == 0:
				return "Unknown"
			sum_ok = 0
			netflix_ip = ""
			if r1.status_code == 200:
				sum_ok += 1
				soup = BeautifulSoup(r1.text, "html.parser")
				netflix_ip_str = str(soup.find_all("script"))
				p1 = netflix_ip_str.find("requestIpAddress")
				if p1 >= 0:
					netflix_ip_r = netflix_ip_str[p1 + 19:p1 + 60]
					p2 = netflix_ip_r.find(",")
					netflix_ip = netflix_ip_r[0:p2] if p2 >= 0 else ""
			rg = ""
			if r2.status_code == 200:
				sum_ok += 1
				rg = r2.url.split("com/")[1].split("/")[0]
				if rg != "title":
					rg = "(" + str.upper(rg[:2]) + ")"
				else:
					rg = ""
			if sum_ok == 0:
				return "None"
			if sum_ok == 1:
				return "Only Original"
			if outbound_ip == netflix_ip and netflix_ip:
				return "Full Native" + rg
			return "Full DNS" + rg
		except Exception:
			logger.exception("Netflix test failed.")
			return "Unknown"

	def __bilibili_unlock_test(self, outbound_ip: str):
		if outbound_ip == "N/A":
			return "N/A"
		try:
			r1 = self.getResponse("https://api.bilibili.com/pgc/player/web/playurl?avid=18281381&cid=29892777&qn=0&type=&otype=json&ep_id=183799&fourk=1&fnver=0&fnval=16")
			r2 = self.getResponse("https://api.bilibili.com/pgc/player/web/playurl?avid=50762638&cid=100279344&qn=0&type=&otype=json&ep_id=268176&fourk=1&fnver=0&fnval=16")
			sumb = 0
			bl = "N/A"
			if r1 != 0 and r2 != 0:
				if r1.text.count("抱歉您所在地区不可观看") == 0:
					bl = "仅限港澳台"
					sumb += 1
				if r2.text.count("抱歉您所在地区不可观看") == 0:
					bl = "仅限台湾"
					sumb += 1
				if sumb == 2:
					bl = "全解锁"
				if sumb == 0:
					bl = "N/A"
			return bl
		except Exception:
			logger.exception("Bilibili test failed.")
			return "N/A"

	def __geoIPInbound(self,config):
		inboundIP = domain2ip(config["server"])
		global inboundGeoIP
		inboundGeoIP = inboundIP
		inboundInfo = IPLoc(inboundIP)
		inboundGeo = "{} {}, {}".format(
			inboundInfo.get("country","N/A"),
			inboundInfo.get("city","Unknown City"),
			inboundInfo.get("organization","N/A")
		)
		global inboundGeoRES
		inboundGeoRES = "{}, {}".format(
			inboundInfo.get("city","Unknown City"),
			inboundInfo.get("organization", "N/A")
		)
		logger.info(
			"Node inbound IP : {}, Geo : {}".format(
				inboundIP,
				inboundGeo
			)
		)
		return (inboundIP,inboundGeo,inboundInfo.get("country_code", "N/A"), inboundInfo)

	def __geoIPOutbound(self):
		outboundInfo = IPLoc()
		outboundIP = outboundInfo.get("ip","N/A")
		global outboundGeoIP
		outboundGeoIP = outboundIP
		outboundGeo = "{} {}, {}".format(
			outboundInfo.get("country","N/A"),
			outboundInfo.get("city","Unknown City"),
			outboundInfo.get("organization","N/A")
		)
		global outboundGeoRES
		outboundGeoRES = "{}, {}".format(
			outboundInfo.get("country_code", "N/A"),
			outboundInfo.get("organization", "N/A")
		)
		logger.info(
			"Node outbound IP : {}, Geo : {}".format(
				outboundIP,
				outboundGeo
			)
		)

		global ntype
		global htype
		global dtype
		global ytype
		global ttype
		global atype
		global btype
		global ctype
		global bltype
		global service_status

		ntype = "None"
		htype = False
		dtype = False
		ytype = False
		ttype = False
		atype = False
		btype = False
		ctype = False
		bltype = "N/A"
		service_status = {
			"GPT": None,
			"Claude": None,
			"Gemini": None,
			"Youtube": None,
			"Disney": None,
			"PrimeVideo": None,
			"HBO": None,
			"Bahamut": None,
			"Tiktok": None,
			"Spotify": None,
			"Steam": None,
			"IGAudio": None
		}
		if outboundIP == "N/A":
			ntype = "Unknown"
			bltype = "N/A"
			for k in list(service_status.keys()):
				service_status[k] = None
		else:
			ntype = "Unknown" if NETFLIX_TEST else "None"
			bltype = "N/A"
			check_map = self.__build_service_check_map()
			with concurrent.futures.ThreadPoolExecutor(max_workers=self.__service_workers) as executor:
				futures = {}
				if NETFLIX_TEST:
					futures["_netflix"] = executor.submit(self.__netflix_unlock_test, outboundIP)
				if BILIBILI_TEST:
					futures["_bilibili"] = executor.submit(self.__bilibili_unlock_test, outboundIP)
				for key, val in check_map.items():
					futures[key] = executor.submit(
						self.__service_check, val[0], forbidden_keywords=val[1]
					)
				if IG_AUDIO_TEST:
					try:
						from ..utils.ig_audio_check import probe_instagram_licensed_audio
					except Exception:
						logger.exception("IG audio check module failed to load; skipping.")
					else:
						futures["IGAudio"] = executor.submit(
							probe_instagram_licensed_audio,
							LOCAL_PORT,
							float(self.__http_timeout),
						)
				for key, future in futures.items():
					try:
						result = future.result()
					except Exception:
						result = None
					if key == "_netflix":
						ntype = result if result is not None else "Unknown"
					elif key == "_bilibili":
						bltype = result if result is not None else "N/A"
					else:
						service_status[key] = result

		return (outboundIP, outboundGeo, outboundInfo.get("country_code", "N/A"), outboundInfo)


	def __tcpPing(self, server, port):
		res = {
			"loss": self.__baseResult["loss"],
			"ping": self.__baseResult["ping"],
			"rawTcpPingStatus": self.__baseResult["rawTcpPingStatus"],
			"gPing": self.__baseResult["gPing"],
			"gPingLoss": self.__baseResult["gPingLoss"],
			"rawGooglePingStatus": self.__baseResult["rawGooglePingStatus"]
		}

		latencyTest = (0, 0, [])
		if PING_TEST:
			st = SpeedTestMethods()
			latencyTest = st.tcpPing(server, port)
			res["loss"] = 1 - latencyTest[1]
			res["ping"] = latencyTest[0]
			res["rawTcpPingStatus"] = latencyTest[2]
			logger.debug(latencyTest)
			time.sleep(0.1)

		if ((not PING_TEST) or (latencyTest[0] > 0)):
			if GOOGLE_PING_TEST:
				try:
					st = SpeedTestMethods()
					googlePingTest = st.googlePing()
					res["gPing"] = googlePingTest[0]
					res["gPingLoss"] = 1 - googlePingTest[1]
					res["rawGooglePingStatus"] = googlePingTest[2]
				except:
					logger.exception("")
					pass

		# When TCP ping is disabled/failed but proxy path is alive, fallback to Google ping loss.
		# This avoids showing fixed 100% loss on UDP-oriented nodes while speed test succeeds.
		if GOOGLE_PING_TEST and res["gPing"] > 0 and (not PING_TEST or res["ping"] <= 0):
			res["loss"] = res["gPingLoss"]
		return res

	def __nat_type_test(self):
		if not pynat:
			logger.warning("pynat is not installed, skip NAT type test.")
			return None, None, None, None, None

		ntt = config.get("ntt") or {}
		preferred_port = int(ntt.get("internal_port", 54320))
		internal_ip = ntt.get("internal_ip", "0.0.0.0")
		stun_host = ntt.get("stun_host") or None
		stun_port = int(ntt.get("stun_port", 3478))
		bind_host = _ntt_bind_host(internal_ip)

		s = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
		s.set_proxy(socks.PROXY_TYPE_SOCKS5, LOCAL_ADDRESS, LOCAL_PORT)
		try:
			try:
				s.bind((bind_host, preferred_port))
			except OSError as ex:
				logger.warning(
					"UDP NAT bind %s:%s failed (%s); retry with ephemeral port.",
					bind_host or "0.0.0.0",
					preferred_port,
					ex,
				)
				s.bind((bind_host, 0))

			bound_ip, bound_port = s.getsockname()
			if bound_ip in ("0.0.0.0", "", "::"):
				source_ip_for_pynat = _ntt_guess_local_ipv4()
				display_ip = source_ip_for_pynat
			else:
				source_ip_for_pynat = bound_ip
				display_ip = bound_ip

			logger.info("Performing UDP NAT Type Test (STUN via SOCKS5)")
			t, eip, eport, _sip = pynat.get_ip_info(
				source_ip=source_ip_for_pynat,
				source_port=int(bound_port),
				stun_host=stun_host,
				stun_port=stun_port,
				include_internal=True,
				sock=s,
			)
			return t, eip, eport, display_ip, int(bound_port)
		except Exception:
			logger.exception("UDP NAT type test failed.")
			return None, None, None, None, None
		finally:
			s.close()

	
	def __start_test(self, test_mode = "FULL"):
		self.__results = []
		total_nodes = len(self.__configs)
		done_nodes = 0
		shared_client = MihomoClient()
		node = self.__get_next_config()
		try:
			while node:
				done_nodes += 1
				_item = self.__getBaseResult()
				client = None
				should_append = True
				try:
					cfg = node.config
					logger.info(
						"Starting test {group} - {remarks} [{cur}/{tol}]".format(
							group = cfg["group"],
							remarks = cfg["remarks"],
							cur = done_nodes,
							tol = total_nodes
						)
					)
					if node.node_type not in ("Shadowsocks", "ShadowsocksR", "V2Ray", "Trojan", "Mihomo"):
						logger.warning(f"Unknown Node Type: {node.node_type}")
						should_append = False
						node = self.__get_next_config()
						continue
					client = shared_client
					_item["group"] = cfg.get("group", "N/A")
					_item["remarks"] = cfg.get("remarks", cfg.get("name", "N/A"))
					_item["Protocol"] = str(cfg.get("type", node.node_type))
					self.__current = _item
					cfg["server_port"] = int(cfg.get("server_port", cfg.get("port", 0)))
					_item["port"] = cfg["server_port"]
					client.startClient(cfg)

					if not self.__wait_client_alive(client, cfg):
						logger.error("Failed to start client.")
						continue
					logger.info("Client started.")

					if not self.__wait_local_port():
						logger.error("Port {} closed.".format(LOCAL_PORT))
						continue

					inboundInfo = self.__geoIPInbound(cfg)
					_item["geoIP"]["inbound"]["address"] = inboundInfo[0]
					_item["geoIP"]["inbound"]["info"] = inboundInfo[1]
					with concurrent.futures.ThreadPoolExecutor(max_workers=2) as exe:
						f_ping = exe.submit(self.__tcpPing, cfg["server"], cfg["server_port"])
						f_outbound = exe.submit(self.__geoIPOutbound)
						pingResult = f_ping.result()
						outboundInfo = f_outbound.result()
					if (isinstance(pingResult, dict)):
						for k in pingResult.keys():
							_item[k] = pingResult[k]
					_item["geoIP"]["outbound"]["address"] = outboundInfo[0]
					_item["geoIP"]["outbound"]["info"] = outboundInfo[1]

					# FULL 下载测速：端口已通且 speed 开启则必跑，不再因 Google ping / 出口 GeoIP 跳过。
					_tcp_ok = PING_TEST and (_item.get("ping") or 0) > 0
					_speed_on = bool(config.get("speed", True))
					if test_mode == "FULL" and _speed_on:
						_run_node_tests = True
					else:
						_run_node_tests = (
							(not GOOGLE_PING_TEST)
							or (_item.get("gPing") or 0) > 0
							or outboundInfo[2] == "CN"
							or _tcp_ok
						)
					if not _run_node_tests:
						logger.warning(
							"[{}] - [{}] skipped tests: Google ping failed, outbound not CN, TCP ping off/failed.".format(
								_item["group"], _item["remarks"]
							)
						)
					if _run_node_tests:
						st = SpeedTestMethods()
						if test_mode == "WPS":
							res = st.startWpsTest()
							_item["webPageSimulation"]["results"] = res
							logger.info("[{}] - [{}] - Loss: [{:.2f}%] - TCP Ping: [{:.2f}] - Google Loss: [{:.2f}%] - Google Ping: [{:.2f}] - [WebPageSimulation]".format
								(
									_item["group"],
									_item["remarks"],
									_item["loss"] * 100,
									int(_item["ping"] * 1000),
									_item["gPingLoss"] * 100,
									int(_item["gPing"] * 1000)
								)
							)
						elif test_mode == "PING":
							nat_info = ""
							if config["ntt"]["enabled"] and pynat:
								t, eip, eport, sip, sport = self.__nat_type_test()
								_item["ntt"]["type"] = t
								_item["ntt"]["internal_ip"] = sip
								_item["ntt"]["internal_port"] = sport
								_item["ntt"]["public_ip"] = eip
								_item["ntt"]["public_port"] = eport

								if t:
									nat_info += " - NAT Type: " + t
									if t != pynat.BLOCKED:
										nat_info += " - Internal End: {}:{}".format(sip, sport)
										nat_info += " - Public End: {}:{}".format(eip, eport)
							elif config["ntt"]["enabled"] and not pynat:
								nat_info += " - NAT Type: N/A (pynat missing)"

							logger.info("[{}] - [{}] - Loss: [{:.2f}%] - TCP Ping: [{:.2f}] - Google Loss: [{:.2f}%] - Google Ping: [{:.2f}]{}".format
								(
									_item["group"],
									_item["remarks"],
									_item["loss"] * 100,
									int(_item["ping"] * 1000),
									_item["gPingLoss"] * 100,
									int(_item["gPing"] * 1000),
									nat_info
								)
							)

						elif test_mode == "FULL":
							client.flush_connections()
							nat_info = ""
							ntt_enabled = bool(config.get("ntt", {}).get("enabled"))
							nat_result = None
							testRes = None
							with concurrent.futures.ThreadPoolExecutor(max_workers=2) as exe:
								f_speed = exe.submit(st.startTest, self.__testMethod)
								f_nat = None
								if ntt_enabled and pynat:
									f_nat = exe.submit(self.__nat_type_test)
								elif ntt_enabled and not pynat:
									nat_info += " - NAT Type: N/A (pynat missing)"
								testRes = f_speed.result()
								if f_nat is not None:
									nat_result = f_nat.result()
							if nat_result is not None:
								t, eip, eport, sip, sport = nat_result
								_item["ntt"]["type"] = t
								_item["ntt"]["internal_ip"] = sip
								_item["ntt"]["internal_port"] = sport
								_item["ntt"]["public_ip"] = eip
								_item["ntt"]["public_port"] = eport
								if t:
									nat_info += " - NAT Type: " + t
									if t != pynat.BLOCKED:
										nat_info += " - Internal End: {}:{}".format(sip, sport)
										nat_info += " - Public End: {}:{}".format(eip, eport)
							if testRes is None:
								logger.warning("startTest returned None; treating as zero speed.")
								testRes = (0, 0, [], 0)
							if self.__speed_zero_retry and int(testRes[0]) == 0:
								logger.warning("Re-testing node.")
								testRes = st.startTest(self.__testMethod) or (0, 0, [], 0)
							global ntype
							global htype
							global dtype
							global ytype
							global ttype
							global atype
							global btype
							global ctype
							global bltype
							global inboundGeoRES
							global outboundGeoRES
							global inboundGeoIP
							global outboundGeoIP
							_item["dspeed"] = testRes[0]
							_item["maxDSpeed"] = testRes[1]
							_item["Ntype"] = ntype
							_item["Htype"] = htype
							_item["Dtype"] = dtype
							_item["Ytype"] = ytype
							_item["Ttype"] = ttype
							_item["Atype"] = atype
							_item["Btype"] = btype
							_item["Ctype"] = ctype
							_item["Bltype"] = bltype
							_item["InRes"] = inboundGeoRES
							_item["OutRes"] = outboundGeoRES
							_item["InIP"] = inboundGeoIP
							_item["OutIP"] = outboundGeoIP
							_item["InASN"] = str(inboundInfo[3].get("asn", "N/A")) if len(inboundInfo) > 3 else "N/A"
							_item["OutASN"] = str(outboundInfo[3].get("asn", "N/A")) if len(outboundInfo) > 3 else "N/A"
							_item["SvcGPT"] = service_status["GPT"]
							_item["SvcClaude"] = service_status["Claude"]
							_item["SvcGemini"] = service_status["Gemini"]
							_item["SvcYoutube"] = service_status["Youtube"]
							_item["SvcDisney"] = service_status["Disney"]
							_item["SvcPrimeVideo"] = service_status["PrimeVideo"]
							_item["SvcHBO"] = service_status["HBO"]
							_item["SvcBahamut"] = service_status["Bahamut"]
							_item["SvcTiktok"] = service_status["Tiktok"]
							_item["SvcSpotify"] = service_status["Spotify"]
							_item["SvcSteam"] = service_status["Steam"]
							_item["SvcIGAudio"] = service_status["IGAudio"]
							try:
								_item["trafficUsed"] = testRes[3]
								_item["rawSocketSpeed"] = testRes[2]
							except:
								pass

							logger.info("[{}] - [{}] - Loss: [{:.2f}%] - TCP Ping: [{:.2f}] - Google Loss: [{:.2f}%] - Google Ping: [{:.2f}] - AvgStSpeed: [{:.2f}MB/s] - AvgMtSpeed: [{:.2f}MB/s]{}".format
								(
									_item["group"],
									_item["remarks"],
									_item["loss"] * 100,
									int(_item["ping"] * 1000),
									_item["gPingLoss"] * 100,
									int(_item["gPing"] * 1000),
									_item["dspeed"] / 1024 / 1024,
									_item["maxDSpeed"] / 1024 / 1024,
									nat_info
								)
							)
						else:
							logger.error(f"Unknown Test Mode {test_mode}")
				except Exception:
					logger.exception("\n")
				finally:
					if should_append:
						self.__results.append(_item)
					if client:
						client.afterNode()
					node = self.__get_next_config()
					time.sleep(self.__node_interval)
		finally:
			shared_client.stopClient()

		self.__current = {}

	def webPageSimulation(self):
		logger.info("Test mode : Web Page Simulation")
		self.__start_test("WPS")

	def tcpingOnly(self):
		logger.info("Test mode : tcp ping only.")
		self.__start_test("PING")

	def fullTest(self):
		logger.info("Test mode : speed and tcp ping.Test method : {}.".format(self.__testMethod))
		self.__start_test("FULL")

