#coding:utf-8

class Sorter(object):
	def __init__(self):
		pass

	def __sortBySpeed(self,result):
		return result["dspeed"]

	def __sortByPing(self,result):
		return result["ping"]

	def __normalize_sort_method(self, sortMethod):
		if sortMethod is None:
			return ""
		s = str(sortMethod).strip()
		if not s:
			return ""
		u = s.upper()
		if u == "RSPEED":
			return "REVERSE_SPEED"
		if u == "RPING":
			return "REVERSE_PING"
		if u in ("SPEED", "REVERSE_SPEED", "PING", "REVERSE_PING"):
			return u
		return ""

	def sortResult(self,result,sortMethod):
		# SPEED: 平均速度降序（大的在上、小的在下）；测速失败等 dspeed<0 会排在最末
		sm = self.__normalize_sort_method(sortMethod)
		if sm == "SPEED":
			result.sort(key=self.__sortBySpeed, reverse=True)
		elif sm == "REVERSE_SPEED":
			result.sort(key=self.__sortBySpeed)
		elif sm == "PING":
			result.sort(key=self.__sortByPing)
		elif sm == "REVERSE_PING":
			result.sort(key=self.__sortByPing, reverse=True)
		return result
