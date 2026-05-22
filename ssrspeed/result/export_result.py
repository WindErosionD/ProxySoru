#coding:utf-8

import re
from PIL import Image,ImageDraw,ImageFont
import json
import os
import sys
import time
import logging
import requests
logger = logging.getLogger("Sub")

from .sorter import Sorter
from .exporters import ExporterWps

from config import config

'''
	resultJson
		{
			"group":"GroupName",
			"remarks":"Remarks",
			"loss":0,#Data loss (0-1)
			"ping":0.014,
			"gping":0.011,
			"dspeed":12435646 #Bytes
			"maxDSpeed":12435646 #Bytes
		}
'''
def sanitize_filename(name):
    	# 去除非法文件名字符（Windows/Unix 通用）
		return re.sub(r'[\\/:*?"<>|]', "_", name)

class ExportResult(object):
	def __init__(self):
		self.__config = config["exportResult"]
		self.__hide_max_speed = config["exportResult"]["hide_max_speed"]
		self.__hide_ntt = not config.get("ntt", {}).get("enabled", True)
		self.__hide_netflix = not config.get("netflix", True)
		self.__hide_bilibili = not config.get("bilibili", False)
		self.__hide_stream = not config.get("stream", False)
		self.__hide_stspeed = not config.get("StSpeed", True)
		self.__test_method = config.get("method", "ST_ASYNC")
		self.__hide_ping = not config.get("ping", True)
		self.__hide_gping = not config.get("gping", True)
		self.__hide_speed = not config.get("speed", True)
		self.__hide_port = not config.get("port", True)
		self.__hide_geoip = not config.get("geoip", False)
		self.__hide_multiplex = not config.get("multiplex", True)
		self.__colors = {}
		self.__colorSpeedList = []
		self.__font = ImageFont.truetype(self.__config["font"],18)
		self.__emoji_font = self.__font
		emoji_font_path = "C:/Windows/Fonts/seguiemj.ttf"
		if os.path.exists(emoji_font_path):
			try:
				self.__emoji_font = ImageFont.truetype(emoji_font_path, 18)
			except Exception:
				self.__emoji_font = self.__font
		self.__timeUsed = "N/A"
		self.__enable_topology = config.get("topology", {}).get("enabled", False)
	#	self.setColors()

	def setTopologyEnabled(self, enabled: bool):
		self.__enable_topology = bool(enabled)

	def setColors(self,name = "origin"):
		for color in self.__config["colors"]:
			if (color["name"] == name):
				logger.info("Set colors as {}.".format(name))
				self.__colors = color["colors"]
				self.__colorSpeedList.append(0)
				for speed in self.__colors.keys():
					try:
						self.__colorSpeedList.append(float(speed))
					except:
						continue
				self.__colorSpeedList.sort()
				return
		logger.warn("Color {} not found in config.".format(name))

	def setTimeUsed(self, timeUsed):
		self.__timeUsed = time.strftime("%H:%M:%S", time.gmtime(timeUsed))
		logger.info("Time Used : {}".format(self.__timeUsed))

	def __result_filename_suffix(self):
		raw = str(self.__config.get("filenameSuffix", "") or "").strip()
		if not raw:
			return ""
		safe = sanitize_filename(raw).strip("._- ")
		if not safe:
			return ""
		return "-" + safe

	def export(self,result,split = 0,exportType = 0,sortMethod = "SPEED"):
		sorter = Sorter()
		result = sorter.sortResult(result, sortMethod)
		if (not exportType):
			self.__exportAsJson(result)
		if not result:
			logger.info("No nodes were tested; skip result/topology image export.")
			return
		self.__exportAsPng(result)

	def exportWpsResult(self, result, exportType = 0):
		if not exportType:
			result = self.__exportAsJson(result)
		if not result:
			logger.info("No nodes were tested; skip WPS export.")
			return
		epwps = ExporterWps(result)
		epwps.export()

	def __getMaxWidth(self,result):
		font = self.__font
		draw = ImageDraw.Draw(Image.new("RGB",(1,1),(255,255,255)))
		maxGroupWidth = 0
		maxRemarkWidth = 0
		lenIn = 0
		lenOut = 0
		for item in result:
			group = item["group"]
			remark = item["remarks"]
			inres = item["InRes"]
			outres = item["OutRes"]
			#maxGroupWidth = max(maxGroupWidth,draw.textsize(group,font=font)[0])
			maxGroupWidth = max(maxGroupWidth, draw.textbbox((0, 0), group, font=font)[2])

			#maxRemarkWidth = max(maxRemarkWidth,draw.textsize(remark,font=font)[0])
			maxRemarkWidth = max(maxRemarkWidth,draw.textbbox((0, 0), remark, font=font)[2])
			#lenIn = max(lenIn, draw.textsize(inres, font=font)[0])
			#lenOut = max(lenOut, draw.textsize(outres, font=font)[0])
			lenIn=max(lenIn, draw.textbbox((0, 0), inres, font=font)[2])
			lenOut=max(lenOut, draw.textbbox((0, 0), outres, font=font)[2])
		return (maxGroupWidth + 10,maxRemarkWidth + 10,lenIn + 20,lenOut + 20)

	def __getMaxWidthStream(self,result):
		maxStreamWidth = 0
		for item in result:
			netflix_type = item["Ntype"]
			hbo_type = item["Htype"]
			disney_type = item["Dtype"]
			youtube_type = item["Ytype"]
			abema_type = item["Atype"]
			bahamut_type = item["Btype"]
			chatgpt_type = item["Ctype"]
			bilibili_type = item["Bltype"]
			tvb_type = item["Ttype"]
			if (netflix_type[:4] == "Full"):
				n_type = True
			else:
				n_type = False
			if (bilibili_type == "全解锁"):
				bl_type = True
			else:
				bl_type = False
			sums = n_type + hbo_type + disney_type + youtube_type + abema_type + bahamut_type + tvb_type + bl_type + chatgpt_type
			tmpWidth = sums * 35 + 20

			if tmpWidth > maxStreamWidth:
				maxStreamWidth = tmpWidth

		return maxStreamWidth
	
	'''
	def __deweighting(self,result):
		_result = []
		for r in result:
			isFound = False
			for i in range(0,len(_result)):
				_r = _result[i]
				if (_r["group"] == r["group"] and _r["remarks"] == r["remarks"]):
					isFound = True
					if (r["dspeed"] > _r["dspeed"]):
						_result[i] = r
					elif(r["ping"] < _r["ping"]):
						_result[i] = r
					break
			if (not isFound):
				_result.append(r)
		return _result
	'''

	def __getBasePos(self, width, text):
		font = self.__font
		draw = ImageDraw.Draw(Image.new("RGB",(1,1),(255,255,255)))
		#textSize = draw.textsize(text, font=font)[0]
		text_bbox = draw.textbbox((0, 0), text, font=font)
		text_width = text_bbox[2] - text_bbox[0]
		basePos = (width - text_width) / 2
		#basePos = (width - textSize) / 2
		logger.debug("Base Position {}".format(basePos))
		return basePos

	def __draw_text_with_emoji(self, draw, pos, text, fill=(0, 0, 0)):
		x, y = pos
		text = str(text)
		for ch in text:
			font = self.__emoji_font if (ord(ch) >= 0x1F000) else self.__font
			draw.text((x, y), ch, font=font, fill=fill)
			bbox = draw.textbbox((x, y), ch, font=font)
			x += max(1, bbox[2] - bbox[0])

	def __speed_fluctuation_rating(self, item: dict):
		"""
		速度波动评级策略（偏实际体验）:
		1) 仅使用有效测速采样点（rawSocketSpeed > 0）
		2) 计算均值、标准差、变异系数(CV=std/mean)、P90/P10 比值
		3) 引入丢包惩罚（loss/gPingLoss）
		4) 综合得分后映射到 低/中/高
		"""
		raw = item.get("rawSocketSpeed", []) or []
		speeds = [float(x) for x in raw if isinstance(x, (int, float)) and x > 0]
		if len(speeds) < 4:
			return "有效点不足"

		n = len(speeds)
		mean_speed = sum(speeds) / n
		if mean_speed <= 0:
			return "波动高"
		var = sum((x - mean_speed) ** 2 for x in speeds) / n
		std = var ** 0.5
		cv = std / mean_speed

		sorted_s = sorted(speeds)
		p10 = sorted_s[max(0, int(n * 0.1) - 1)]
		p90 = sorted_s[min(n - 1, int(n * 0.9))]
		spread = (p90 / max(p10, 1.0)) if p10 > 0 else 999.0

		loss = float(item.get("loss", 1))
		gloss = float(item.get("gPingLoss", 1))
		loss_penalty = max(loss, gloss)

		# Relaxed thresholds/weights: reduce false "high fluctuation" on short tests.
		score = cv * 0.52 + min(spread / 10.0, 1.0) * 0.16 + loss_penalty * 0.10
		if score >= 0.50:
			return "波动高"
		if score >= 0.30:
			return "波动中"
		return "波动低"

	def __safe_text(self, text, allow_emoji=True):
		s = str(text) if text is not None else ""
		if any(x in s for x in ("Ã", "Â", "â", "ð", "å", "æ", "ç")):
			for enc in ("utf-8", "gbk"):
				try:
					s2 = s.encode("latin1", "ignore").decode(enc, "ignore")
					if s2:
						s = s2
						break
				except Exception:
					continue
		s = re.sub(r"[\u0000-\u001f]", "", s)
		if not allow_emoji:
			s = re.sub(r"[\U00010000-\U0010ffff]", "", s)
		return s if s else "N/A"

	def __abbrev_protocol_display(self, raw):
		"""仅 SS / SSR / HY / HY2 四种写成缩写；其余与数据源一致（如 trojan、anytls）。"""
		s = self.__safe_text(raw, allow_emoji=False).strip()
		if s in ("N/A", "", "None"):
			return "N/A"
		key = re.sub(r"\s+", "", s).lower()
		if key in ("shadowsocks", "ss"):
			return "SS"
		if key in ("shadowsocksr", "ssr"):
			return "SSR"
		if key in ("hysteria2", "hy2"):
			return "HY2"
		if key in ("hysteria", "hy"):
			return "HY"
		return s

	def __protocol_column_pixel_width(self, result, font):
		"""协议列宽度：表头「协议」与各节点显示文本的最大像素宽度 + 边距。"""
		draw = ImageDraw.Draw(Image.new("RGB", (1, 1), (255, 255, 255)))
		label = "协议"

		def tw(text):
			t = self.__safe_text(text, allow_emoji=False)
			bb = draw.textbbox((0, 0), t, font=font)
			return max(0, bb[2] - bb[0])

		mw = tw(label)
		for item in result:
			cell = self.__abbrev_protocol_display(item.get("Protocol", "N/A"))
			mw = max(mw, tw(cell))
		return max(56, min(320, mw + 20))

	def __abbrev_ntt_display(self, raw):
		"""UDP NAT 类型缩写，避免列宽不够。"""
		s = str(raw or "").strip()
		if not s or s.lower() == "unknown":
			return "-"
		table = {
			"Open": "Open",
			"Blocked": "阻断",
			"UDP Firewall": "UDP墙",
			"Full-cone NAT": "全锥",
			"Restricted-cone NAT": "限锥",
			"Restricted-port NAT": "限端口",
			"Symmetric NAT": "对称",
		}
		if s in table:
			return table[s]
		return s

	def __fit_cell_text(self, draw, text, max_inner_width, font):
		"""将文本缩略到不超过 max_inner_width（像素）。"""
		t = self.__safe_text(text, allow_emoji=False)
		if draw.textbbox((0, 0), t, font=font)[2] <= max_inner_width:
			return t
		for i in range(len(t), 0, -1):
			cand = t[:i] + "…"
			if draw.textbbox((0, 0), cand, font=font)[2] <= max_inner_width:
				return cand
		return "…"

	def __exportAsPng(self,result):
		if self.__colorSpeedList == []:
			self.setColors()
		resultFont = self.__font
		generatedTime = time.localtime()
		row_h = 30
		header_top = 30
		header_h = 30
		footer_h = 60

		max_group_w, _, _, _ = self.__getMaxWidth(result)
		group_col_w = max(130, max_group_w + 16)
		protocol_col_w = self.__protocol_column_pixel_width(result, resultFont)

		def stream_cell(v):
			if v is None:
				return "N/A"
			if v is True:
				return "解锁"
			return "未解锁"

		def stream_color(v):
			if v is None:
				return (235, 240, 250)
			if v is True:
				return (220, 245, 220)
			return (255, 225, 225)

		def multiplex_status(item, all_items):
			inbound_ip = item.get("InIP", "N/A")
			outbound_ip = item.get("OutIP", "N/A")
			if outbound_ip == "N/A":
				return "未知"
			inbound_same = len([x for x in all_items if x.get("InIP", "N/A") == inbound_ip and inbound_ip != "N/A"])
			outbound_same = len([x for x in all_items if x.get("OutIP", "N/A") == outbound_ip])
			if inbound_same > 1 and outbound_same > 1:
				return "完全复用"
			if inbound_same <= 1 and outbound_same <= 1:
				return "无复用"
			if inbound_same > 1:
				return "中转复用"
			return "落地复用"

		columns = [
			("group", "Group", group_col_w),
			("remarks", "Remarks", 280),
			("protocol", "协议", protocol_col_w),
			("loss", "Loss", 80),
			("gping", "Google Ping", 110),
			("port", "Port", 70),
			("avg", "AvgSpeed", 110),
			("max", "MaxSpeed", 110),
			("fluctuation", "速度波动评级", 120),
			("nat", "UDP NAT", 72),
			("netflix", "Netflix", 150),
			("bilibili", "Bilibili", 100),
			("SvcGPT", "GPT", 70),
			("SvcClaude", "Claude", 80),
			("SvcGemini", "Gemini", 80),
			("SvcYoutube", "Youtube", 80),
			("SvcDisney", "Disney+", 80),
			("SvcPrimeVideo", "PrimeVideo", 95),
			("SvcHBO", "HBO", 70),
			("SvcBahamut", "台动画疯", 90),
			("SvcTiktok", "Tiktok", 80),
			("SvcSpotify", "Spotify", 80),
			("SvcSteam", "Steam", 80),
			("SvcIGAudio", "IG音频", 80),
			("multiplex", "复用检测", 100),
		]

		image_w = sum([x[2] for x in columns]) + 2
		image_h = header_top + header_h + len(result) * row_h + footer_h + 2
		resultImg = Image.new("RGB", (image_w, image_h), (255, 255, 255))
		draw = ImageDraw.Draw(resultImg)

		title = "便宜机场测速 With ProxySoru（ v{} ）".format(config["VERSION"])
		draw.text((self.__getBasePos(image_w, title), 4), title, font=resultFont, fill=(0, 0, 0))
		draw.line((0, header_top, image_w - 1, header_top), fill=(127, 127, 127), width=1)

		x = 1
		for _, label, width in columns:
			draw.rectangle((x, header_top + 1, x + width, header_top + header_h), outline=(127, 127, 127), width=1)
			draw.text((x + self.__getBasePos(width, label), header_top + 4), label, font=resultFont, fill=(0, 0, 0))
			x += width

		totalTraffic = 0
		onlineNode = 0
		for idx, item in enumerate(result):
			y0 = header_top + header_h + idx * row_h
			y1 = y0 + row_h
			totalTraffic += item.get("trafficUsed", 0) if item.get("trafficUsed", 0) > 0 else 0
			if (item.get("ping", 0) > 0 and item.get("gPing", 0) > 0) or item.get("dspeed", -1) > 0:
				onlineNode += 1

			x = 1
			row_values = {
				"group": self.__safe_text(item.get("group", "N/A")),
				"remarks": self.__safe_text(item.get("remarks", "N/A")),
				"protocol": self.__abbrev_protocol_display(item.get("Protocol", "N/A")),
				"loss": "{:.2f}%".format(item.get("loss", 1) * 100),
				"gping": "{:.2f}".format(item.get("gPing", 0) * 1000),
				"port": str(item.get("port", 0)),
				"avg": "N/A" if item.get("dspeed", -1) < 0 else self.__parseSpeed(item.get("dspeed", 0)),
				"max": "N/A" if item.get("maxDSpeed", -1) < 0 else self.__parseSpeed(item.get("maxDSpeed", 0)),
				"fluctuation": self.__speed_fluctuation_rating(item),
				"nat": self.__abbrev_ntt_display(item.get("ntt", {}).get("type", "") or ""),
				"netflix": item.get("Ntype", "None"),
				"bilibili": item.get("Bltype", "N/A"),
				"SvcGPT": stream_cell(item.get("SvcGPT")),
				"SvcClaude": stream_cell(item.get("SvcClaude")),
				"SvcGemini": stream_cell(item.get("SvcGemini")),
				"SvcYoutube": stream_cell(item.get("SvcYoutube")),
				"SvcDisney": stream_cell(item.get("SvcDisney")),
				"SvcPrimeVideo": stream_cell(item.get("SvcPrimeVideo")),
				"SvcHBO": stream_cell(item.get("SvcHBO")),
				"SvcBahamut": stream_cell(item.get("SvcBahamut")),
				"SvcTiktok": stream_cell(item.get("SvcTiktok")),
				"SvcSpotify": stream_cell(item.get("SvcSpotify")),
				"SvcSteam": stream_cell(item.get("SvcSteam")),
				"SvcIGAudio": stream_cell(item.get("SvcIGAudio")),
				"multiplex": multiplex_status(item, result),
			}

			for key, _, width in columns:
				bg = (255, 255, 255)
				if key in ("avg", "max"):
					raw_speed = item.get("dspeed", -1) if key == "avg" else item.get("maxDSpeed", -1)
					if raw_speed > 0:
						bg = self.__getColor(raw_speed)
				elif key == "fluctuation":
					grade = row_values["fluctuation"]
					if grade == "波动低":
						bg = (220, 245, 220)
					elif grade == "波动中":
						bg = (255, 245, 205)
					elif grade == "有效点不足":
						bg = (235, 240, 250)
					else:
						bg = (255, 225, 225)
				elif key.startswith("Svc"):
					bg = stream_color(item.get(key))
				draw.rectangle((x, y0, x + width, y1), fill=bg, outline=(127, 127, 127), width=1)
				txt = row_values[key]
				if key == "remarks":
					self.__draw_text_with_emoji(draw, (x + 4, y0 + 4), txt, fill=(0, 0, 0))
				elif key == "nat":
					txt = self.__fit_cell_text(draw, txt, max(8, width - 8), resultFont)
					draw.text((x + self.__getBasePos(width, txt), y0 + 4), txt, font=resultFont, fill=(0, 0, 0))
				else:
					draw.text((x + self.__getBasePos(width, txt), y0 + 4), txt, font=resultFont, fill=(0, 0, 0))
				x += width

		trafficUsed = "N/A" if totalTraffic < 0 else self.__parseTraffic(totalTraffic)
		footer_y = header_top + header_h + len(result) * row_h + 6
		draw.text((5, footer_y), "Traffic used: {}. Time used: {}. Online Node(s): [{}/{}]".format(
			trafficUsed, self.__timeUsed, onlineNode, len(result)
		), font=resultFont, fill=(0, 0, 0))
		draw.text((5, footer_y + 26), "测速频道：@Cheap_Proxy   Generated at {}".format(
			time.strftime("%Y-%m-%d %H:%M:%S", generatedTime)
		), font=resultFont, fill=(0, 0, 0))

		appendix = result[1] if len(result) > 1 else (result[0] if result else {"group": "DefaultGroup"})
		group_name = str(appendix.get("group", "DefaultGroup")).strip()
		if not group_name or group_name.upper() == "N/A":
			group_name = "DefaultGroup"
		group_name = sanitize_filename(group_name)
		os.makedirs("./results", exist_ok=True)
		time_suffix = time.strftime("%Y-%m-%d-%H-%M-%S", generatedTime)
		fn_suffix = self.__result_filename_suffix()
		filename = "./results/" + group_name + time_suffix + fn_suffix + ".png"
		resultImg.save(filename)
		logger.info("Result image saved as %s" % filename)

		if self.__enable_topology:
			self.__exportTopologyPng(result, group_name, generatedTime)

	def __exportTopologyPng(self, result, group_name, generated_time):
		row_h = 30
		header_top = 30
		header_h = 30
		section_gap = 18
		footer_h = 40

		def split_res(txt: str):
			s = self.__safe_text(txt, allow_emoji=False)
			parts = [x.strip() for x in s.split(",")]
			if len(parts) >= 2:
				return parts[0], parts[1]
			return (parts[0] if parts else "N/A"), "N/A"

		def mask_ip(ip: str):
			ip = self.__safe_text(ip, allow_emoji=False)
			if ":" in ip:
				parts = ip.split(":")
				return ":".join(parts[:2]) + ":*:*"
			parts = ip.split(".")
			if len(parts) == 4:
				return "{}.{}.***.**".format(parts[0], parts[1])
			return ip

		def as_consistency(in_asn: str, out_asn: str):
			in_asn = self.__safe_text(in_asn, allow_emoji=False)
			out_asn = self.__safe_text(out_asn, allow_emoji=False)
			if in_asn in ("N/A", "", "0") or out_asn in ("N/A", "", "0"):
				return "未知"
			if in_asn == out_asn:
				return "同AS"
			return "跨AS"

		def fit_text(draw, text, max_width, font):
			text = self.__safe_text(text, allow_emoji=False)
			if draw.textbbox((0, 0), text, font=font)[2] <= max_width - 8:
				return text
			for i in range(len(text), 0, -1):
				t = text[:i] + "..."
				if draw.textbbox((0, 0), t, font=font)[2] <= max_width - 8:
					return t
			return text[:1]

		def stretch_columns(cols, target_w, prefer_key):
			current_w = sum([x[2] for x in cols]) + 2
			if current_w >= target_w:
				return cols
			extra = target_w - current_w
			new_cols = []
			for key, label, width in cols:
				if key == prefer_key:
					new_cols.append((key, label, width + extra))
				else:
					new_cols.append((key, label, width))
			return new_cols

		entry_map = {}
		for item in result:
			in_ip = self.__safe_text(item.get("InIP", "N/A"), allow_emoji=False)
			region, org = split_res(item.get("InRes", "N/A"))
			out_region, _ = split_res(item.get("OutRes", "N/A"))
			if in_ip not in entry_map:
				entry_map[in_ip] = {
					"region": region,
					"org": org,
					"mask_ip": mask_ip(in_ip),
					"count": 0,
					"out_regions": set(),
				}
			entry_map[in_ip]["count"] += 1
			if out_region and out_region != "N/A":
				entry_map[in_ip]["out_regions"].add(out_region)

		entry_rows = []
		entry_index = {}
		for i, (in_ip, data) in enumerate(sorted(entry_map.items(), key=lambda x: -x[1]["count"]), start=1):
			entry_index[in_ip] = i
			entry_rows.append({
				"idx": str(i),
				"region": data["region"],
				"org": data["org"],
				"ipmask": data["mask_ip"],
				"nodes": str(data["count"]),
				"outcov": str(len(data["out_regions"])),
			})

		cluster_counter = {}
		for item in result:
			key = (mask_ip(item.get("OutIP", "N/A")), self.__safe_text(item.get("OutASN", "N/A"), allow_emoji=False))
			cluster_counter[key] = cluster_counter.get(key, 0) + 1

		exit_rows = []
		for i, item in enumerate(result, start=1):
			in_ip = self.__safe_text(item.get("InIP", "N/A"), allow_emoji=False)
			out_region, out_org = split_res(item.get("OutRes", "N/A"))
			out_mask = mask_ip(item.get("OutIP", "N/A"))
			out_asn = self.__safe_text(item.get("OutASN", "N/A"), allow_emoji=False)
			cluster_size = cluster_counter.get((out_mask, out_asn), 1)
			exit_rows.append({
				"idx": str(i),
				"entry": str(entry_index.get(in_ip, 0)),
				"region": out_region,
				"org": out_org,
				"outmask": out_mask,
				"asmatch": as_consistency(item.get("InASN", "N/A"), item.get("OutASN", "N/A")),
				"cluster": str(cluster_size),
				"name": self.__safe_text(item.get("remarks", "N/A"), allow_emoji=False),
			})

		entry_cols = [("idx", "序号", 60), ("region", "地区", 170), ("org", "组织", 290), ("ipmask", "入口IP段", 150), ("nodes", "节点数", 90), ("outcov", "出口覆盖", 90)]
		exit_cols = [("idx", "序号", 60), ("entry", "入口", 60), ("region", "地区", 90), ("org", "组织", 260), ("outmask", "出口IP段", 145), ("asmatch", "AS一致性", 95), ("cluster", "簇内节点数", 95), ("name", "节点名称", 230)]

		w1 = sum([x[2] for x in entry_cols]) + 2
		w2 = sum([x[2] for x in exit_cols]) + 2
		image_w = max(w1, w2)
		entry_cols = stretch_columns(entry_cols, image_w, "org")
		exit_cols = stretch_columns(exit_cols, image_w, "name")
		image_h = (
			header_top + header_h +
			28 + header_h + len(entry_rows) * row_h +
			section_gap +
			28 + header_h + len(exit_rows) * row_h +
			footer_h + 2
		)
		img = Image.new("RGB", (image_w, image_h), (248, 250, 255))
		draw = ImageDraw.Draw(img)

		draw.rectangle((0, 0, image_w - 1, header_top), fill=(35, 50, 80))
		title = "便宜机场测速 - 节点拓扑分析"
		draw.text((self.__getBasePos(image_w, title), 4), title, font=self.__font, fill=(245, 248, 255))

		y = header_top + 4
		draw.text((5, y), "入口分析", font=self.__font, fill=(35, 50, 80))
		y += 24
		x = 1
		for _, label, width in entry_cols:
			draw.rectangle((x, y, x + width, y + header_h), fill=(225, 235, 252), outline=(127, 127, 127), width=1)
			draw.text((x + self.__getBasePos(width, label), y + 4), label, font=self.__font, fill=(20, 35, 65))
			x += width
		y += header_h
		for i, row in enumerate(entry_rows):
			x = 1
			bg = (255, 255, 255) if i % 2 == 0 else (242, 247, 255)
			for key, _, width in entry_cols:
				draw.rectangle((x, y, x + width, y + row_h), fill=bg, outline=(127, 127, 127), width=1)
				txt = fit_text(draw, row[key], width, self.__font)
				draw.text((x + self.__getBasePos(width, txt), y + 4), txt, font=self.__font, fill=(0, 0, 0))
				x += width
			y += row_h

		y += section_gap
		draw.text((5, y), "出口分析", font=self.__font, fill=(35, 50, 80))
		y += 24
		x = 1
		for _, label, width in exit_cols:
			draw.rectangle((x, y, x + width, y + header_h), fill=(225, 235, 252), outline=(127, 127, 127), width=1)
			draw.text((x + self.__getBasePos(width, label), y + 4), label, font=self.__font, fill=(20, 35, 65))
			x += width
		y += header_h
		for i, row in enumerate(exit_rows):
			x = 1
			bg = (255, 255, 255) if i % 2 == 0 else (242, 247, 255)
			for key, _, width in exit_cols:
				draw.rectangle((x, y, x + width, y + row_h), fill=bg, outline=(127, 127, 127), width=1)
				txt = fit_text(draw, row[key], width, self.__font)
				draw.text((x + self.__getBasePos(width, txt), y + 4), txt, font=self.__font, fill=(0, 0, 0))
				x += width
			y += row_h

		draw.rectangle((0, image_h - footer_h, image_w - 1, image_h - 1), fill=(235, 240, 250))
		draw.text((5, image_h - 30), "Generated at {}".format(time.strftime("%Y-%m-%d %H:%M:%S", generated_time)), font=self.__font, fill=(35, 50, 80))
		filename = "./results/" + sanitize_filename(group_name) + time.strftime("%Y-%m-%d-%H-%M-%S", generated_time) + self.__result_filename_suffix() + "-topology.png"
		img.save(filename)
		logger.info("Topology image saved as %s" % filename)

	def __parseTraffic(self,traffic):
		traffic = traffic / 1024 / 1024
		if (traffic < 1):
			return("%.2f KB" % (traffic * 1024))
		gbTraffic = traffic / 1024
		if (gbTraffic < 1):
			return("%.2f MB" % traffic)
		return ("%.2f GB" % gbTraffic)

	def __parseSpeed(self,speed):
		speed = speed / 1024 / 1024
		if (speed < 1):
			return("%.2fKB" % (speed * 1024))
		else:
			return("%.2fMB" % speed)

	def __newMixColor(self,lc,rc,rt):
	#	print("RGB1 : {}, RGB2 : {}, RT : {}".format(lc,rc,rt))
		return (
			int(lc[0]*(1-rt)+rc[0]*rt),
			int(lc[1]*(1-rt)+rc[1]*rt),
			int(lc[2]*(1-rt)+rc[2]*rt)
		)

	def __getColor(self,data):
		if (self.__colorSpeedList == []):
			return (255,255,255)
		rt = 1
		curSpeed = self.__colorSpeedList[len(self.__colorSpeedList)-1]
		backSpeed = 0
		if (data >= curSpeed  * 1024 * 1024):
			return (self.__colors[str(curSpeed)][0],self.__colors[str(curSpeed)][1],self.__colors[str(curSpeed)][2])
		for i in range (0,len(self.__colorSpeedList)):
			curSpeed = self.__colorSpeedList[i] * 1024 * 1024
			if (i > 0):
				backSpeed = self.__colorSpeedList[i-1]
			backSpeedStr = str(backSpeed)
		#	print("{} {}".format(data/1024/1024,backSpeed))
			if (data < curSpeed):
				rgb1 = self.__colors[backSpeedStr] if backSpeed > 0 else (255,255,255)
				rgb2 = self.__colors[str(self.__colorSpeedList[i])]
				rt = (data - backSpeed * 1024 * 1024)/(curSpeed - backSpeed * 1024 * 1024)
				logger.debug("Speed : {}, RGB1 : {}, RGB2 : {}, RT : {}".format(data/1024/1024,rgb1,rgb2,rt))
				return self.__newMixColor(rgb1,rgb2,rt)
		return (255,255,255)

	

	def __exportAsJson(self, result):
		for item in result:
			group = item.get("group", "").strip()
			if not group or group.upper() == "N/A":
				group = "DefaultGroup"
			item["group"] = group

		appendix = result[1] if len(result) > 1 else (result[0] if result else {"group": "DefaultGroup"})
		group_name = appendix.get("group", "DefaultGroup").strip()
		if not group_name or group_name.upper() == "N/A":
			group_name = "DefaultGroup"

		# 清理非法文件名字符
		group_name = sanitize_filename(group_name) 

		filename = "./results/" + group_name + time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime()) + self.__result_filename_suffix() + ".json"

		# 确保目录存在
		os.makedirs("./results", exist_ok=True)

		with open(filename, "w+", encoding="utf-8") as f:
			f.writelines(json.dumps(result, sort_keys=True, indent=4, separators=(',', ':')))

		logger.info("Result exported as %s" % filename)
		return result

