import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time

REQ_MARKER_NAME = ".ssrspeed_req.sha256"

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)
from env_cache import cleanup_after_pip_install, pip_subprocess_env  # noqa: E402
from pip_requirements_install import PYNAT_SPEC  # noqa: E402


def _set_windows_console_codepage_utf8():
	"""与 bat 的 chcp 65001 双保险：部分环境仅改 Python 编码仍会在控制台乱码。"""
	if os.name != "nt":
		return
	try:
		import ctypes

		kernel32 = ctypes.windll.kernel32
		cp_utf8 = 65001
		kernel32.SetConsoleCP(cp_utf8)
		kernel32.SetConsoleOutputCP(cp_utf8)
	except (OSError, AttributeError):
		pass


def _configure_stdio_utf8():
	"""在 Windows 控制台（尤其已 chcp 65001 的 cmd）下避免中文变成问号。"""
	os.environ.setdefault("PYTHONIOENCODING", "utf-8")
	os.environ.setdefault("PYTHONUTF8", "1")
	if os.name == "nt":
		_set_windows_console_codepage_utf8()
	try:
		if hasattr(sys.stdout, "reconfigure"):
			sys.stdout.reconfigure(encoding="utf-8", errors="replace")
		if hasattr(sys.stderr, "reconfigure"):
			sys.stderr.reconfigure(encoding="utf-8", errors="replace")
	except (OSError, ValueError, AttributeError):
		pass


_configure_stdio_utf8()


def _open_utf8_log(path, mode="a"):
	"""UTF-8 日志；新文件写入 BOM，便于 Windows 记事本默认正确打开。"""
	new_file = mode == "w" or not os.path.isfile(path) or os.path.getsize(path) == 0
	fp = open(path, mode, encoding="utf-8", errors="replace", newline="")
	if new_file:
		fp.write("\ufeff")
	return fp

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(ROOT_DIR, ".venv")
PORTABLE_MARKER = os.path.join(ROOT_DIR, "PORTABLE_BUILD")
PORTABLE_PYTHON = os.path.join(ROOT_DIR, "python", "python.exe")

# bat 若用 >>log 2>&1 重定向子进程，控制台会全程黑屏且看不到 input 提示；改由本模块双写。
_BOOTSTRAP_TEE_HOLD = []


class _TeeTextStream:
	"""将 stdout/stderr 同时写到控制台与 SSR_BOOTSTRAP_LOG 指定文件（追加）。"""

	def __init__(self, primary, log_fp):
		self._primary = primary
		self._log = log_fp

	def write(self, data):
		if not data:
			return 0
		n = self._primary.write(data)
		self._log.write(data)
		self._log.flush()
		return n

	def flush(self):
		self._primary.flush()
		self._log.flush()

	def isatty(self):
		try:
			return self._primary.isatty()
		except (AttributeError, OSError):
			return False

	def __getattr__(self, name):
		return getattr(self._primary, name)


def _maybe_install_bootstrap_log_tee():
	raw = (os.environ.get("SSR_BOOTSTRAP_LOG") or "").strip()
	if not raw:
		return
	try:
		log_path = raw if os.path.isabs(raw) else os.path.join(ROOT_DIR, raw)
		d = os.path.dirname(log_path)
		if d:
			os.makedirs(d, exist_ok=True)
		log_fp = _open_utf8_log(log_path, "a")
		log_fp.write(
			"==== {} RUN_SPEED_TEST_ASCII ====\n".format(
				time.strftime("%Y/%m/%d %a %H:%M:%S", time.localtime())
			)
		)
		log_fp.flush()
	except Exception:
		return
	_BOOTSTRAP_TEE_HOLD.append(log_fp)
	sys.stdout = _TeeTextStream(sys.stdout, log_fp)
	sys.stderr = _TeeTextStream(sys.stderr, log_fp)


_maybe_install_bootstrap_log_tee()


