#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口数据采集 + 可视化
==========================================================
从串口读一段时间的数据，自动抓出数字，画成曲线图（自包含 HTML）。
调超声波抖动、看滤波前后对比、画 ADC 温度漂移 —— 不用再盯 XCOM 数数字。

用法：
  python serial_plot.py --list                      列出可用串口
  python serial_plot.py --seconds 10                自动找串口，读 10 秒
  python serial_plot.py -p COM3 -b 115200 -t 10     指定串口/波特率/时长
  python serial_plot.py --file 日志.txt             分析已有的日志文件
  python serial_plot.py -r "Dist:\\s*(\\d+)"         只抓符合正则的那个数字

常用选项：
  -p/--port     串口，如 COM3（不给就自动挑一个 USB 串口）
  -b/--baud     波特率，默认 115200
  -t/--seconds  采集时长（秒），默认 10
  -r/--regex    正则，取第 1 个捕获组作为数值
  -o/--out      输出 HTML 路径
  --csv         同时输出 CSV
  --cols        指定列数（一行里取前 N 个数字作为 N 条曲线）
"""
import argparse
import glob
import html
import os
import re
import statistics
import sys
import time
from datetime import datetime

# 输出统一走 UTF-8：Windows 下 Python 默认用 GBK，中文/符号容易炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("[错误] 没装 pyserial。用专用 Python 跑：")
    print(r'  D:\STM32_AI_Tools\venvs\emb\Scripts\python.exe "D:\STM32_AI_Tools\bin\serial_plot.py" ...')
    sys.exit(1)

RE_NUM = re.compile(r"-?\d+(?:\.\d+)?")
# 蓝牙/虚拟串口常见关键字，自动选口时优先排除
BT_HINT = ("bluetooth", "蓝牙", "BTHENUM", "BthModem")

PALETTE = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
           "#0891b2", "#be185d", "#4d7c0f"]


# ---------------------------------------------------------------- 串口

def list_serial_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("没有找到任何串口。")
        return []
    print("%-10s %-28s %s" % ("端口", "描述", "硬件ID"))
    print("-" * 78)
    for p in ports:
        desc = (p.description or "")[:26]
        hwid = (p.hwid or "")[:30]
        bt = "  <-- 蓝牙/虚拟" if any(h.lower() in (p.description or "").lower()
                                     or h in (p.hwid or "") for h in BT_HINT) else ""
        print("%-10s %-28s %s%s" % (p.device, desc, hwid, bt))
    return [p.device for p in ports]


def pick_port():
    """自动挑一个像样的 USB 串口"""
    ports = list(list_ports.comports())
    real, bt = [], []
    for p in ports:
        blob = ((p.description or "") + (p.hwid or "")).lower()
        if any(h.lower() in blob for h in BT_HINT):
            bt.append(p.device)
        else:
            real.append(p.device)
    # 优先 USB 转串口常见的（CH340 / CP210x / FTDI / STLink VCP）
    for p in ports:
        blob = ((p.description or "") + (p.hwid or "")).lower()
        if any(k in blob for k in ("ch340", "cp210", "ftdi", "usb serial",
                                   "stlink", "st-link", "usb-serial")):
            return p.device
    if real:
        return real[0]
    if bt:
        return bt[0]
    return None


def read_serial(port, baud, seconds, echo=True):
    print("打开 %s @ %d，采集 %g 秒 ..." % (port, baud, seconds))
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as e:
        print("[错误] 打不开串口 %s：%s" % (port, e))
        print("       检查：① 板子通电了吗 ② 串口被 XCOM/串口助手占着吗")
        print("       ③ 端口号对不对（用 --list 看）")
        return None

    lines = []
    t0 = time.time()
    buf = b""
    try:
        while time.time() - t0 < seconds:
            chunk = ser.read(4096)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, _, buf = buf.partition(b"\n")
                txt = decode_line(raw)
                lines.append((time.time() - t0, txt))
                if echo:
                    print("  %6.2fs  %s" % (time.time() - t0, txt))
    except KeyboardInterrupt:
        print("\n  手动停止。")
    finally:
        try:
            ser.close()
        except Exception:
            pass
    print("采集结束，共 %d 行。" % len(lines))
    return lines


def decode_line(raw):
    """板子发的中文是 GBK，优先按 GBK 解，失败再退 UTF-8"""
    raw = raw.rstrip(b"\r")
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_file(path):
    with open(path, "rb") as f:
        blob = f.read()
    lines = []
    for i, raw in enumerate(blob.split(b"\n")):
        if not raw.strip():
            continue
        lines.append((float(i), decode_line(raw)))
    print("读入 %s，共 %d 行。" % (path, len(lines)))
    return lines


# ---------------------------------------------------------------- 解析

def extract_series(lines, regex=None, cols=None):
    """把文本行变成若干条数值曲线"""
    series = []
    if regex:
        pat = re.compile(regex)
        vals = []
        for _, txt in lines:
            m = pat.search(txt)
            if m:
                try:
                    vals.append(float(m.group(1)))
                except (ValueError, IndexError):
                    pass
        if vals:
            series.append({"name": "数值", "values": vals})
        return series, []

    # 自动模式：统计每行的数字个数，取众数作为列数
    per_line = []
    for _, txt in lines:
        nums = RE_NUM.findall(txt)
        if nums:
            per_line.append(nums)
    if not per_line:
        return [], lines

    if cols:
        ncols = cols
    else:
        freq = {}
        for nums in per_line:
            freq[len(nums)] = freq.get(len(nums), 0) + 1
        ncols = max(freq.items(), key=lambda kv: (kv[1], kv[0]))[0]
    ncols = max(1, min(ncols, 8))

    buckets = [[] for _ in range(ncols)]
    skipped = []
    for nums in per_line:
        if len(nums) < ncols:
            skipped.append(nums)
            continue
        for i in range(ncols):
            try:
                buckets[i].append(float(nums[i]))
            except ValueError:
                pass
    for i, b in enumerate(buckets):
        if b:
            series.append({"name": "第 %d 列" % (i + 1), "values": b})
    return series, skipped


def downsample(vals, limit=1500):
    if len(vals) <= limit:
        return list(range(len(vals))), vals
    step = len(vals) / float(limit)
    xs, ys = [], []
    i = 0.0
    while int(i) < len(vals):
        xs.append(int(i))
        ys.append(vals[int(i)])
        i += step
    return xs, ys


# ---------------------------------------------------------------- 绘图

def make_chart(series, width=980, height=340):
    pad_l, pad_r, pad_t, pad_b = 62, 22, 22, 46
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b

    allv = [v for s in series for v in s["values"]]
    if not allv:
        return ""
    vmin, vmax = min(allv), max(allv)
    if vmax == vmin:
        vmax = vmin + 1
    span = vmax - vmin
    vmin -= span * 0.08
    vmax += span * 0.08

    nmax = max(len(s["values"]) for s in series)
    nmax = max(nmax, 2)

    def px(i):
        return pad_l + iw * i / float(nmax - 1)

    def py(v):
        return pad_t + ih * (1.0 - (v - vmin) / (vmax - vmin))

    A = []
    A.append('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:100%%;'
             'height:auto" xmlns="http://www.w3.org/2000/svg">' % (width, height))

    # 横向网格 + Y 轴刻度
    for k in range(6):
        v = vmin + (vmax - vmin) * k / 5.0
        y = py(v)
        A.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e7eb" '
                 'stroke-width="0.5"/>' % (pad_l, y, pad_l + iw, y))
        A.append('<text x="%d" y="%.1f" text-anchor="end" dominant-baseline="central" '
                 'font-family="ui-monospace,Consolas,monospace" font-size="11" '
                 'fill="#6b7280">%s</text>'
                 % (pad_l - 8, y, fmt_num(v)))

    # 纵向网格 + X 轴刻度
    for k in range(5):
        i = int((nmax - 1) * k / 4.0)
        x = px(i)
        A.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#f0f0f0" '
                 'stroke-width="0.5"/>' % (x, pad_t, x, pad_t + ih))
        A.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="'
                 'ui-monospace,Consolas,monospace" font-size="11" fill="#6b7280">%d</text>'
                 % (x, pad_t + ih + 18, i))

    # 边框
    A.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" '
             'stroke="#d1d5db" stroke-width="0.5"/>' % (pad_l, pad_t, iw, ih))

    # 曲线
    for si, s in enumerate(series):
        color = PALETTE[si % len(PALETTE)]
        xs, ys = downsample(s["values"])
        pts = " ".join("%.1f,%.1f" % (px(x), py(y)) for x, y in zip(xs, ys))
        A.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6" '
                 'stroke-linejoin="round" stroke-linecap="round"/>' % (pts, color))

    # 图例
    lx = pad_l + 6
    ly = pad_t + 14
    for si, s in enumerate(series):
        color = PALETTE[si % len(PALETTE)]
        A.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2.5"/>'
                 % (lx, ly, lx + 18, ly, color))
        A.append('<text x="%d" y="%d" dominant-baseline="central" font-family='
                 '"system-ui,sans-serif" font-size="12" fill="#374151">%s</text>'
                 % (lx + 24, ly, html.escape(s["name"])))
        lx += 24 + max(60, len(s["name"]) * 8 + 20)

    A.append("</svg>")
    return "\n".join(A)


def fmt_num(v):
    if abs(v) >= 10000 or (v != 0 and abs(v) < 0.01):
        return "%.3g" % v
    if abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return "%.3f" % v


def stats_block(series):
    rows = []
    for si, s in enumerate(series):
        v = s["values"]
        if not v:
            continue
        rows.append({
            "name": s["name"],
            "color": PALETTE[si % len(PALETTE)],
            "n": len(v),
            "min": min(v), "max": max(v),
            "mean": statistics.fmean(v),
            "sd": statistics.pstdev(v) if len(v) > 1 else 0.0,
            "last": v[-1],
        })
    return rows


def build_html(series, meta):
    rows = stats_block(series)
    A = []
    A.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    A.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A.append("<title>串口数据 · %s</title>" % html.escape(meta["title"]))
    A.append("""<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2328;--muted:#6b7280;--line:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;
