# STM32 嵌入式工具箱
# 由 嵌入式工具箱.bat 调用（ASCII bat + PowerShell 处理中文，零乱码）
# 本文件必须以 UTF-8 with BOM 保存，否则 PS 5.1 会把中文解成乱码

$ErrorActionPreference = "Continue"
# 统一编码：否则 Python 用 GBK 输出中文，PowerShell 按 UTF-8 读会匹配不到路径
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"
$Root   = "D:\STM32_AI_Tools"
$Py     = "$Root\venvs\emb\Scripts\python.exe"
$Bin    = "$Root\bin"
$ProjRoot = "D:\STM32_Project"
$UV4    = "D:\keil5\UV4\UV4.exe"

function Pause-It {
    Write-Host ""
    Read-Host "  回车返回菜单" | Out-Null
}

function Get-Projects {
    $r = @()
    if (Test-Path $ProjRoot) {
        $r = Get-ChildItem -Path $ProjRoot -Directory -ErrorAction SilentlyContinue |
             Where-Object { Test-Path (Join-Path $_.FullName "Project") } |
             Sort-Object Name | Select-Object -ExpandProperty Name
    }
    return $r
}

function Pick-Project([string]$title, [string]$needFile) {
    $list = Get-Projects
    if ($list.Count -eq 0) {
        Write-Host "  在 $ProjRoot 下没找到任何工程。" -ForegroundColor Red
        return $null
    }
    while ($true) {
        Clear-Host
        Write-Host "==================================================" -ForegroundColor Cyan
        Write-Host "  $title" -ForegroundColor Cyan
        Write-Host "==================================================" -ForegroundColor Cyan
        Write-Host ""
        $i = 1
        foreach ($p in $list) {
            $mark = ""
            if ($needFile -ne "") {
                $f = Get-ChildItem -Path (Join-Path $ProjRoot $p) -Filter $needFile -Recurse -ErrorAction SilentlyContinue
                if ($f) { $mark = "  [有 $needFile]" } else { $mark = "  [缺 $needFile，先编译一次]" }
            }
            Write-Host ("   [{0,2}] {1}{2}" -f $i, $p, $mark)
            $i++
        }
        Write-Host "   [ 0] 返回"
        Write-Host ""
        $sel = Read-Host "  输入编号"
        if ($sel -eq "0" -or [string]::IsNullOrWhiteSpace($sel)) { return $null }
        $n = 0
        if ([int]::TryParse($sel, [ref]$n)) {
            if ($n -ge 1 -and $n -le $list.Count) { return $list[$n - 1] }
        }
        Write-Host "  编号不对，重新输入。" -ForegroundColor Yellow
        Start-Sleep -Milliseconds 900
    }
}

function Open-Report([string]$path) {
    if (Test-Path $path) {
        Write-Host ""
        Write-Host "  正在打开报告 ..." -ForegroundColor Green
        Start-Process $path
    } else {
        Write-Host "  报告没生成成功。" -ForegroundColor Red
    }
}

# ---------------------------------------------------------------- 1. 串口画图

function Do-SerialPlot {
    Clear-Host
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  串口画图 —— 读串口，自动出曲线" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  当前串口列表：" -ForegroundColor White
    Write-Host ""
    & $Py "$Bin\serial_plot.py" --list
    Write-Host ""
    Write-Host "  直接回车 = 自动挑 USB 串口（会跳过蓝牙虚拟口）" -ForegroundColor DarkGray
    $port = Read-Host "  串口号（如 COM9，可留空）"
    $sec = Read-Host "  采集几秒（直接回车 = 10 秒）"
    if ([string]::IsNullOrWhiteSpace($sec)) { $sec = "10" }
    $rx = Read-Host "  只抓特定数字？给个正则（直接回车 = 自动抓全部数字）"

    $cargs = @("$Bin\serial_plot.py", "--seconds", $sec, "--out",
               "$Root\logs\串口曲线_$(Get-Date -Format 'MMdd_HHmmss').html", "--csv")
    if (-not [string]::IsNullOrWhiteSpace($port)) { $cargs += @("--port", $port.Trim()) }
    if (-not [string]::IsNullOrWhiteSpace($rx))   { $cargs += @("--regex", $rx.Trim()) }

    Write-Host ""
    Write-Host "  开始采集，板子上的数据会实时刷出来 ..." -ForegroundColor Yellow
    Write-Host ""
    $out = & $Py @cargs 2>&1
    $out | ForEach-Object { Write-Host "  $_" }
    $html = $out | Where-Object { $_ -match "曲线图：" } | Select-Object -First 1
    if ($html) {
        $p = ($html -split "：", 2)[1].Trim()
        Open-Report $p
    }
    Pause-It
}