def _write_boot_diag(line: str) -> None:
	"""写入诊断行，便于 bat 闪退时查看 logs/bootstrap_diag.txt。"""
	try:
		d = os.path.join(ROOT_DIR, "logs")
		os.makedirs(d, exist_ok=True)
		p = os.path.join(d, "bootstrap_diag.txt")
		with open(p, "a", encoding="utf-8", errors="replace") as f:
			f.write(line + "\n")
	except Exception:
		pass


_write_boot_diag("module load ROOT_DIR=%r argv=%r" % (ROOT_DIR, sys.argv))


def _is_portable_distribution():
	"""由 tools/build_portable.py 生成的目录：内置 embeddable Python，无需本机 venv/pip。"""
	return (
		os.name == "nt"
		and os.path.isfile(PORTABLE_MARKER)
		and os.path.isfile(PORTABLE_PYTHON)
	)


def _venv_python_path():
	if _is_portable_distribution():
		return PORTABLE_PYTHON
	if os.name == "nt":
		return os.path.join(VENV_DIR, "Scripts", "python.exe")
	return os.path.join(VENV_DIR, "bin", "python")


VENV_PYTHON = _venv_python_path()


def run(cmd, cwd=None):
	work = cwd or ROOT_DIR
	env = os.environ.copy()
	# 子进程 cwd 与 PYTHONPATH 固定为项目根，避免便携包/快捷方式在非项目目录启动时找不到 clients、
	# mihomo 写到错误路径、SOCKET 测速一直为 0。
	env["PYTHONPATH"] = work
	return subprocess.call(cmd, cwd=work, env=env)


def run_quiet(cmd, cwd=None):
	return subprocess.run(cmd, cwd=cwd or ROOT_DIR, capture_output=True, text=True)


def _subprocess_env():
	env = pip_subprocess_env()
	# 尽量显示 pip 下载进度条（旧版 pip 会忽略未知变量）
	env.setdefault("PIP_PROGRESS_BAR", "on")
	return env


def run_live(cmd, cwd=None):
	"""子进程输出直接打到控制台，避免长时间无输出被误认为卡住。"""
	return subprocess.run(cmd, cwd=cwd or ROOT_DIR, env=_subprocess_env())


def run_live_capture(cmd, cwd=None):
	"""同 run_live，但合并捕获输出供失败时写入 pip_install_last.log。"""
	env = _subprocess_env()
	proc = subprocess.Popen(
		cmd,
		cwd=cwd or ROOT_DIR,
		env=env,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		encoding="utf-8",
		errors="replace",
	)
	chunks = []
	if proc.stdout:
		for line in proc.stdout:
			print(line, end="", flush=True)
			chunks.append(line)
	code = proc.wait()
	out = "".join(chunks)

	class _Result:
		pass

	r = _Result()
	r.returncode = code
	r.stdout = out
	r.stderr = ""
	return r


def _run_with_heartbeat(cmd, cwd=None, label="执行中", interval_sec=12):
	"""对可能长时间无输出的子进程（如 python -m venv）周期性提示，避免用户以为卡死。"""
	stop = threading.Event()

	def _beat():
		n = 0
		while not stop.wait(interval_sec):
			n += 1
			elapsed = n * interval_sec
			print(
				"[{}] 仍在运行… 已约 {} 秒（创建环境/复制文件可能较慢，请勿关闭窗口）".format(label, elapsed),
				flush=True,
			)

	t = threading.Thread(target=_beat, daemon=True)
	t.start()
	try:
		code = subprocess.call(cmd, cwd=cwd or ROOT_DIR)
	finally:
		stop.set()
		t.join(timeout=2.0)
	return code


def _log_phase(step, total, title, detail=""):
	line = "[{}/{}] {}".format(step, total, title)
	if detail:
		line += " — " + detail
	print(line, flush=True)


