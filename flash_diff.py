# -*- coding: utf-8 -*-
"""
flash_diff.py —— 把芯片读出来的 Flash 镜像 和 工程的 Intel HEX 逐字节比对

用途：验证「编译 → 烧录」的结果是否真的进了芯片；或者判断芯片里跑的到底是哪个工程。

用法：
    python flash_diff.py <芯片镜像.bin> <候选1.hex> [候选2.hex ...]
    python flash_diff.py --datapage <数据页镜像.bin>

输出：每个候选的匹配长度、差异字节数、首个差异地址，并按匹配度排序。
"""

import sys
import os
from pathlib import Path

# 输出统一走 UTF-8：否则 Windows 下 Python 默认用 GBK 输出，
# 遇到 ✓ / ✗ 这类字符直接抛 UnicodeEncodeError（实测踩过）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FLASH_BASE = 0x08000000


def parse_hex(path):
    """解析 Intel HEX，返回 {绝对地址: 字节} 的稀疏字典 + 覆盖区间"""
    data = {}
    upper = 0
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise ValueError(f"{path}:{lineno} 不是合法 HEX 行（缺冒号）")
            body = line[1:]
            try:
                rec = bytes.fromhex(body)
            except ValueError:
                raise ValueError(f"{path}:{lineno} HEX 内容非法")
            if len(rec) < 5:
                raise ValueError(f"{path}:{lineno} 记录太短")
            count = rec[0]
            addr = (rec[1] << 8) | rec[2]
            rtype = rec[3]
            payload = rec[4:4 + count]
            # 校验和：所有字节求和后低 8 位应为 0
            if (sum(rec) & 0xFF) != 0:
                raise ValueError(f"{path}:{lineno} 校验和错误")
            if rtype == 0x00:          # 数据
                base = upper + addr
                for i, b in enumerate(payload):
                    data[base + i] = b
            elif rtype == 0x01:        # 文件结束
                break
            elif rtype == 0x04:        # 扩展线性地址
                upper = ((payload[0] << 8) | payload[1]) << 16
            elif rtype == 0x02:        # 扩展段地址
                upper = ((payload[0] << 8) | payload[1]) << 4
            # 03/05 起点地址，忽略
    return data


def hex_to_image(data, base, size):
    """把稀疏字典铺平成 size 字节的数组，未覆盖处填 0xFF"""
    img = bytearray(b"\xff" * size)
    lo, hi = base, base + size
    for addr, b in data.items():
        if lo <= addr < hi:
            img[addr - lo] = b
    return img


def fmt(n):
    return f"{n:,}"


# --------------------------------------------------------------------------
# 数据页解码（flash_drv.h 的 StoreRecord_t）
# --------------------------------------------------------------------------

STORE_MAGIC = 0x5A5AA5A5
STORE_BITS = [(0, "LEFT_TURN"), (1, "RIGHT_TURN"), (2, "BEEP"), (3, "LED")]


def decode_store(path, base=0x0807F800):
    """解码一页数据页 dump（默认 2KB），按 StoreRecord_t 解释"""
    dp = Path(path).read_bytes()
    print(f"数据页镜像 : {path}")
    print(f"             基址 {base:#010x}，{len(dp)} 字节")
    print()

    nz = sum(1 for v in dp if v != 0xFF)
    if nz == 0:
        print("  整页都是 0xFF —— 这条数据页从未被写过。")
        return 0

    magic = int.from_bytes(dp[0:4], "little")
    flags = int.from_bytes(dp[4:8], "little")
    tlen = int.from_bytes(dp[8:12], "little")
    text = dp[12:44]
    cal = int.from_bytes(dp[44:46], "little", signed=True)
    cks = int.from_bytes(dp[46:48], "little")

    print(f"  非 0xFF 字节 : {nz} / {len(dp)}")
    print()
    print(f"  magic          = {magic:#010x}   "
          f"{'✓ 与 STORE_MAGIC 一致' if magic == STORE_MAGIC else '✗ 不是有效记录'}")
    print(f"  flags          = {flags:#010x}   "
          f"({' | '.join(n for b, n in STORE_BITS if flags & (1 << b)) or '全 0'})")
    if tlen <= 32:
        try:
            shown = text[:tlen].decode("gbk")
        except Exception:
            shown = text[:tlen].hex()
        print(f"  textLen        = {tlen}         text = {shown!r}")
    else:
        print(f"  textLen        = {tlen}         ⚠ 超出 32，记录可能已损坏")
    print(f"  calOffsetX100  = {cal}          "
          f"({'未标定' if cal == 0 else f'标定偏移 {cal/100:+.2f} ℃'})")
    print(f"  checksum       = {cks:#06x}")

    calc = sum(dp[:46]) & 0xFFFF
    print(f"  累加和(前46B)  = {calc:#06x}   "
          f"{'✓ 校验通过，这条记录是真的' if calc == cks else '✗ 校验不符，记录无效'}")
    return 0 if (calc == cks and magic == STORE_MAGIC) else 1


