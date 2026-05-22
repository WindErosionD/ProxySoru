# -*- coding: utf-8 -*-

import socket


def check_port(port: int):
	"""连接 127.0.0.1:port。若端口有进程监听则正常返回；若被拒绝/超时等则抛出 OSError 子类。"""
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.settimeout(3)
	try:
		s.connect(("127.0.0.1", port))
	finally:
		try:
			s.shutdown(socket.SHUT_RDWR)
		except OSError:
			pass
		try:
			s.close()
		except OSError:
			pass