def _bootstrap_auto():
	"""由一键测速.bat 设置，或 stdin 非终端（被重定向）时自动重试安装，不等待 input。"""
	if os.environ.get("SSR_BOOTSTRAP_AUTO", "").strip().lower() in ("1", "true", "yes", "on"):
		return True
	try:
		return not sys.stdin.isatty()
	except (AttributeError, OSError):
		return True


def _stdin_interactive():
	"""stdin 是否为可交互终端（双击 bat 一般为 True；被重定向/管道则为 False）。"""
	try:
		return sys.stdin is not None and sys.stdin.isatty()
	except (AttributeError, OSError):
		return False


def _try_attach_windows_console_stdin():
	"""从一键测速.bat 启动时，部分环境下 sys.stdin 不是 TTY，会被误判为不可交互而立刻退出；改绑控制台输入。"""
	if os.name != "nt":
		return
	if not os.environ.get("SSR_BOOTSTRAP_AUTO", "").strip().lower() in ("1", "true", "yes", "on"):
		return
	try:
		if sys.stdin is not None and sys.stdin.isatty():
			return
	except (AttributeError, OSError):
		pass
	try:
		sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
	except OSError:
		try:
			sys.stdin = open("CON:", "r", encoding="utf-8", errors="replace")
		except OSError:
			pass


def _pause_before_close(seconds, msg):
	"""避免窗口一闪而过：自动安装模式下用倒计时，否则尽量等待按键。"""
	print(msg, flush=True)
	if _bootstrap_auto():
		try:
			time.sleep(seconds)
		except KeyboardInterrupt:
			pass
		return
	try:
		input("按回车关闭窗口…")
	except (EOFError, OSError):
		try:
			time.sleep(min(seconds, 15))
		except KeyboardInterrupt:
			pass


def _pip_index_tuna():
	return "https://pypi.tuna.tsinghua.edu.cn/simple"


def _pip_install_log_path():
	d = os.path.join(ROOT_DIR, "logs")
	os.makedirs(d, exist_ok=True)
	return os.path.join(d, "pip_install_last.log")


def _write_pip_install_log(label: str, cmd, result) -> None:
	"""pip 失败时写入完整输出，便于用户用 UTF-8 打开排查（控制台可能乱码）。"""
	try:
		with open(_pip_install_log_path(), "a", encoding="utf-8", errors="replace", newline="\n") as f:
			f.write("\n==== {} ====\n".format(label))
			f.write("cmd: {}\n".format(" ".join(str(x) for x in cmd)))
			f.write("returncode: {}\n".format(getattr(result, "returncode", "?")))
			for stream_name in ("stdout", "stderr"):
				text = getattr(result, stream_name, None) or ""
				if text.strip():
					f.write("[{}]\n{}\n".format(stream_name, text))
	except OSError:
		pass


def progress(label, step, total):
	bar_len = 24
	done = int(bar_len * step / total)
	bar = "=" * done + " " * (bar_len - done)
	percent = int(step * 100 / total)
	print("[{}] {:>3}% {}".format(bar, percent, label))


def _env_bar(pct, note=""):
	"""单行环境检测进度（用 \\r 刷新，避免刷屏）。"""
	pct = max(0, min(100, int(pct)))
	width = 28
	filled = max(0, min(width, int(round(width * pct / 100.0))))
	bar = "=" * filled + "-" * (width - filled)
	msg = "环境检测"
	if note:
		msg += " " + note
	line = "\r[{}] {:>3}%  {}".format(bar, pct, msg)
	print(line.ljust(86), end="", flush=True)


def _env_bar_nl():
	print(flush=True)


def _env_detection_bar_sequence():
	"""环境正常时仅展示简短进度动画，不打印其它检测文案。"""
	for p in (18, 42, 68, 92, 100):
		_env_bar(p, "")
		time.sleep(0.04)
	_env_bar_nl()


