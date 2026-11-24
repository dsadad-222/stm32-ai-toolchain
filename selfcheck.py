# -*- coding: utf-8 -*-
"""STM32 AI 工具链自检（D 盘版，不依赖 PowerShell）"""
import os
import subprocess
import sys

ROOT = r"D:\STM32_AI_Tools"
PY = os.path.join(ROOT, "venvs", "emb", "Scripts", "python.exe")
UV4 = r"D:\keil5\UV4\UV4.exe"
SKILLS = os.path.join(os.environ.get("USERPROFILE", ""), ".workbuddy-ai", "skills")
MCP_CFG = os.path.join(os.environ.get("USERPROFILE", ""), ".workbuddy-ai", "mcp.json")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ok = True


def head(t):
    print("\n" + t)


def line(label, good, detail=""):
    global ok
    if not good:
        ok = False
    print(f"  [{'OK ' if good else '!! '}] {label}{('  ' + detail) if detail else ''}")


print("=" * 46)
print("  STM32 AI 工具链自检")
print("=" * 46)

head("[1] Python 环境")
line("venv 解释器", os.path.exists(PY), PY if os.path.exists(PY) else "缺失")

head("[2] Keil")
line("UV4.exe", os.path.exists(UV4), UV4 if os.path.exists(UV4) else "缺失")

head("[3] 关键依赖")
if os.path.exists(PY):
    out = subprocess.run([PY, "-m", "pip", "list"], capture_output=True, text=True).stdout
    for key, label in [("keil-mcp-server", "keil-mcp-server"), ("uart-mcp", "uart-mcp"),
                       ("pyocd", "pyocd"), ("pyserial", "pyserial"), ("mcp ", "mcp")]:
        hit = [l for l in out.splitlines() if l.lower().startswith(key)]
        line(label, bool(hit), hit[0].split()[1] if hit else "未安装")

head("[4] 命令行工具链（D 盘 tools 目录）")
import glob as _glob

TOOLS = os.path.join(ROOT, "tools")
TOOL_ITEMS = [
    ("cppcheck", os.path.join(TOOLS, "cppcheck", "cppcheck.exe"), "--version"),
    ("OpenOCD", os.path.join(TOOLS, "openocd", "xpack-openocd-0.12.0-7", "bin", "openocd.exe"), "--version"),
    ("arm-none-eabi-gdb", os.path.join(TOOLS, "arm-gcc", "xpack-arm-none-eabi-gcc-15.2.1-1.1", "bin", "arm-none-eabi-gdb.exe"), "--version"),
    ("arm-none-eabi-size", os.path.join(TOOLS, "arm-gcc", "xpack-arm-none-eabi-gcc-15.2.1-1.1", "bin", "arm-none-eabi-size.exe"), "--version"),
]
for label, path, arg in TOOL_ITEMS:
    if not os.path.exists(path):
        # 版本号可能变，退化成按名字找
        hits = _glob.glob(os.path.join(TOOLS, "*", "**", os.path.basename(path)), recursive=True)
        path = hits[0] if hits else path
    if os.path.exists(path):
        try:
            r = subprocess.run([path, arg], capture_output=True, text=True, timeout=20)
            ver = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            ver = ver[0].strip() if ver else "?"
        except Exception as e:
            ver = "运行失败: %s" % e
        line(label, True, ver[:60])
    else:
        line(label, False, "未安装")

head("[5] 自建脚本")
BIN = os.path.join(ROOT, "bin")
for s in ("serial_plot.py", "map_analyze.py", "check_code.py",
          "git_panel.ps1", "toolbox.ps1", "嵌入式工具箱.bat"):
    p = os.path.join(BIN, s)
    line(s, os.path.exists(p), "%d B" % os.path.getsize(p) if os.path.exists(p) else "缺失")

head("[6] Git 版本管理")
REPO = r"D:\STM32_Project"
if os.path.isdir(os.path.join(REPO, ".git")):
    r = subprocess.run(["git", "-C", REPO, "log", "--oneline", "-1"],
                       capture_output=True, text=True)
    n = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True)
    line("仓库已建立", True, "跟踪 %d 个文件" % len([l for l in n.stdout.splitlines() if l]))
    line("最近一次备份", True, r.stdout.strip()[:60] or "(无)")
else:
    line("仓库", False, "未建立（双击 bin\\代码版本管理.bat）")

head("[7] 技能")
if os.path.isdir(SKILLS):
    names = [d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d))]
    emb = [n for n in names if any(k in n for k in ("keil", "embedded", "hardfault", "debug-method", "state-machine", "c-cpp"))]
    line(f"技能总数 {len(names)}（其中嵌入式相关 {len(emb)}）", True, ", ".join(sorted(emb)[:6]) + " ...")
else:
    line("技能目录", False, "缺失")

head("[8] MCP 配置")
if os.path.exists(MCP_CFG):
    import json
    try:
        cfg = json.load(open(MCP_CFG, encoding="utf-8"))
        for k in cfg.get("mcpServers", {}):
            line(f"已配置 {k}", True)
    except Exception as e:
        line("解析 mcp.json", False, str(e))
else:
    line("mcp.json", False, "缺失")

head("[9] 硬件连接")
if os.path.exists(PY):
    r = subprocess.run([PY, "-c",
        "import serial.tools.list_ports as p;ps=list(p.comports());"
        "print(len(ps));[print(x.device+'|'+(x.description or '')) for x in ps]"],
        capture_output=True, text=True)
    lines = [l for l in r.stdout.strip().splitlines() if l]
    if lines:
        n = lines[0]
        real = [l for l in lines[1:] if "蓝牙" not in l and "Bluetooth" not in l]
        print(f"      串口共 {n} 个，其中非蓝牙 {len(real)} 个")
        for l in real:
            print(f"        {l}")
        if not real:
            print("      提示：USB-TTL 没插或驱动没装（现在只看到蓝牙虚拟串口）")
    r2 = subprocess.run([PY, "-m", "pyocd", "list"], capture_output=True, text=True, timeout=60)
    raw = (r2.stdout or "") + (r2.stderr or "")
    probes = []
    for l in raw.splitlines():
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        if set(s) <= set("-= "):          # 表格分隔线
            continue
        if "No available" in s:
            continue
        probes.append(s)
    if probes:
        line("调试探针", True, probes[0][:70])
    else:
        line("调试探针", False, "未发现（ST-Link 没插）")

head("[10] 磁盘")
import shutil
for d in ("C", "D"):
    try:
        print(f"      {d}: 可用 {shutil.disk_usage(d + ':/').free / 2**30:.2f} GB")
    except Exception:
        pass

print("\n" + "=" * 46)
print("  自检结束" + ("" if ok else "  —— 有项目未就绪，见上面 !! 行"))
print("=" * 46)