# ---------------------------------------------------------------- 2. 内存分析

function Do-MapAnalyze {
    $proj = Pick-Project "内存占用分析（.map）" "*.map"
    if (-not $proj) { return }
    Clear-Host
    Write-Host "  正在分析 $proj 的内存占用 ..." -ForegroundColor Yellow
    Write-Host ""
    $out = & $Py "$Bin\map_analyze.py" (Join-Path $ProjRoot $proj) --top 15 2>&1
    $out | ForEach-Object { Write-Host "  $_" }
    $html = $out | Where-Object { $_ -match "HTML 报告：" } | Select-Object -First 1
    if ($html) {
        $p = ($html -split "：", 2)[1].Trim()
        Open-Report $p
    }
    Pause-It
}

# ---------------------------------------------------------------- 3. 静态检查

function Do-CheckCode {
    $proj = Pick-Project "静态代码检查（cppcheck）" "*.uvprojx"
    if (-not $proj) { return }
    Clear-Host
    Write-Host "  正在检查 $proj ..." -ForegroundColor Yellow
    Write-Host "  （几十个源文件，大概要等十几秒）" -ForegroundColor DarkGray
    Write-Host ""
    $out = & $Py "$Bin\check_code.py" (Join-Path $ProjRoot $proj) `
            --only warning,style,performance,portability --html 2>&1
    $out | ForEach-Object { Write-Host "  $_" }
    $html = $out | Where-Object { $_ -match "HTML 报告：" } | Select-Object -First 1
    if ($html) {
        $p = ($html -split "：", 2)[1].Trim()
        Open-Report $p
    }
    Pause-It
}

# ---------------------------------------------------------------- 4. 编译

function Do-Build {
    $proj = Pick-Project "编译工程（Keil UV4）" "*.uvprojx"
    if (-not $proj) { return }
    $uv = Get-ChildItem -Path (Join-Path $ProjRoot $proj) -Filter "*.uvprojx" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $uv) {
        Write-Host "  找不到 .uvprojx" -ForegroundColor Red
        Pause-It
        return
    }
    Clear-Host
    Write-Host "  正在编译 $($uv.Name) ..." -ForegroundColor Yellow
    Write-Host "  （首次全编译约 30-40 秒）" -ForegroundColor DarkGray
    Write-Host ""
    if (-not (Test-Path $UV4)) {
        Write-Host "  找不到 Keil：$UV4" -ForegroundColor Red
        Pause-It
        return
    }
    $log = "$Root\logs\build_$($uv.BaseName)_$(Get-Date -Format 'MMdd_HHmmss').log"
    $p = Start-Process -FilePath $UV4 -ArgumentList @("-b", $uv.FullName, "-j0", "-o", $log) -Wait -PassThru
    Write-Host "  UV4 退出码：$($p.ExitCode)（0 = 成功，1 = 有警告，2+ = 有错误）"
    Write-Host ""
    if (Test-Path $log) {
        Write-Host "  ---- 编译日志（末尾）----" -ForegroundColor White
        Get-Content $log -Tail 25 -Encoding Default | ForEach-Object { Write-Host "  $_" }
    }
    Write-Host ""
    Write-Host "  完整日志：$log" -ForegroundColor DarkGray
    Pause-It
}

# ---------------------------------------------------------------- 6. 读芯片固件

function Do-ChipFirmware {
    Clear-Host
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  读芯片固件 —— 纯只读，不动芯片一个字节" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  需要：ST-Link 插好、板子通电。" -ForegroundColor DarkGray
    Write-Host ""

    $ocRoot = Get-ChildItem -Path "$Root\tools\openocd" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $ocRoot) {
        Write-Host "  找不到 OpenOCD（$Root\tools\openocd）。" -ForegroundColor Red
        Pause-It
        return
    }
    $ocd = Join-Path $ocRoot.FullName "bin\openocd.exe"
    $scr = Join-Path $ocRoot.FullName "share\openocd\scripts"
    if (-not (Test-Path $ocd)) {
        Write-Host "  找不到 $ocd" -ForegroundColor Red
        Pause-It
        return
    }

    $img  = "$Root\logs\chip_flash.bin"
    $page = "$Root\logs\chip_datapage.bin"
    Remove-Item $img, $page -ErrorAction SilentlyContinue

    Write-Host "  正在读取芯片 Flash（约 5 秒）..." -ForegroundColor Yellow
    Write-Host ""
    $imgF  = $img.Replace('\', '/')
    $pageF = $page.Replace('\', '/')
    $cmd = "init; reset halt; dump_image {$imgF} 0x08000000 0x7F800; dump_image {$pageF} 0x0807F800 0x800; reset run; shutdown"
    $log = & $ocd -s $scr -f interface/stlink.cfg -f target/stm32f1x.cfg -c $cmd 2>&1
    $log | Where-Object { $_ -match "STLINK|Target voltage|DPIDR|Cortex|flash size|device id|Error|error" } |
        ForEach-Object { Write-Host "  $_" }

    if (-not (Test-Path $img)) {
        Write-Host ""
        Write-Host "  没读到芯片。逐项检查：" -ForegroundColor Red
        Write-Host "    1) ST-Link 的 USB 插好了吗（设备管理器里能看到 STM32 STLink）" -ForegroundColor Red
        Write-Host "    2) 板子通电了吗（目标电压应该 3.2 V 左右）" -ForegroundColor Red
        Write-Host "    3) Keil 是不是正开着并占着调试口？先关掉 Keil 再试" -ForegroundColor Red
        Pause-It
        return
    }

    Write-Host ""
    Write-Host "  已导出：$img" -ForegroundColor Green
    Write-Host ""

    # 收集所有候选 hex（跳过构建中间产物里那些小的）
    $hexes = Get-ChildItem -Path $ProjRoot -Filter "*.hex" -Recurse -ErrorAction SilentlyContinue |
             Where-Object { $_.Length -gt 5000 -and $_.FullName -notmatch '\\\.git\\' } |
             Select-Object -ExpandProperty FullName

    Write-Host "  与 $($hexes.Count) 个工程 hex 比对 ..." -ForegroundColor Yellow
    Write-Host ""
    if ($hexes.Count -gt 0) {
        $a = @("$Bin\flash_diff.py", $img) + $hexes
        & $Py @a 2>&1 | ForEach-Object { Write-Host "  $_" }
    }

    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  数据页 0x0807F800（掉电保存的内容）" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    & $Py "$Bin\flash_diff.py" --datapage $page 2>&1 | ForEach-Object { Write-Host "  $_" }

    Pause-It
}

# ---------------------------------------------------------------- 主菜单

while ($true) {
    Clear-Host
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "            STM32 嵌入式工具箱" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   [1]  串口画图          读串口，自动出曲线图" -ForegroundColor Green
    Write-Host "   [2]  内存占用分析      .map → Flash/RAM 占用排行" -ForegroundColor White
    Write-Host "   [3]  静态代码检查      cppcheck 找潜在 bug" -ForegroundColor White
    Write-Host "   [4]  编译工程          Keil 命令行编译" -ForegroundColor White
    Write-Host "   [5]  代码版本管理      备份 / 回滚" -ForegroundColor White
    Write-Host "   [6]  读芯片固件        看板子里烧的是哪份固件（只读）" -ForegroundColor White
    Write-Host "   [7]  环境自检          检查工具链是否齐全" -ForegroundColor DarkGray
    Write-Host "   [0]  退出" -ForegroundColor DarkGray
    Write-Host ""
    $choice = Read-Host "  输入编号后回车"
    switch ($choice.Trim()) {
        "1" { Do-SerialPlot }
        "2" { Do-MapAnalyze }
        "3" { Do-CheckCode }
        "4" { Do-Build }
        "5" { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Bin\git_panel.ps1" }
        "6" { Do-ChipFirmware }
        "7" { Clear-Host; & $Py "$Bin\selfcheck.py"; Pause-It }
        "0" { return }
        default { }
    }
}