def run_pip_silent(cmd, cwd=None):
	"""pip 静默安装/升级：成功时不刷屏，失败时由调用方打印 stderr。"""
	cmd = list(cmd)
	try:
		i = cmd.index("pip") + 1
		if i < len(cmd) and cmd[i] != "-q":
			cmd.insert(i, "-q")
	except ValueError:
		pass
	env = _subprocess_env()
	env["PIP_PROGRESS_BAR"] = "off"
	return subprocess.run(
		cmd,
		cwd=cwd or ROOT_DIR,
		env=env,
		capture_output=True,
		text=True,
		encoding="utf-8",
		errors="replace",
	)


def _fingerprint_requirements():
	req = os.path.join(ROOT_DIR, "requirements.txt")
	if not os.path.isfile(req):
		return ""
	with open(req, "rb") as f:
		return hashlib.sha256(f.read()).hexdigest()


def _req_marker_path():
	return os.path.join(VENV_DIR, REQ_MARKER_NAME)


def _read_req_marker():
	p = _req_marker_path()
	if not os.path.isfile(p):
		return None
	try:
		with open(p, "r", encoding="ascii") as f:
			return f.read().strip()
	except OSError:
		return None


def _write_req_marker(digest):
	if not digest or not os.path.isdir(VENV_DIR):
		return
	try:
		with open(_req_marker_path(), "w", encoding="ascii") as f:
			f.write(digest)
	except OSError:
		pass


def _venv_python_quick_ok():
	if not os.path.isfile(VENV_PYTHON):
		return False
	return run_quiet([VENV_PYTHON, "-c", "import sys"]).returncode == 0


def _env_ready_fast():
	"""venv 可用且 requirements.txt 指纹与上次成功安装一致时，跳过环境准备输出与 pip。"""
	fp = _fingerprint_requirements()
	if not fp:
		return False
	if not _venv_python_quick_ok():
		return False
	return _read_req_marker() == fp


def _venv_python_usable(quiet=False, compact=False):
	"""虚拟环境存在且其解释器可运行（旧机器路径失效时需重建 .venv）。"""
	if not os.path.isfile(VENV_PYTHON):
		return False
	if not quiet and not compact:
		_log_phase(1, 4, "检查已有虚拟环境", "验证 .venv 内 Python 是否可用…")
	check = run_quiet([VENV_PYTHON, "-c", "import sys"])
	ok = check.returncode == 0
	if not quiet and not compact:
		if ok:
			print("[结果] 现有 .venv 正常，无需重建。", flush=True)
		else:
			print("[结果] 现有 .venv 已失效（例如曾移动过系统 Python），将重新创建。", flush=True)
	return ok


def ensure_venv(quiet=False, compact=False):
	if _venv_python_usable(quiet=quiet, compact=compact):
		if not quiet and not compact:
			progress("检测测试环境", 1, 4)
		return True
	if os.path.isdir(VENV_DIR):
		shutil.rmtree(VENV_DIR, ignore_errors=True)
	if not compact:
		progress("检测测试环境", 1, 4)
	else:
		_env_bar_nl()
		print("[环境] 正在创建虚拟环境 .venv（首次或环境失效，可能 1～3 分钟）…", flush=True)
	if not compact:
		_log_phase(
			1,
			4,
			"创建虚拟环境",
			"目录：.venv（此步骤通常无下载百分比，若较久属正常；下方会每隔约 {} 秒提示一次）".format(12),
		)
	creator = shutil.which("py")
	if creator:
		for ver_flag in ("-3.11", "-3.12", "-3.10", "-3"):
			if not compact:
				_log_phase(1, 4, "创建虚拟环境", "尝试命令：py {} -m venv …".format(ver_flag))
			t0 = time.monotonic()
			code = _run_with_heartbeat(
				[creator, ver_flag, "-m", "venv", VENV_DIR],
				label="创建 .venv",
			)
			dt = int(time.monotonic() - t0)
			if code == 0 and os.path.isfile(VENV_PYTHON):
				if not compact:
					print("[结果] 虚拟环境创建成功（py {}，耗时约 {} 秒）。".format(ver_flag, dt), flush=True)
				return True
			if not compact:
				print("[结果] py {} 创建失败（退出码 {}），将尝试下一版本。".format(ver_flag, code), flush=True)
	if not compact:
		_log_phase(1, 4, "创建虚拟环境", "尝试使用当前解释器：{} -m venv …".format(sys.executable))
	t0 = time.monotonic()
	code = _run_with_heartbeat([sys.executable, "-m", "venv", VENV_DIR], label="创建 .venv")
	dt = int(time.monotonic() - t0)
	ok = code == 0 and os.path.isfile(VENV_PYTHON)
	if ok:
		if not compact:
			print("[结果] 虚拟环境创建成功（当前解释器，耗时约 {} 秒）。".format(dt), flush=True)
	else:
		print("[结果] 虚拟环境创建失败（退出码 {}）。".format(code), flush=True)
	return ok