def compare(chip, chip_base, hex_path):
    hx = parse_hex(hex_path)
    if not hx:
        return None
    addrs = sorted(hx)
    lo, hi = addrs[0], addrs[-1] + 1
    covered = len(hx)
    span = hi - lo

    img = hex_to_image(hx, chip_base, len(chip))

    # ① 固件覆盖范围内的差异 —— 这才是"芯片里是不是这份固件"的判据
    in_diff = [i for i in range(span) if img[i] != chip[i]]
    # ② 固件覆盖范围之外、芯片却有内容的字节 —— 上一次烧录的残留
    out_nonff = sum(1 for i in range(span, len(chip)) if chip[i] != 0xFF)

    first = (chip_base + in_diff[0]) if in_diff else None

    # 连续匹配的字节数（从 Flash 基址开始数到第一个差异）
    prefix = 0
    for i in range(len(img)):
        if img[i] == chip[i]:
            prefix += 1
        else:
            break

    return {
        "path": hex_path,
        "size": os.path.getsize(hex_path),
        "lo": lo,
        "hi": hi,
        "span": span,
        "covered": covered,
        "fill": span - covered,          # HEX 里没写、芯片应为 0xFF 的洞
        "ndiff": len(in_diff),           # 覆盖范围内差异（判据）
        "noutside": out_nonff,           # 覆盖范围外的残留
        "first": first,
        "prefix": prefix,
    }


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--datapage":
        return decode_store(sys.argv[2])

    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    chip_path = sys.argv[1]
    candidates = sys.argv[2:]

    chip = Path(chip_path).read_bytes()
    print(f"芯片镜像 : {chip_path}")
    print(f"            {fmt(len(chip))} 字节  "
          f"({FLASH_BASE:#010x} ~ {FLASH_BASE + len(chip) - 1:#010x})")
    print()

    results = []
    for c in candidates:
        if not os.path.exists(c):
            print(f"[跳过] 文件不存在: {c}")
            continue
        try:
            r = compare(chip, FLASH_BASE, c)
        except Exception as e:
            print(f"[错误] {c}\n       {e}")
            continue
        if r:
            results.append(r)

    if not results:
        print("没有可比对的候选文件。")
        return 2

    results.sort(key=lambda r: (r["ndiff"], r["path"]))

    print("=" * 96)
    print(f"{'候选 HEX':<38}{'HEX大小':>9}{'覆盖内差异':>11}{'范围外残留':>11}{'首个差异':>14}")
    print("-" * 96)
    for r in results:
        name = os.path.basename(r["path"])
        parent = os.path.basename(os.path.dirname(os.path.dirname(r["path"])))
        label = f"{parent}/{name}" if parent else name
        if len(label) > 36:
            label = "..." + label[-33:]
        first = f"{r['first']:#010x}" if r["first"] is not None else "—"
        flag = " ★" if r["ndiff"] == 0 else ""
        print(f"{label:<38}{r['size']:>9,}{r['ndiff']:>11,}"
              f"{r['noutside']:>11,}{first:>14}{flag}")

    print("=" * 96)
    print("  覆盖内差异 = 固件自己覆盖的地址里有几个字节对不上（0 = 芯片里就是这份固件）")
    print("  范围外残留 = 固件覆盖范围之外、芯片却还有内容的字节数（上一次烧录的残骸）")
    print()

    best = results[0]
    print("详细：")
    for r in results[:5]:
        name = os.path.basename(r["path"])
        print(f"\n  {name}")
        print(f"    覆盖区间   : {r['lo']:#010x} ~ {r['hi'] - 1:#010x}"
              f"  (跨度 {fmt(r['span'])} 字节)")
        print(f"    已写数据   : {fmt(r['covered'])} 字节"
              f"，空洞(应为 0xFF) {fmt(r['fill'])} 字节")
        if r["ndiff"] == 0:
            print("    比对结果   : ★ 覆盖范围内逐字节一致 —— 芯片里有这份固件")
        else:
            pct = 100.0 * r["ndiff"] / max(r["span"], 1)
            print(f"    比对结果   : 覆盖范围内有 {fmt(r['ndiff'])} 字节不同"
                  f"（{pct:.2f}%），首个差异 {r['first']:#010x}")
            print(f"    起始连续相同: {fmt(r['prefix'])} 字节")
        print(f"    范围外残留 : {fmt(r['noutside'])} 字节"
              + ("（无，覆盖范围外是干净的 0xFF）" if r["noutside"] == 0 else ""))

    print()
    hits = [r for r in results if r["ndiff"] == 0]
    if hits:
        names = "、".join(os.path.basename(r["path"]) for r in hits)
        print(f"结论：芯片里含有以下固件（覆盖范围内逐字节一致）—— {names}")
        if len(hits) > 1:
            print("      多个命中说明它们共用同一段镜像（例如工程目录里同时存在旧副本）。")
        leftover = max(r["noutside"] for r in hits)
        if leftover > 0:
            print(f"      另外，覆盖范围之外还有 {fmt(leftover)} 字节非 0xFF 内容，"
                  f"是之前烧过的固件残骸（Keil 按扇区擦，不整片擦）。")
    else:
        print(f"结论：没有任何候选与芯片的覆盖范围完全一致。最接近的是 "
              f"{os.path.basename(best['path'])}（{fmt(best['ndiff'])} 字节不同）。")
        print("      可能是：芯片里烧的是更早/更新的构建产物，"
              "或者工程输出目录被重新编译过（hex 比芯片新）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
