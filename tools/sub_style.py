"""把字幕轨样式里脚本能写的三项（字体、大小、位置）写进达芬奇的时间线文件（.drt）。

用法：
    python sub_style.py <导出的.drt> <输出的.drt> --font "Microsoft YaHei" --style Regular --size 88 --x 960 --y 60
    （--width / --height 是时间线分辨率，默认 1920×1080；--weight 是 Qt 字重，Regular=50、Medium=57、Bold=75）

流程：达芬奇里把时间线导出成 DRT → 本脚本改写 → 媒体池「导入时间线」导回，得到一条新时间线。
导回后画面、校色、缩放位置原样保留（实测逐帧一致）；轨道的启用/停用状态不保留。

为什么要这样绕：达芬奇的脚本接口读不到也写不了字幕样式。样式实际存在字幕轨（Sm2TiTrack，Type=2）的
FieldsBlob 里：键值表 → EffectFiltersBA → protobuf。实测（达芬奇 21.0.4.5 免费版）：
    效果 136：参数 18 = Qt 字体描述串（只有家族名和字形名起作用）
              参数 19 = 检查器里的「大小」（整数；按 2160 高度计——1080p 时间线上 88 = 字高约 44 像素；默认约 116）
              参数 17 = 位置，归一化，x 从左、y 从下（y = 60/1080 时文字行中心离底边 60 像素）
    参数之间有空的占位项，顺序是 18、空、19、17；只写这三项时其余走默认值。
描边、投影、颜色的字段没有找到——这几项仍要在检查器「轨道样式」里手动设一次。
时间线里只能有一条字幕轨。字体必须是本机已安装的字体，名字按达芬奇字体下拉里显示的写。
"""
import argparse, re, struct, sys, zipfile

sys.stdout.reconfigure(encoding="utf-8")


def kd_decode(hx):
    b = bytes.fromhex(hx); hdr, _cnt = struct.unpack(">II", b[:8]); o = 8; ents = []
    while o < len(b) - 4:
        kl = struct.unpack(">I", b[o:o + 4])[0]
        if not (2 <= kl <= 200 and kl % 2 == 0 and o + 4 + kl <= len(b)):
            break
        key = b[o + 4:o + 4 + kl].decode("utf-16-be"); o += 4 + kl
        vt = struct.unpack(">I", b[o:o + 4])[0]; o += 4; st = b[o]; o += 1
        if vt in (0x0a, 0x0c):
            ln = struct.unpack(">I", b[o:o + 4])[0]; o += 4
            if ln == 0xffffffff:
                val = None
            else:
                val = b[o:o + ln]; o += ln
        elif vt == 0x01:
            val = b[o:o + 1]; o += 1
        elif vt in (0x02, 0x03):
            val = b[o:o + 4]; o += 4
        elif vt in (0x04, 0x05, 0x06):
            val = b[o:o + 8]; o += 8
        elif vt == 0x10:
            val = b[o:o + 9]; o += 9
        else:
            raise ValueError("不认识的字段类型 0x%x（键 %s）" % (vt, key))
        ents.append([key, vt, st, val])
    return hdr, ents


def kd_encode(hdr, ents):
    out = struct.pack(">II", hdr, len(ents))
    for key, vt, st, val in ents:
        kb = key.encode("utf-16-be"); out += struct.pack(">I", len(kb)) + kb + struct.pack(">IB", vt, st)
        if vt in (0x0a, 0x0c):
            out += struct.pack(">I", 0xffffffff) if val is None else struct.pack(">I", len(val)) + val
        else:
            out += val
    return out


def vi(v):
    o = b""
    while v > 0x7f:
        o += bytes([(v & 0x7f) | 0x80]); v >>= 7
    return o + bytes([v])


LEN = lambda f, p: vi((f << 3) | 2) + vi(len(p)) + p
VAR = lambda f, v: vi((f << 3) | 0) + vi(v)
PARAM = lambda pid, leaf: LEN(9, VAR(1, pid) + LEN(3, LEN(1, leaf)))


def style_blob(font_desc, size, x_norm, y_norm):
    body = VAR(1, 136)
    body += PARAM(18, LEN(3, font_desc.encode("utf-8")))
    body += LEN(9, b"")
    body += PARAM(19, VAR(1, int(size)))
    body += PARAM(17, LEN(7, struct.pack(">d", x_norm) + struct.pack(">d", y_norm)))
    pb = LEN(1, body)
    return struct.pack(">II", 2, len(pb) + 1) + b"\x80" + pb


def main():
    ap = argparse.ArgumentParser(description="把字体、大小、位置写进 DRT 的字幕轨样式")
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--font", required=True, help="字体家族名，按达芬奇字体下拉里显示的写")
    ap.add_argument("--style", default="Regular", help="字形名，如 Regular / Medium / Bold")
    ap.add_argument("--weight", type=int, default=None, help="Qt 字重；不给时按字形名取 Regular=50 / Medium=57 / Bold=75")
    ap.add_argument("--size", type=int, required=True, help="检查器里的「大小」")
    ap.add_argument("--x", type=float, default=None, help="检查器里的位置 X（像素）；不给时水平居中")
    ap.add_argument("--y", type=float, required=True, help="检查器里的位置 Y（像素，从画面底边往上）")
    ap.add_argument("--width", type=int, default=1920); ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()
    weight = a.weight if a.weight is not None else {"regular": 50, "medium": 57, "semibold": 63, "bold": 75}.get(a.style.lower(), 50)
    desc = "%s,%d,-1,5,%d,0,0,0,0,0,%s" % (a.font, a.size, weight, a.style)
    xn = 0.5 if a.x is None else a.x / a.width
    blob = style_blob(desc, a.size, xn, a.y / a.height)

    zin = zipfile.ZipFile(a.src); zout = zipfile.ZipFile(a.dst, "w", zipfile.ZIP_DEFLATED); done = 0
    for n in zin.namelist():
        data = zin.read(n)
        if n.startswith("SeqContainer/") and b"<Type>2</Type>" in data:
            x = data.decode("utf-8")
            if x.count("<Type>2</Type>") != 1:
                raise SystemExit("这条时间线有 %d 条字幕轨，本脚本只处理恰好一条的情况" % x.count("<Type>2</Type>"))
            i = x.find("<Type>2</Type>"); j = x.rfind("<Sm2TiTrack", 0, i)
            m = re.compile(r"<FieldsBlob>([0-9a-f]*)</FieldsBlob>").search(x, j)
            if not m or m.start() > i:
                raise SystemExit("没找到字幕轨的 FieldsBlob，文件结构和预期不一样（可能是别的达芬奇版本）")
            hdr, ents = kd_decode(m.group(1))
            ents = [e for e in ents if e[0] != "EffectFiltersBA"] + [["EffectFiltersBA", 0x0c, 0, blob]]
            x = x[:m.start(1)] + kd_encode(hdr, ents).hex() + x[m.end(1):]
            data = x.encode("utf-8"); done += 1
        zout.writestr(zin.getinfo(n), data)
    zout.close()
    if done != 1:
        raise SystemExit("没有找到带字幕轨的时间线（先在达芬奇里把字幕落到字幕轨再导出 DRT）")
    print("已写入：字体 %s / 大小 %d / 位置 x=%.4f y=%.4f（归一化） -> %s" % (desc, a.size, xn, a.y / a.height, a.dst))


if __name__ == "__main__":
    main()