def _upgrade_pip_chain(compact=False):
	"""依次尝试默认源、清华镜像、修复 setuptools 后再试。"""
	if not compact:
		print("", flush=True)
		_log_phase(2, 4, "升级 pip", "接下来 pip 会打印下载进度条/百分比，请稍候")
	steps = [
		("默认 PyPI", [VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip"]),
		("清华镜像", [VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "--upgrade", "pip"]),
	]
	last = None
	for name, cmd in steps:
		if not compact:
			print("", flush=True)
			print("[pip] 尝试来源：{} — 正在升级 pip（下方为实时输出）…".format(name), flush=True)
			last = run_live(cmd)
		else:
			last = run_pip_silent(cmd)
		if last.returncode == 0:
			if not compact:
				print("[结果] pip 升级成功（来源：{}）。".format(name), flush=True)
			return True
		if not compact:
			print("[结果] 本次失败（退出码 {}），将自动换其它方式重试。".format(last.returncode), flush=True)
	if not compact:
		print("", flush=True)
		print("[pip] 正在升级 pip / setuptools / wheel / six 以修复常见安装问题…", flush=True)
		run_live([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "six"])
	else:
		run_pip_silent([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "six"])
	for name, cmd in steps:
		if not compact:
			print("", flush=True)
			print("[pip] 修复工具链后重试：{} …".format(name), flush=True)
			last = run_live(cmd)
		else:
			last = run_pip_silent(cmd)
		if last.returncode == 0:
			if not compact:
				print("[结果] pip 升级成功（来源：{}）。".format(name), flush=True)
			return True
		if not compact:
			print("[结果] 仍失败（退出码 {}）。".format(last.returncode), flush=True)
	if last is not None:
		err = (getattr(last, "stderr", None) or getattr(last, "stdout", None) or "")[-1200:]
		if err and err.strip():
			print("[pip] 末次错误摘要：", flush=True)
			print(err, flush=True)
	return False


def _install_requirements_txt_chain(compact=False):
	req = os.path.join(ROOT_DIR, "requirements.txt")
	try:
		with _open_utf8_log(_pip_install_log_path(), "w") as f:
			f.write("pip install requirements.txt log\n")
	except OSError:
		pass
	if not compact:
		print("", flush=True)
		_log_phase(3, 4, "安装项目依赖", "文件：requirements.txt — pip 将逐包显示下载进度")
	# 一键测速（SSR_BOOTSTRAP_AUTO）面向国内用户居多：优先清华，再回退官方 PyPI
	if _bootstrap_auto():
		steps = [
			("清华镜像", [VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req]),
			("默认 PyPI", [VENV_PYTHON, "-m", "pip", "install", "-r", req]),
		]
	else:
		steps = [
			("默认 PyPI", [VENV_PYTHON, "-m", "pip", "install", "-r", req]),
			("清华镜像", [VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req]),
		]
	last = None
	_pip_run = run_pip_silent if compact else run_live_capture
	for name, cmd in steps:
		if not compact:
			print("", flush=True)
			print("[pip] 尝试来源：{} — 正在安装依赖（下方为实时输出）…".format(name), flush=True)
		last = _pip_run(cmd)
		if last.returncode == 0:
			if not compact:
				print("[结果] 依赖安装成功（来源：{}）。".format(name), flush=True)
			return True
		if not compact:
			print("[结果] 本次失败（退出码 {}），将自动换镜像或修复后再试。".format(last.returncode), flush=True)
		_write_pip_install_log("install requirements ({})".format(name), cmd, last)
	if not compact:
		print("", flush=True)
		print(
			"[pip] 依赖仍失败：升级构建工具后安装 six，并单独构建 pynat（需 six），再安装 requirements…",
			flush=True,
		)
		run_live_capture(
			[VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "six"]
		)
		run_live_capture(
			[VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), PYNAT_SPEC, "--no-build-isolation"]
		)
		last = run_live_capture([VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req])
		if last.returncode != 0:
			last = run_live_capture(
				[
					VENV_PYTHON,
					"-m",
					"pip",
					"install",
					"-i",
					_pip_index_tuna(),
					"-r",
					req,
					"--no-build-isolation",
				]
			)
	else:
		run_pip_silent([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "six"])
		run_pip_silent(
			[VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), PYNAT_SPEC, "--no-build-isolation"]
		)
		last = run_pip_silent([VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req])
		if last.returncode != 0:
			last = run_pip_silent(
				[
					VENV_PYTHON,
					"-m",
					"pip",
					"install",
					"-i",
					_pip_index_tuna(),
					"-r",
					req,
					"--no-build-isolation",
				]
			)
	if last.returncode != 0:
		repair_cmd = [VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req]
		_write_pip_install_log("install requirements (toolchain repair)", repair_cmd, last)
	if last.returncode == 0:
		if not compact:
			print("[结果] 依赖安装成功（清华镜像 + 工具链修复后）。", flush=True)
		return True
	err = ""
	if last is not None:
		err = (getattr(last, "stderr", None) or getattr(last, "stdout", None) or "")[-1500:]
	if err:
		print("[pip] 末次错误摘要：", flush=True)
		print(err, flush=True)
	log_path = _pip_install_log_path()
	print(
		"[pip] 完整安装日志已写入（请用记事本「UTF-8」打开）：{}".format(log_path),
		flush=True,
	)
	print(
		"[提示] 若含 curl-cffi / 编译相关错误，请安装「Visual C++ 2015–2022 x64 运行库」；"
		"或改用发布方提供的便携版（含已装好的 python 目录，无需 pip）。",
		flush=True,
	)
	return False


