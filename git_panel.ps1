# STM32_Project 代码版本管理面板
# 由 代码版本管理.bat 调用（ASCII bat + PowerShell 处理中文，零乱码）

$Repo = "D:\STM32_Project"
# 统一编码：git 输出的中文路径/日志不乱码
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
if (-not (Test-Path $Repo)) { Write-Host "找不到仓库目录：$Repo" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
Set-Location $Repo

function Get-ShortStatus {
    $s = git status --short 2>$null
    if ($s) { return @($s).Count } else { return 0 }
}

function Do-Commit {
    Clear-Host
    Write-Host "=============== 备份当前代码 ===============" -ForegroundColor Cyan
    $changed = Get-ShortStatus
    if ($changed -eq 0) {
        Write-Host ""
        Write-Host "  没有需要备份的改动，当前已经是最新状态。" -ForegroundColor Yellow
        Write-Host ""
        Read-Host "  回车返回"
        return
    }
    Write-Host ""
    Write-Host "  检测到 $changed 处改动：" -ForegroundColor White
    Write-Host ""
    git status --short 2>$null | Select-Object -First 20 | ForEach-Object { Write-Host "    $_" }
    if ($changed -gt 20) { Write-Host "    ...（还有 $($changed - 20) 处）" -ForegroundColor DarkGray }
    Write-Host ""
    $msg = Read-Host "  给这次备份写个说明（直接回车 = 用时间戳）"
    if ([string]::IsNullOrWhiteSpace($msg)) {
        $msg = "自动备份 " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }
    git add -A 2>$null
    git commit -q -m $msg 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "  已备份：$msg" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  备份失败，请把上面的信息发给阿布。" -ForegroundColor Red
    }
    Write-Host ""
    Read-Host "  回车返回"
}

function Show-Changes {
    Clear-Host
    Write-Host "=============== 当前改动 ===============" -ForegroundColor Cyan
    $changed = Get-ShortStatus
    if ($changed -eq 0) {
        Write-Host ""
        Write-Host "  工作区是干净的，跟上次备份完全一致。" -ForegroundColor Green
        Write-Host ""
        Read-Host "  回车返回"
        return
    }
    Write-Host ""
    git status --short 2>$null
    Write-Host ""
    Write-Host "  ---- 代码具体改了什么（行数统计）----" -ForegroundColor White
    Write-Host ""
    git diff --stat 2>$null | Select-Object -Last 40
    Write-Host ""
    Write-Host "  想看某个文件的具体改动？复制下面这行到 PowerShell 里跑：" -ForegroundColor DarkGray
    Write-Host "    git -C $Repo diff <文件路径>" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "  回车返回"
}

function Undo-Changes {
    Clear-Host
    Write-Host "=============== 撤销未备份的改动 ===============" -ForegroundColor Red
    $changed = Get-ShortStatus
    if ($changed -eq 0) {
        Write-Host ""
        Write-Host "  工作区是干净的，没有东西可以撤销。" -ForegroundColor Green
        Write-Host ""
        Read-Host "  回车返回"
        return
    }
    Write-Host ""
    Write-Host "  以下 $changed 处改动将被丢弃，回到上次备份的状态：" -ForegroundColor Yellow
    Write-Host ""
    git status --short 2>$null | Select-Object -First 25 | ForEach-Object { Write-Host "    $_" }
    Write-Host ""
    Write-Host "  注意：被丢弃的改动无法找回（除非已经备份过）。" -ForegroundColor Yellow
    Write-Host ""
    $ans = Read-Host "  确认撤销请输入 YES（其他任何键取消）"
    if ($ans -ne "YES") {
        Write-Host ""
        Write-Host "  已取消，什么都没动。" -ForegroundColor Green
        Write-Host ""
        Read-Host "  回车返回"
        return
    }
    git checkout -- . 2>$null
    git clean -fd -e .gitignore 2>$null | Out-Null
    Write-Host ""
    Write-Host "  已回到上次备份的状态。" -ForegroundColor Green
    Write-Host ""
    Read-Host "  回车返回"
}

function Show-History {
    Clear-Host
    Write-Host "=============== 历史版本 ===============" -ForegroundColor Cyan
    Write-Host ""
    git log --oneline --date=format:'%m-%d %H:%M' --pretty=format:'  %h  %ad  %s' -25 2>$null
    Write-Host ""
    Write-Host ""
    Write-Host "  ---- 回到某个历史版本 ----" -ForegroundColor White
    Write-Host "  输入上面某一行的前 7 位编号，会把整个工程恢复到那个版本" -ForegroundColor DarkGray
    Write-Host "  （当前状态会先自动备份，所以不会丢东西）" -ForegroundColor DarkGray
    Write-Host ""
    $h = Read-Host "  输入版本编号（直接回车 = 只看不改）"
    if ([string]::IsNullOrWhiteSpace($h)) { return }
    $h = $h.Trim()
    $ok = git cat-file -t $h 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ok)) {
        Write-Host ""
        Write-Host "  没找到这个版本编号：$h" -ForegroundColor Red
        Write-Host ""
        Read-Host "  回车返回"
        return
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git add -A 2>$null
    git commit -q -m "回滚前自动备份 $stamp" 2>$null
    git checkout $h -- . 2>$null
    Write-Host ""
    Write-Host "  已恢复到版本 $h" -ForegroundColor Green
    Write-Host "  当前状态已自动备份，随时可以再切回来。" -ForegroundColor Green
    Write-Host ""
    Read-Host "  回车返回"
}

function Show-Current {
    Clear-Host
    Write-Host "=============== 当前状态 ===============" -ForegroundColor Cyan
    Write-Host ""
    $branch = git rev-parse --abbrev-ref HEAD 2>$null
    $last = git log -1 --date=format:'%Y-%m-%d %H:%M' --pretty=format:'%h  %ad  %s' 2>$null
    Write-Host "  仓库：$Repo" -ForegroundColor White
    Write-Host "  分支：$branch" -ForegroundColor White
    Write-Host "  最近备份：$last" -ForegroundColor White
    Write-Host "  未备份改动：$(Get-ShortStatus) 处" -ForegroundColor White
    Write-Host ""
    Read-Host "  回车返回"
}

while ($true) {
    Clear-Host
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "        STM32_Project   代码版本管理" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   [1]  备份当前代码        （改好了，存一版）" -ForegroundColor Green
    Write-Host "   [2]  查看改了什么        （只看，不动代码）" -ForegroundColor White
    Write-Host "   [3]  撤销未备份的改动    （改崩了，退回上次）" -ForegroundColor Yellow
    Write-Host "   [4]  历史版本 / 回滚     （回到任意一次备份）" -ForegroundColor White
    Write-Host "   [5]  当前状态一览" -ForegroundColor DarkGray
    Write-Host "   [0]  退出" -ForegroundColor DarkGray
    Write-Host ""
    $choice = Read-Host "  请输入编号后回车"
    switch ($choice.Trim()) {
        "1" { Do-Commit }
        "2" { Show-Changes }
        "3" { Undo-Changes }
        "4" { Show-History }
        "5" { Show-Current }
        "0" { return }
        default { }
    }
}
