#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keil MDK .map 内存占用分析器
==========================================================
读 armlink 生成的 .map 文件，输出：
  1. Flash / RAM 占用总览（含下载范围预算）
  2. 按目标文件（.o）排序的占用排行 —— 揪出"谁最占空间"
  3. 最大的函数（Thumb）与数据段（Data / Section）
  4. 自包含 HTML 报告（含条形图，可直接发给人看）

用法：
  python map_analyze.py <工程目录或 .map 路径> [--out 报告.html] [--top 20]

例：
  python map_analyze.py D:\\STM32_Project\\AdcTemp_Full
  python map_analyze.py D:\\STM32_Project\\RadarRemote_Full\\Project\\Listings\\RadarRemote_Full.map
"""
import argparse
import glob
import html
import os
import re
import sys
from datetime import datetime

# 输出统一走 UTF-8：Windows 下 Python 默认用 GBK，中文/符号容易炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 常量

# STM32F103ZET6：Flash 512KB，SRAM 64KB
CHIP_FLASH = 512 * 1024
CHIP_RAM = 64 * 1024
# 工程下载范围 -FL07F800（保护末页 2KB 标定数据）
DOWNLOAD_LIMIT = 0x7F800  # 522240 B

RE_NUM = r"\d+"
RE_ROW6 = re.compile(
    r"^\s*(" + RE_NUM + r")\s+(" + RE_NUM + r")\s+(" + RE_NUM + r")\s+("
    + RE_NUM + r")\s+(" + RE_NUM + r")\s+(" + RE_NUM + r")\s+(\S.*?)\s*$"
)
RE_SYM = re.compile(
    r"^\s*(\S+)\s+(0x[0-9a-fA-F]+)\s+"
    r"(Thumb|Section|Data|Number|Zero|RO|RW)"
    r"(?:\s+(Code|Data))?"          # 函数行是 "Thumb Code <size>"，多一个词
    r"\s+(\d+)\s+(\S+?)(?:\s+(.*?))?\s*$"
)
RE_TOTAL_RO = re.compile(r"Total\s+RO\s+Size\s+\(Code\s*\+\s*RO\s+Data\)\s+(\d+)")
RE_TOTAL_RW = re.compile(r"Total\s+RW\s+Size\s+\(RW\s+Data\s*\+\s*ZI\s+Data\)\s+(\d+)")
RE_TOTAL_ROM = re.compile(r"Total\s+ROM\s+Size\s+\(Code\s*\+\s*RO\s+Data\s*\+\s+RW\s+Data\)\s+(\d+)")


def human(n):
    """字节数转可读字符串"""
    if n >= 1024 * 1024:
        return "%.2f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.2f KB" % (n / 1024.0)
    return "%d B" % n


def where(section):
    """按段名判断变量落在 Flash 还是 RAM"""
    s = (section or "").lower()
    if "const" in s:
        return "Flash"
    if ".bss" in s or "stack" in s or "heap" in s:
        return "RAM"
    if ".data" in s:
        return "RAM"
    if s.startswith(".text") or "code" in s:
        return "Flash"
    return "—"


def find_map(target):
    """给工程目录或文件路径，返回 .map 文件路径"""
    if os.path.isfile(target) and target.lower().endswith(".map"):
        return target
    cands = []
    for pat in (
        os.path.join(target, "Project", "Listings", "*.map"),
        os.path.join(target, "Listings", "*.map"),
        os.path.join(target, "Project", "Objects", "*.map"),
        os.path.join(target, "**", "*.map"),
    ):
        cands.extend(glob.glob(pat, recursive=True))
    if not cands:
        return None
    # 优先 Listings 下的，其次体积大的
    cands.sort(key=lambda p: (0 if "listings" in p.lower() else 1, -os.path.getsize(p)))
    return cands[0]


# ---------------------------------------------------------------- 解析

def parse_map(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    res = {
        "path": path,
        "project": os.path.basename(path)[:-4],
        "tool": "",
        "objects": [],       # {name, code, ro, rw, zi, debug}
        "objects_total": None,
        "libs": [],
        "symbols": [],       # {name, addr, type, size, obj, section}
        "grand": None,
        "total_ro": None,
        "total_rw": None,
        "total_rom": None,
        "warnings": [],
    }

    # 1) 工具链版本
    for ln in lines[:6]:
        if "Component:" in ln:
            res["tool"] = ln.strip().replace("Component:", "").strip()
            break

    # 2) 段落行号
    idx = {}
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "Image Symbol Table":
            idx.setdefault("symbols", i)
        elif s == "Memory Map of the image":
            idx.setdefault("memmap", i)
        elif s == "Image component sizes":
            idx.setdefault("sizes", i)

    # 3) 总量行（Grand Totals / Total RO|RW|ROM）
    for ln in lines:
        if "Grand Totals" in ln:
            m = RE_ROW6.match(ln)
            if m:
                g = [int(x) for x in m.groups()[:5]]
                res["grand"] = {
                    "code": g[0], "ro": g[1], "rw": g[2], "zi": g[3], "debug": g[4]
                }
        m = RE_TOTAL_RO.search(ln)
        if m:
            res["total_ro"] = int(m.group(1))
        m = RE_TOTAL_RW.search(ln)
        if m:
            res["total_rw"] = int(m.group(1))
        m = RE_TOTAL_ROM.search(ln)
        if m:
            res["total_rom"] = int(m.group(1))

    # 4) Image component sizes —— 按子表解析
    if "sizes" in idx:
        start = idx["sizes"]
        mode = None
        for ln in lines[start:]:
            s = ln.strip()
            if s.endswith("Object Name"):
                mode = "obj"
                continue
            if s.endswith("Library Member Name"):
                mode = "libmem"
                continue
            if s.endswith("Library Name"):
                mode = "lib"
                continue
            if "Object Totals" in s:
                m = RE_ROW6.match(ln)
                if m:
                    res["objects_total"] = [int(x) for x in m.groups()[:5]]
                mode = None
                continue
            if "Library Totals" in s:
                mode = None
                continue
            if "Grand Totals" in s or "ELF Image Totals" in s or "ROM Totals" in s:
                mode = None
                continue
            if mode is None or not s or set(s) <= set("-="):
                continue
            m = RE_ROW6.match(ln)
            if not m:
                continue
            # 7 组：6 个数字 + 名字
            g = m.groups()
            code, ro, rw, zi, dbg, name = g[0], g[1], g[2], g[3], g[4], g[6]
            rec = {
                "name": name, "code": int(code), "ro": int(ro),
                "rw": int(rw), "zi": int(zi), "debug": int(dbg),
            }
            rec["rom"] = rec["code"] + rec["ro"] + rec["rw"]
            rec["ram"] = rec["rw"] + rec["zi"]
            if mode == "obj":
                res["objects"].append(rec)
            elif mode == "lib":
                res["libs"].append(rec)

    # 5) 符号表
    KIND = {"Thumb": "函数", "Data": "变量", "Section": "段"}
    if "symbols" in idx:
        end = idx.get("memmap", len(lines))
        for ln in lines[idx["symbols"]:end]:
            m = RE_SYM.match(ln)
            if not m:
                continue
            name, addr, typ, _sub, size, obj, sect = m.groups()
            size = int(size)
            if size <= 0:
                continue
            # armlink 把段名粘在文件名后面：cn_font16.o(.constdata)
            # 也有分开写的：boardinit2.o ABSOLUTE
            obj = obj or ""
            sect = (sect or "").strip()
            if not sect and "(" in obj:
                obj, _, tail = obj.partition("(")
                sect = "(" + tail
            res["symbols"].append({
                "name": name, "addr": addr, "type": typ,
                "kind": KIND.get(typ, typ), "size": size,
                "obj": obj, "section": sect,
                "loc": where(sect),
            })

    if not res["objects"] and not res["grand"]:
        res["warnings"].append("没能从 map 里解析出任何数据，格式可能不认识。")
    return res


# ---------------------------------------------------------------- 报告

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2328;--muted:#6b7280;--line:#e5e7eb;
--blue:#2563eb;--green:#16a34a;--amber:#d97706;--red:#dc2626;--purple:#7c3aed;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8eb;
--muted:#9aa4b2;--line:#2a2f3a;--blue:#60a5fa;--green:#4ade80;--amber:#fbbf24;
--red:#f87171;--purple:#a78bfa;}}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
h2{font-size:16px;font-weight:600;margin:32px 0 12px;padding-bottom:8px;
border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;margin-bottom:4px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:14px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card .k{color:var(--muted);font-size:12px;margin-bottom:6px}
.card .v{font-size:24px;font-weight:600;letter-spacing:-.5px}
.card .d{color:var(--muted);font-size:12px;margin-top:4px}
.bar{height:8px;border-radius:4px;background:var(--line);margin-top:10px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{background:var(--bg);color:var(--muted);font-weight:500;font-size:12px;
position:sticky;top:0}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;
font-family:ui-monospace,Consolas,monospace}
.mini{height:6px;border-radius:3px;display:inline-block;vertical-align:middle;
background:var(--blue);min-width:2px}
.badge{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;
border:1px solid currentColor}
.b-ok{color:var(--green)}.b-warn{color:var(--amber)}.b-bad{color:var(--red)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);
border-radius:8px;padding:14px 16px;color:var(--muted);font-size:13px;margin-top:14px}
.foot{color:var(--muted);font-size:12px;margin-top:36px;text-align:center}
code{background:var(--bg);border:1px solid var(--line);border-radius:4px;
padding:1px 5px;font-size:12px}
"""


