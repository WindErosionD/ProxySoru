# 供「一键测速.bat」在需要时静默安装 Python（失败时由 bat 提示手动安装）
$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "[Python 自动安装] 将通过官网直链下载安装包（若某一版本不可用会自动换下一个）。"
Write-Host "[Python 自动安装] 下载时 PowerShell 可能显示进度条；安装阶段窗口可能数分钟无新输出，属正常现象。"
Write-Host ""
$versions = @(
	"3.12.8", "3.12.7", "3.12.6",
	"3.11.11", "3.11.10", "3.11.9"
)
$dst = Join-Path $env:TEMP "ssrspeed-python-setup.exe"
foreach ($v in $versions) {
	$url = "https://www.python.org/ftp/python/$v/python-$v-amd64.exe"
	try {
		Write-Host "[下载] 正在请求: $url"
		Write-Host "[下载] 保存到: $dst （文件约 25MB+，请耐心等待）"
		Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
		Write-Host "[安装] 下载完成，正在静默运行官方安装程序（可能 1～3 分钟无新输出，请勿关闭）…"
		$p = Start-Process -FilePath $dst -ArgumentList @(
			"/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
			"Include_test=0", "SimpleInstall=1"
		) -Wait -PassThru
		if ($p.ExitCode -ne 0) {
			throw "安装程序退出码: $($p.ExitCode)"
		}
		Write-Host "[成功] 已安装 Python $v（已写入用户 PATH，并包含 py 启动器）"
		exit 0
	} catch {
		Write-Host "[跳过] $($_.Exception.Message)"
	}
}
Write-Host "[失败] 未能下载或安装列表中的任一 Python 版本，请检查网络或换用手动安装。"
exit 1