--fg:#e6e8eb;--muted:#9aa4b2;--line:#2a2f3a}}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1060px;margin:0 auto}
h1{font-size:21px;font-weight:600;margin:0 0 6px}
h2{font-size:15px;font-weight:600;margin:26px 0 12px;padding-bottom:7px;
border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px;margin:18px 0}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:right;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums;font-family:ui-monospace,Consolas,monospace}
th{background:var(--bg);color:var(--muted);font-weight:500;font-size:12px}
td:first-child,th:first-child{text-align:left;font-family:inherit}
tr:last-child td{border-bottom:none}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;
margin-right:7px;vertical-align:middle}
.foot{color:var(--muted);font-size:12px;margin-top:32px;text-align:center}
</style></head><body><div class="wrap">""")
    A.append("<h1>%s</h1>" % html.escape(meta["title"]))
    for k, v in meta["subs"]:
        A.append("<div class='sub'>%s：%s</div>" % (html.escape(k), html.escape(v)))

    A.append("<div class='chart'>")
    A.append(make_chart(series))
    A.append("</div>")

    if rows:
        A.append("<h2>统计</h2>")
        A.append("<table><thead><tr><th>曲线</th><th>点数</th><th>最小</th>"
                 "<th>最大</th><th>极差</th><th>平均</th><th>标准差</th>"
                 "<th>最后值</th></tr></thead><tbody>")
        for r in rows:
            A.append("<tr><td><span class='dot' style='background:%s'></span>%s</td>"
                     "<td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (r["color"], html.escape(r["name"]), r["n"],
                        fmt_num(r["min"]), fmt_num(r["max"]),
                        fmt_num(r["max"] - r["min"]),
                        fmt_num(r["mean"]), fmt_num(r["sd"]), fmt_num(r["last"])))
        A.append("</tbody></table>")
        A.append("<div class='sub' style='margin-top:10px'>"
                 "标准差大 = 数据抖得厉害；极差大 = 有跳变或异常值。</div>")

    A.append("<div class='foot'>由 serial_plot.py 生成 · %s</div>"
             % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    A.append("</div></body></html>")
    return "\n".join(A)


# ---------------------------------------------------------------- 主程序

def main():
    ap = argparse.ArgumentParser(
        description="串口数据采集 + 可视化",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", "-l", action="store_true", help="列出可用串口后退出")
    ap.add_argument("--port", "-p", default=None, help="串口号，如 COM3")
    ap.add_argument("--baud", "-b", type=int, default=115200, help="波特率，默认 115200")
    ap.add_argument("--seconds", "-t", type=float, default=10.0, help="采集时长（秒）")
    ap.add_argument("--file", "-f", default=None, help="改从已有日志文件读取")
    ap.add_argument("--regex", "-r", default=None, help="正则，取第 1 个捕获组为数值")
    ap.add_argument("--cols", type=int, default=None, help="一行取前 N 个数字作为 N 条曲线")
    ap.add_argument("--out", "-o", default=None, help="输出 HTML 路径")
    ap.add_argument("--csv", action="store_true", help="同时输出 CSV")
    ap.add_argument("--no-echo", action="store_true", help="不实时打印原始行")
    args = ap.parse_args()

    if args.list:
        list_serial_ports()
        return 0

    # 取数据
    if args.file:
        if not os.path.isfile(args.file):
            print("[错误] 文件不存在：%s" % args.file)
            return 2
        lines = read_file(args.file)
        src = "文件 %s" % os.path.basename(args.file)
        title = os.path.splitext(os.path.basename(args.file))[0]
    else:
        port = args.port or pick_port()
        if not port:
            print("[错误] 没找到可用串口。用 --list 看看有哪些。")
            return 2
        if not args.port:
            print("自动选中串口：%s（要指定用 -p COMx）" % port)
        lines = read_serial(port, args.baud, args.seconds, echo=not args.no_echo)
        if lines is None:
            return 3
        if not lines:
            print("[错误] 一秒数据都没收到。检查波特率对不对（板子是多少？）、")
            print("       板子有没有在发数据、TX/RX 有没有接反。")
            return 4
        src = "%s @ %d" % (port, args.baud)
        title = "串口数据 %s" % port

    if not lines:
        print("[错误] 没有读到任何数据。")
        return 4

    # 解析
    series, skipped = extract_series(lines, args.regex, args.cols)
    if not series:
        print("[错误] 这些行里没找到数字。前 10 行原始内容：")
        for _, t in lines[:10]:
            print("    %s" % t)
        print("       如果数据格式特殊，用 -r 指定正则，例如：")
        print('         -r "Dist:\\s*(\\d+)"')
        return 5

    print("")
    print("=" * 62)
    print("  解析出 %d 条曲线" % len(series))
    print("=" * 62)
    for r in stats_block(series):
        print("  %-10s %5d 点   最小 %-10s 最大 %-10s 平均 %-10s 标准差 %s"
              % (r["name"], r["n"], fmt_num(r["min"]), fmt_num(r["max"]),
                 fmt_num(r["mean"]), fmt_num(r["sd"])))
    if skipped:
        print("  （%d 行数字个数对不上，已跳过）" % len(skipped))

    # 输出
    out = args.out
    if not out:
        stamp = datetime.now().strftime("%m%d_%H%M%S")
        out = os.path.join(os.path.expanduser("~"), "Desktop",
                           "串口数据_%s.html" % stamp)
        if not os.path.isdir(os.path.dirname(out)):
            out = os.path.abspath("串口数据_%s.html" % stamp)

    meta = {
        "title": title,
        "subs": [
            ("数据来源", src),
            ("总行数", str(len(lines))),
            ("解析出", "%d 条曲线，共 %d 个点"
             % (len(series), sum(len(s["values"]) for s in series))),
        ],
    }
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_html(series, meta))
    print("")
    print("  曲线图：%s" % out)

    if args.csv:
        csvp = os.path.splitext(out)[0] + ".csv"
        n = max(len(s["values"]) for s in series)
        with open(csvp, "w", encoding="utf-8-sig") as f:
            f.write(",".join(s["name"] for s in series) + "\n")
            for i in range(n):
                row = []
                for s in series:
                    row.append(str(s["values"][i]) if i < len(s["values"]) else "")
                f.write(",".join(row) + "\n")
        print("  CSV  ：%s" % csvp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