def esc(s):
    return html.escape(str(s))


def bar_cell(value, vmax, color="var(--blue)"):
    pct = 0 if vmax <= 0 else max(1.0, value * 100.0 / vmax)
    return '<span class="mini" style="width:%.1f%%;background:%s"></span>' % (pct, color)


def build_html(r, top=20):
    g = r["grand"] or {}
    code = g.get("code", 0)
    ro = g.get("ro", 0)
    rw = g.get("rw", 0)
    zi = g.get("zi", 0)
    rom = r["total_rom"] or (code + ro + rw)
    ram = r["total_rw"] or (rw + zi)

    flash_pct = rom * 100.0 / DOWNLOAD_LIMIT
    ram_pct = ram * 100.0 / CHIP_RAM

    def cls(pct):
        return "b-ok" if pct < 70 else ("b-warn" if pct < 90 else "b-bad")

    objs = sorted(r["objects"], key=lambda o: -o["rom"])
    vmax_obj = objs[0]["rom"] if objs else 1

    funcs = [s for s in r["symbols"] if s["type"] == "Thumb"]
    funcs.sort(key=lambda s: -s["size"])
    vars_ = [s for s in r["symbols"] if s["type"] == "Data"]
    vars_.sort(key=lambda s: -s["size"])
    secs = [s for s in r["symbols"] if s["type"] == "Section"]
    secs.sort(key=lambda s: -s["size"])

    out = []
    A = out.append
    A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A("<title>内存占用分析 · %s</title>" % esc(r["project"]))
    A("<style>%s</style></head><body><div class='wrap'>" % CSS)

    A("<h1>%s · 内存占用分析</h1>" % esc(r["project"]))
    A("<div class='sub'>map 文件：<code>%s</code></div>" % esc(r["path"]))
    A("<div class='sub'>工具链：%s</div>" % esc(r["tool"] or "未知"))
    A("<div class='sub'>生成时间：%s</div>" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 卡片
    A("<div class='cards'>")
    A("<div class='card'><div class='k'>Flash 占用（ROM 镜像）</div>"
      "<div class='v'>%s</div><div class='d'>%d 字节 · 预算 %s（%d B）"
      "<span class='badge %s'>%.1f%%</span></div>"
      "<div class='bar'><i style='width:%.1f%%;background:var(--blue)'></i></div></div>"
      % (human(rom), rom, human(DOWNLOAD_LIMIT), DOWNLOAD_LIMIT,
         cls(flash_pct), flash_pct, min(flash_pct, 100)))
    A("<div class='card'><div class='k'>RAM 占用（RW + ZI）</div>"
      "<div class='v'>%s</div><div class='d'>%d 字节 · 芯片 %s"
      "<span class='badge %s'>%.1f%%</span></div>"
      "<div class='bar'><i style='width:%.1f%%;background:var(--purple)'></i></div></div>"
      % (human(ram), ram, human(CHIP_RAM), cls(ram_pct), ram_pct, min(ram_pct, 100)))
    A("<div class='card'><div class='k'>代码 Code</div><div class='v'>%s</div>"
      "<div class='d'>含内联数据 %d B</div></div>" % (human(code), code))
    A("<div class='card'><div class='k'>只读数据 RO Data</div><div class='v'>%s</div>"
      "<div class='d'>字库 / 常量表</div></div>" % human(ro))
    A("<div class='card'><div class='k'>已初始化变量 RW</div><div class='v'>%s</div>"
      "<div class='d'>占 Flash + RAM</div></div>" % human(rw))
    A("<div class='card'><div class='k'>未初始化变量 ZI</div><div class='v'>%s</div>"
      "<div class='d'>只占 RAM</div></div>" % human(zi))
    A("</div>")

    # Flash 构成条
    if rom:
        p_code = code * 100.0 / rom
        p_ro = ro * 100.0 / rom
        p_rw = rw * 100.0 / rom
        A("<h2>Flash 镜像构成</h2>")
        A("<div class='bar' style='height:22px;display:flex'>")
        A("<i style='width:%.2f%%;background:var(--blue);border-radius:4px 0 0 4px'>"
          "</i>" % p_code)
        A("<i style='width:%.2f%%;background:var(--green)'></i>" % p_ro)
        A("<i style='width:%.2f%%;background:var(--amber);border-radius:0 4px 4px 0'>"
          "</i>" % p_rw)
        A("</div>")
        A("<div class='sub' style='margin-top:8px'>"
          "<span style='color:var(--blue)'>■ 代码 %s (%.0f%%)</span>&nbsp;&nbsp;"
          "<span style='color:var(--green)'>■ 只读数据 %s (%.0f%%)</span>&nbsp;&nbsp;"
          "<span style='color:var(--amber)'>■ 已初始化变量 %s (%.0f%%)</span></div>"
          % (human(code), p_code, human(ro), p_ro, human(rw), p_rw))

    # 目标文件排行
    A("<h2>占用排行 · 按目标文件（前 %d 个）</h2>" % top)
    A("<table><thead><tr><th>#</th><th>目标文件</th>"
      "<th class='n'>Code</th><th class='n'>RO</th><th class='n'>RW</th>"
      "<th class='n'>ZI</th><th class='n'>占 Flash</th><th style='width:150px'>占比</th>"
      "</tr></thead><tbody>")
    for i, o in enumerate(objs[:top], 1):
        A("<tr><td class='n'>%d</td><td>%s</td>"
          "<td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td>"
          "<td class='n'>%d</td><td class='n'>%s</td><td>%s</td></tr>"
          % (i, esc(o["name"]), o["code"], o["ro"], o["rw"], o["zi"],
             human(o["rom"]), bar_cell(o["rom"], vmax_obj)))
    A("</tbody></table>")

    if len(objs) > top:
        rest = sum(o["rom"] for o in objs[top:])
        A("<div class='sub' style='margin-top:8px'>其余 %d 个文件合计 %s</div>"
          % (len(objs) - top, human(rest)))

    # 最大变量
    vmax = vars_[0]["size"] if vars_ else 1
    ram_only = sum(s["size"] for s in vars_ if s["loc"] == "RAM")
    flash_only = sum(s["size"] for s in vars_ if s["loc"] == "Flash")
    A("<h2>最大的变量（前 %d 个）</h2>" % top)
    A("<div class='sub'>其中落在 RAM 的共 %s，落在 Flash 的常量共 %s</div>"
      % (human(ram_only), human(flash_only)))
    A("<table><thead><tr><th>#</th><th>变量名</th><th>位置</th><th>所在文件</th>"
      "<th class='n'>大小</th><th style='width:150px'>占比</th>"
      "</tr></thead><tbody>")
    for i, s in enumerate(vars_[:top], 1):
        color = "var(--purple)" if s["loc"] == "RAM" else "var(--green)"
        A("<tr><td class='n'>%d</td><td><code>%s</code></td><td>%s</td><td>%s</td>"
          "<td class='n'>%s</td><td>%s</td></tr>"
          % (i, esc(s["name"]), esc(s["loc"]), esc(s["obj"]),
             human(s["size"]), bar_cell(s["size"], vmax, color)))
    A("</tbody></table>")

    # 最大函数
    fmax = funcs[0]["size"] if funcs else 1
    A("<h2>最大的函数（前 %d 个）</h2>" % top)
    A("<table><thead><tr><th>#</th><th>函数名</th><th>所在文件</th>"
      "<th class='n'>大小</th><th style='width:150px'>占比</th>"
      "</tr></thead><tbody>")
    for i, s in enumerate(funcs[:top], 1):
        A("<tr><td class='n'>%d</td><td><code>%s</code></td><td>%s</td>"
          "<td class='n'>%s</td><td>%s</td></tr>"
          % (i, esc(s["name"]), esc(s["obj"]),
             human(s["size"]), bar_cell(s["size"], fmax, "var(--blue)")))
    A("</tbody></table>")

    # 最大段
    A("<h2>最大的段（Section，前 %d 个）</h2>" % top)
    A("<table><thead><tr><th>#</th><th>段名</th><th>所属文件</th>"
      "<th class='n'>大小</th></tr></thead><tbody>")
    for i, s in enumerate(secs[:top], 1):
        A("<tr><td class='n'>%d</td><td><code>%s</code></td><td>%s</td>"
          "<td class='n'>%s</td></tr>"
          % (i, esc(s["name"]), esc(s["obj"]), human(s["size"])))
    A("</tbody></table>")

    # 提示
    A("<div class='note'><b>怎么读这份报告</b><br>"
      "· <b>占 Flash</b> = Code + RO + RW，也就是烧进芯片的镜像大小；"
      "下载范围是 <code>0x08000000 + 0x07F800</code>，末页 2KB 留给标定数据，所以预算是 %s。<br>"
      "· <b>RAM</b> = RW + ZI，不含栈和堆 —— 那两个在启动文件里配，不体现在 map 总量里。<br>"
      "· 想省 Flash，先看排行前几名：字库（<code>cn_font16.o</code>）和 "
      "<code>main.o</code> 通常是最大头。<br>"
      "· 想省 RAM，看 ZI 列 —— ZI 是未初始化变量（数组、缓冲区），"
      "它是 RAM 占用的大头。</div>" % human(DOWNLOAD_LIMIT))

    A("<div class='foot'>由 map_analyze.py 生成 · %s</div>"
      % datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("</div></body></html>")
    return "\n".join(out)


# ---------------------------------------------------------------- 主程序

def main():
    ap = argparse.ArgumentParser(
        description="Keil MDK .map 内存占用分析器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", help="工程目录，或直接给 .map 文件路径")
    ap.add_argument("--out", "-o", default=None, help="HTML 报告输出路径")
    ap.add_argument("--top", "-n", type=int, default=20, help="每个榜单显示多少条（默认 20）")
    ap.add_argument("--quiet", "-q", action="store_true", help="只在终端打印摘要")
    args = ap.parse_args()

    map_path = find_map(args.target)
    if not map_path:
        print("[错误] 在 %s 下找不到 .map 文件。" % args.target)
        print("       请先编译一次工程（编译会生成 Listings/*.map）。")
        return 2

    print("map 文件：%s" % map_path)
    r = parse_map(map_path)
    if r["warnings"]:
        for w in r["warnings"]:
            print("[警告] %s" % w)
        return 3

    g = r["grand"] or {}
    rom = r["total_rom"] or (g.get("code", 0) + g.get("ro", 0) + g.get("rw", 0))
    ram = r["total_rw"] or (g.get("rw", 0) + g.get("zi", 0))

    print("")
    print("=" * 62)
    print("  %s  内存占用" % r["project"])
    print("=" * 62)
    print("  工具链      : %s" % (r["tool"] or "未知"))
    print("  Code        : %8d B  (%s)" % (g.get("code", 0), human(g.get("code", 0))))
    print("  RO Data     : %8d B  (%s)" % (g.get("ro", 0), human(g.get("ro", 0))))
    print("  RW Data     : %8d B  (%s)" % (g.get("rw", 0), human(g.get("rw", 0))))
    print("  ZI Data     : %8d B  (%s)" % (g.get("zi", 0), human(g.get("zi", 0))))
    print("-" * 62)
    print("  Flash 镜像  : %8d B  (%s)  = %.1f%% / %s"
          % (rom, human(rom), rom * 100.0 / DOWNLOAD_LIMIT, human(DOWNLOAD_LIMIT)))
    print("  RAM 占用    : %8d B  (%s)  = %.1f%% / %s"
          % (ram, human(ram), ram * 100.0 / CHIP_RAM, human(CHIP_RAM)))
    print("=" * 62)

    objs = sorted(r["objects"], key=lambda o: -o["rom"])
    if objs:
        print("")
        print("  占 Flash 最多的目标文件：")
        for i, o in enumerate(objs[:args.top], 1):
            print("    %2d. %-26s %8d B  %7s  (Code %d / RO %d / RW %d)"
                  % (i, o["name"], o["rom"], human(o["rom"]),
                     o["code"], o["ro"], o["rw"]))

    vars_ = [s for s in r["symbols"] if s["type"] == "Data"]
    vars_.sort(key=lambda s: -s["size"])
    if vars_:
        ram_only = sum(s["size"] for s in vars_ if s["loc"] == "RAM")
        flash_only = sum(s["size"] for s in vars_ if s["loc"] == "Flash")
        print("")
        print("  最占空间的变量（RAM 合计 %s / Flash 常量 %s）："
              % (human(ram_only), human(flash_only)))
        for i, s in enumerate(vars_[:args.top], 1):
            print("    %2d. %-30s %7s  %-5s [%s]"
                  % (i, s["name"][:30], human(s["size"]), s["loc"], s["obj"]))

    funcs = [s for s in r["symbols"] if s["type"] == "Thumb"]
    funcs.sort(key=lambda s: -s["size"])
    if funcs:
        print("")
        print("  最大的函数：")
        for i, s in enumerate(funcs[:args.top], 1):
            print("    %2d. %-30s %7s  [%s]"
                  % (i, s["name"][:30], human(s["size"]), s["obj"]))

    if not args.quiet:
        out = args.out
        if not out:
            base = os.path.splitext(os.path.basename(map_path))[0]
            out = os.path.join(os.path.dirname(os.path.abspath(map_path)),
                               "%s_内存报告.html" % base)
        with open(out, "w", encoding="utf-8") as f:
            f.write(build_html(r, args.top))
        print("")
        print("  HTML 报告：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
