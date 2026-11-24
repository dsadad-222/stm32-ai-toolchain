#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keil 工程静态代码检查（cppcheck 封装）
==========================================================
自动从 .uvprojx 里读出 include 路径、宏定义、源文件清单，
再用 cppcheck 做静态分析 —— 不用手工拼那一长串 -I 和 -D。

用法：
  python check_code.py <工程目录或 .uvprojx> [选项]

选项：
  --level   检查强度：normal(默认) | full | exhaustive
  --only    只看某几个级别，逗号分隔：error,warning,performance,portability,style
  --html    额外生成 HTML 报告
  --quiet   只打印有问题的行

例：
  python check_code.py D:\\STM32_Project\\AdcTemp_Full
  python check_code.py D:\\STM32_Project\\RadarRemote_Full --only error,warning
"""
import argparse
import glob
import html
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime

# 输出统一走 UTF-8：Windows 下 Python 默认用 GBK，中文/符号容易炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CPPCHECK = r"D:\STM32_AI_Tools\tools\cppcheck\cppcheck.exe"

SEV_ORDER = ["error", "warning", "performance", "portability", "style",
             "information", "debug"]
SEV_CN = {
    "error": "错误", "warning": "警告", "performance": "性能",
    "portability": "可移植性", "style": "风格", "information": "提示",
    "debug": "调试",
}
SEV_COLOR = {
    "error": "#dc2626", "warning": "#d97706", "performance": "#7c3aed",
    "portability": "#0891b2", "style": "#6b7280", "information": "#9ca3af",
    "debug": "#9ca3af",
}


def find_uvprojx(target):
    if os.path.isfile(target) and target.lower().endswith(".uvprojx"):
        return target
    pats = [
        os.path.join(target, "Project", "*.uvprojx"),
        os.path.join(target, "*.uvprojx"),
        os.path.join(target, "**", "*.uvprojx"),
    ]
    for p in pats:
        hits = glob.glob(p, recursive=True)
        if hits:
            hits.sort(key=lambda h: (0 if os.path.basename(os.path.dirname(h)) == "Project" else 1,
                                     len(h)))
            return hits[0]
    return None


def parse_project(uvprojx):
    """从 .uvprojx 里抠出 include 路径、宏、源文件"""
    tree = ET.parse(uvprojx)
    root = tree.getroot()
    proj_dir = os.path.dirname(os.path.abspath(uvprojx))

    incs, defines, sources = [], [], []
    for el in root.iter("IncludePath"):
        if el.text and el.text.strip():
            incs.append(el.text.strip())
            break
    for el in root.iter("Define"):
        if el.text and el.text.strip():
            defines.append(el.text.strip())
            break
    for el in root.iter("FilePath"):
        if el.text and el.text.strip().lower().endswith(".c"):
            sources.append(el.text.strip())

    def norm(p):
        p = p.replace("\\\\", "\\").replace("\\", os.sep)
        return os.path.normpath(os.path.join(proj_dir, p))

    inc_dirs = []
    for chunk in (incs[0].split(";") if incs else []):
        chunk = chunk.strip()
        if chunk:
            d = norm(chunk)
            if os.path.isdir(d):
                inc_dirs.append(d)
    defs = []
    for chunk in (defines[0].split(",") if defines else []):
        chunk = chunk.strip()
        if chunk:
            defs.append(chunk)
    src_files = []
    for s in sources:
        f = norm(s)
        if os.path.isfile(f):
            src_files.append(f)

    return {
        "uvprojx": os.path.abspath(uvprojx),
        "project_dir": proj_dir,
        "name": os.path.basename(uvprojx)[:-8],
        "inc_dirs": inc_dirs,
        "defines": defs,
        "sources": src_files,
    }


def run_cppcheck(proj, level, enable):
    """返回 (输出文本, 错误说明, 退出码)"""
    if not os.path.isfile(CPPCHECK):
        return None, "找不到 cppcheck：%s" % CPPCHECK, -1

    cmd = [
        CPPCHECK,
        "--enable=" + enable,
        "--check-level=" + level,
        "--inline-suppr",
        "--quiet",
        "--template={file}\t{line}\t{severity}\t{id}\t{message}",
        "--language=c",
    ]
    for d in proj["inc_dirs"]:
        cmd += ["-I", d]
    for d in proj["defines"]:
        cmd += ["-D", d]
    cmd += proj["sources"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return None, "cppcheck 超时（超过 15 分钟）", -1
    except OSError as e:
        return None, "启动 cppcheck 失败：%s" % e, -1

    out = (r.stdout or "") + (r.stderr or "")
    # cppcheck 自己报的错（参数非法、找不到文件等）长这样：cppcheck: error: xxx
    # 必须单独识别 —— 否则会被当成"零问题"，是危险的静默失败
    own_errors = [ln.strip() for ln in out.splitlines()
                  if ln.strip().startswith("cppcheck:")]
    if own_errors:
        return None, "cppcheck 自身报错：\n    " + "\n    ".join(own_errors), r.returncode
    return out, None, r.returncode


def parse_output(text):
    """解析 cppcheck 的 --template 输出。NOISE 里的是纯噪音，直接丢掉。"""
    NOISE = {
        # 只是提示"分支分析被限制了"，不是代码问题
        "normalCheckLevelMaxBranches",
        "checkersReport",
        "missingIncludeSystem",
        "unmatchedSuppression",
    }
    items = []
    for ln in text.splitlines():
        parts = ln.split("\t")
        if len(parts) < 5:
            continue
        f, line, sev, cid, msg = parts[0], parts[1], parts[2], parts[3], parts[4]
        if sev not in SEV_CN or cid in NOISE:
            continue
        items.append({"file": f, "line": line, "sev": sev,
                      "id": cid, "msg": msg})
    return items


def build_html(proj, items, level):
    by_sev = OrderedDict()
    for s in SEV_ORDER:
        grp = [i for i in items if i["sev"] == s]
        if grp:
            by_sev[s] = grp

    A = []
    A.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A.append("<title>静态检查 · %s</title>" % html.escape(proj["name"]))
    A.append("""<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2328;--muted:#6b7280;--line:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;