def install_requirements(compact=False):
	try:
		return _install_requirements_impl(compact)
	finally:
		if os.path.isfile(VENV_PYTHON):
			cleanup_after_pip_install(VENV_PYTHON, VENV_DIR)


def _install_requirements_impl(compact=False):
	if not compact:
		progress("更新 pip", 2, 4)
		print("", flush=True)
		print("[说明] 第 2 步会升级 pip；第 3 步会安装 requirements.txt。两步都会显示 pip 自带进度。", flush=True)
	if not _upgrade_pip_chain(compact=compact):
		print("[环境检测失败] pip 升级失败（已自动换镜像与修复工具链仍无效）。")
		if not _bootstrap_auto():
			print("快捷键: [R] 重试  [M] 使用清华镜像重试  [Q] 退出")
			choice = input("请选择: ").strip().lower()
			if choice == "m":
				if run_live([VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "--upgrade", "pip"]).returncode != 0:
					return False
			elif choice == "r":
				if run_live([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip"]).returncode != 0:
					return False
			else:
				return False
		else:
			return False
	if not compact:
		progress("安装依赖", 3, 4)
	if _install_requirements_txt_chain(compact=compact):
		_write_req_marker(_fingerprint_requirements())
		return True
	print("[环境检测失败] 依赖安装失败。")
	if not _bootstrap_auto():
		print("快捷键: [R] 重试  [M] 使用清华镜像安装  [P] 修复 pip/setuptools  [Q] 退出")
		choice = input("请选择: ").strip().lower()
		req = os.path.join(ROOT_DIR, "requirements.txt")
		if choice == "m":
			ok = run_live([VENV_PYTHON, "-m", "pip", "install", "-i", _pip_index_tuna(), "-r", req]).returncode == 0
			if ok:
				_write_req_marker(_fingerprint_requirements())
			return ok
		if choice == "p":
			run_live([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "six"])
			run_live(
				[VENV_PYTHON, "-m", "pip", "install", PYNAT_SPEC, "--no-build-isolation"]
			)
			ok = run_live([VENV_PYTHON, "-m", "pip", "install", "-r", req]).returncode == 0
			if ok:
				_write_req_marker(_fingerprint_requirements())
			return ok
		if choice == "r":
			ok = run_live([VENV_PYTHON, "-m", "pip", "install", "-r", req]).returncode == 0
			if ok:
				_write_req_marker(_fingerprint_requirements())
			return ok
		return False
	return False


def run_main(quiet=False, skip_start_banner=False):
	_try_attach_windows_console_stdin()
	if not quiet and not skip_start_banner:
		progress("启动测速", 4, 4)
	extra_args = sys.argv[1:]
	if extra_args:
		if not quiet and not skip_start_banner:
			_log_phase(4, 4, "启动测速", "命令行参数已传入，直接运行 main.py …")
		args = [VENV_PYTHON, os.path.join(ROOT_DIR, "main.py")] + extra_args
		return run(args)

	if not _stdin_interactive():
		print("", flush=True)
		print(
			"[错误] 当前无法交互输入（stdin 不是终端，可能被重定向或由非控制台方式启动）。",
			flush=True,
		)
		print("请在本目录双击运行「一键测速.bat」，或在 CMD 中执行：", flush=True)
		print("  py -3 tools\\bootstrap_and_run.py", flush=True)
		_pause_before_close(
			30,
			"[提示] 若仍无法输入，请勿用「运行」对话框直接运行本 .py；窗口将稍后关闭以便阅读上述说明。",
		)
		return 1

	if not quiet and not skip_start_banner:
		_log_phase(4, 4, "启动测速", "环境就绪；接下来请在提示下输入订阅链接等信息。")
	elif quiet or skip_start_banner:
		# 快速检测路径常 quiet+skip：进度条后若不再打印，易被误认为闪退；此处给固定可见行
		print("", flush=True)
		print(
			"[OK] Environment ready. / 环境就绪，请按下方英文或中文提示输入（订阅链接必填）。",
			flush=True,
		)
		print("", flush=True)
	while True:
		sub_url = input("请输入订阅链接（必填，输入 e/q 退出）: ").strip()
		if sub_url.lower() in ("e", "q"):
			return 0
		while not sub_url:
			sub_url = input("订阅链接不能为空，请重新输入（输入 e/q 退出）: ").strip()
			if sub_url.lower() in ("e", "q"):
				return 0

		test_name = input("请输入测速名称（选填，用于结果文件名）: ").strip()
		while True:
			topo_raw = input("是否开启拓扑测试？(y/n): ").strip()
			if topo_raw == "":
				enable_topology = False
				break
			topo = topo_raw.lower()
			if topo in ("y", "n"):
				enable_topology = (topo == "y")
				break
			print("输入无效，请输入 y 或 n。")

		extra_args = ["-u", sub_url, "-y", "--skip-requirements-check", "-m", "stasync", "-M", "all"]
		if test_name:
			extra_args.extend(["-g", test_name])
		if enable_topology:
			extra_args.append("--topology")

		args = [VENV_PYTHON, os.path.join(ROOT_DIR, "main.py")] + extra_args
		code = run(args)
		print("")
		print("本次测速已结束（退出码: {}）。".format(code))
		choice = input("按回车继续下一条，输入 e/q 退出: ").strip().lower()
		if choice in ("e", "q"):
			return 0


def main():
	_configure_stdio_utf8()
	if _bootstrap_auto() and os.environ.get("SSR_BOOTSTRAP_LOG"):
		print("[编码] 控制台与日志已使用 UTF-8（代码页 65001）。", flush=True)
	_write_boot_diag("main() enter portable=%s" % (_is_portable_distribution(),))
	if _is_portable_distribution():
		return run_main(quiet=True, skip_start_banner=True)

	ready = _env_ready_fast()
	if ready:
		_env_detection_bar_sequence()
		if not ensure_venv(quiet=True, compact=True):
			print("", flush=True)
			print(
				"[环境] 快速检测通过但虚拟环境仍需修复，将分步显示处理过程（含 pip 下载进度），请勿关闭窗口。",
				flush=True,
			)
			print("", flush=True)
			if not ensure_venv(quiet=False, compact=False):
				print("[环境检测失败] 无法创建虚拟环境（.venv）。")
				print("建议：确认本机已安装 Python 3.10 或更高版本，并关闭 Windows「应用执行别名」里的 python 占位项后，重新运行一键测速。")
				return 1
			if not install_requirements(compact=False):
				print("[环境检测失败] 无法完成依赖包安装（pip）。")
				print("请打开 logs\\pip_install_last.log（UTF-8）查看 pip 具体报错，或向发布方索取便携版。")
				return 1
			print("", flush=True)
			print("[环境] 依赖与虚拟环境已就绪。", flush=True)
			print("", flush=True)
			return run_main(quiet=False, skip_start_banner=True)
		return run_main(quiet=True, skip_start_banner=True)

	# 需要创建/修复 venv 或重装依赖：逐步打印阶段说明与 pip 实时输出，避免用户干等无反馈
	print("", flush=True)
	print(
		"[环境] 检测到需要准备运行环境（新建或修复 .venv、升级 pip、安装依赖等），将分步显示进度；"
		"下载包时会出现 pip 百分比/进度条，请勿关闭窗口。",
		flush=True,
	)
	print("", flush=True)
	if not ensure_venv(quiet=False, compact=False):
		print("[环境检测失败] 无法创建虚拟环境（.venv）。")
		print("建议：确认本机已安装 Python 3.10 或更高版本，并关闭 Windows「应用执行别名」里的 python 占位项后，重新运行一键测速。")
		return 1
	if not install_requirements(compact=False):
		print("[环境检测失败] 无法完成依赖包安装（pip）。")
		print("请打开 logs\\pip_install_last.log（UTF-8）查看 pip 具体报错，或向发布方索取便携版。")
		return 1
	print("", flush=True)
	print("[环境] 依赖与虚拟环境已就绪。", flush=True)
	print("", flush=True)
	return run_main(quiet=False, skip_start_banner=True)


if __name__ == "__main__":
	try:
		sys.exit(main())
	except KeyboardInterrupt:
		print("\n已取消。", flush=True)
		sys.exit(130)
	except EOFError:
		print("", flush=True)
		print(
			"[错误] 输入流已结束（常见于无控制台 stdin、管道或自动化环境）。请双击「一键测速.bat」运行。",
			flush=True,
		)
		_pause_before_close(25, "[提示] 请改用「一键测速.bat」或在 CMD 中运行；窗口将稍后关闭。")
		sys.exit(1)
	except Exception:
		import traceback

		traceback.print_exc()
		print("", flush=True)
		print("启动失败：请将上方报错截图或复制后排查。", flush=True)
		_pause_before_close(60, "[提示] 阅读上方报错后按回车关闭，或稍等自动结束。")
		sys.exit(1)