--fg:#e6e8eb;--muted:#9aa4b2;--line:#2a2f3a}}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:var(--bg);color:var(--fg);
font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1060px;margin:0 auto}
h1{font-size:21px;font-weight:600;margin:0 0 6px}
h2{font-size:15px;font-weight:600;margin:28px 0 10px;padding-bottom:7px;
border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:2px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line);
vertical-align:top}
th{background:var(--bg);color:var(--muted);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
td.n{text-align:right;font-family:ui-monospace,Consolas,monospace;color:var(--muted)}
code{background:var(--bg);border:1px solid var(--line);border-radius:4px;
padding:1px 5px;font-size:12px;word-break:break-all}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;
border:1px solid currentColor;white-space:nowrap}
.ok{background:var(--card);border:1px solid var(--line);border-left:3px solid #16a34a;
border-radius:8px;padding:16px;color:var(--fg)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid #2563eb;
border-radius:8px;padding:14px 16px;color:var(--muted);font-size:13px;margin-top:16px}
.foot{color:var(--muted);font-size:12px;margin-top:34px;text-align:center}
</style></head><body><div class="wrap">""")
    A.append("<h1>%s · 静态代码检查</h1>" % html.escape(proj["name"]))
    A.append("<div class='sub'>工程：<code>%s</code></div>"
             % html.escape(proj["uvprojx"]))
    A.append("<div class='sub'>检查强度：%s ｜ 源文件 %d 个 ｜ include 路径 %d 个"
             " ｜ 宏 %d 个</div>"
             % (level, len(proj["sources"]), len(proj["inc_dirs"]),
                len(proj["defines"])))
    A.append("<div class='sub'>生成时间：%s</div>"
             % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if not items:
        A.append("<div class='ok' style='margin-top:20px'>"
                 "<b>没有发现问题。</b><br>"
                 "在当前检查强度下，cppcheck 没有报出任何错误、警告或风格问题。</div>")
    else:
        A.append("<div class='cards'>")
        for s in SEV_ORDER:
            n = len([i for i in items if i["sev"] == s])
            if n:
                A.append("<div class='card'><div class='k'>%s</div>"
                         "<div class='v' style='color:%s'>%d</div></div>"
                         % (SEV_CN[s], SEV_COLOR[s], n))
        A.append("</div>")

        for s, grp in by_sev.items():
            A.append("<h2>%s（%d 处）</h2>" % (SEV_CN[s], len(grp)))
            A.append("<table><thead><tr><th style='width:56px'>行号</th>"
                     "<th>文件</th><th>问题</th><th style='width:150px'>检查项</th>"
                     "</tr></thead><tbody>")
            for it in grp:
                A.append("<tr><td class='n'>%s</td><td><code>%s</code></td>"
                         "<td>%s</td><td><span class='pill' style='color:%s'>%s</span></td></tr>"
                         % (html.escape(it["line"]),
                            html.escape(os.path.basename(it["file"])),
                            html.escape(it["msg"]),
                            SEV_COLOR[s], html.escape(it["id"])))
            A.append("</tbody></table>")

    A.append("<div class='note'><b>怎么读</b><br>"
             "· <b>错误</b>（error）基本要改，是真问题。<br>"
             "· <b>警告</b>（warning）要逐个看，cppcheck 有时会误报"
             "（例如它把字符串字面量的长度当成数组边界）。<br>"
             "· <b>性能</b> / <b>可移植性</b> 在单片机上多数可以忽略。<br>"
             "· <b>风格</b> 是习惯问题，不影响功能，按需改。<br>"
             "· 检查强度 <code>normal</code> 会为了速度限制分支分析，"
             "想更彻底可以换 <code>exhaustive</code>（慢很多）。</div>")
    A.append("<div class='foot'>由 check_code.py 生成 · cppcheck 2.18.0</div>")
    A.append("</div></body></html>")
    return "\n".join(A)


def main():
    ap = argparse.ArgumentParser(description="Keil 工程静态代码检查（cppcheck 封装）")
    ap.add_argument("target", help="工程目录，或直接给 .uvprojx")
    ap.add_argument("--level", default="normal",
                    choices=["normal", "full", "exhaustive"])
    ap.add_argument("--only", default="warning,performance,portability",
                    help="启用的检查类别，逗号分隔。"
                         "合法值：warning,style,performance,portability,"
                         "information,unusedFunction,missingInclude,all"
                         "（error 不是合法值 —— 错误级别默认就会报，无需开启）")
    ap.add_argument("--html", action="store_true", help="生成 HTML 报告")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="只打印有问题的行")
    args = ap.parse_args()

    uv = find_uvprojx(args.target)
    if not uv:
        print("[错误] 在 %s 下找不到 .uvprojx 工程文件。" % args.target)
        return 2

    proj = parse_project(uv)
    if not args.quiet:
        print("工程      : %s" % proj["name"])
        print("工程文件  : %s" % proj["uvprojx"])
        print("源文件    : %d 个" % len(proj["sources"]))
        print("include   : %d 个" % len(proj["inc_dirs"]))
        for d in proj["inc_dirs"]:
            print("            %s" % d)
        print("宏        : %s" % (", ".join(proj["defines"]) or "（无）"))
        print("检查强度  : %s" % args.level)
        print("正在检查，请稍候 ...")
        print("")

    out, err, rc = run_cppcheck(proj, args.level, args.only)
    if err:
        print("[错误] %s" % err)
        return 3

    items = parse_output(out)

    counts = OrderedDict()
    for s in SEV_ORDER:
        n = len([i for i in items if i["sev"] == s])
        if n:
            counts[s] = n

    # 按检查项 ID 汇总 —— 一眼看出"108 处里 100 处其实是同一类"
    id_counts = {}
    for it in items:
        id_counts[it["id"]] = id_counts.get(it["id"], 0) + 1

    print("=" * 66)
    if not items:
        print("  没有发现问题（%d 个源文件）" % len(proj["sources"]))
    else:
        print("  共 %d 处：" % len(items)
              + "  ".join("%s %d" % (SEV_CN[s], n) for s, n in counts.items()))
        print("-" * 66)
        print("  按检查项汇总（前 8 类）：")
        for cid, n in sorted(id_counts.items(), key=lambda kv: -kv[1])[:8]:
            print("    %-32s %3d 处" % (cid, n))
    print("=" * 66)

    for s in SEV_ORDER:
        grp = [i for i in items if i["sev"] == s]
        if not grp:
            continue
        print("")
        print("  ── %s（%d 处）──" % (SEV_CN[s], len(grp)))
        for it in grp:
            print("    %s:%s" % (os.path.basename(it["file"]), it["line"]))
            print("        %s  [%s]" % (it["msg"], it["id"]))

    if args.html:
        out_path = os.path.join(os.path.dirname(os.path.abspath(uv)),
                                "%s_静态检查报告.html" % proj["name"])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(build_html(proj, items, args.level))
        print("")
        print("  HTML 报告：%s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
