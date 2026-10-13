"""给达芬奇导入用的最小 FCPXML 1.10 生成器（Minimal FCPXML 1.10 builder for DaVinci Resolve import）。

为什么用它：用接口一个个片段去搭时间线又慢又带不了转场和关键帧；写成 FCPXML 一次导入，叠化、推拉、音量关键帧都能带进去。
已处理的三个坑：素材自带起始时间码（asset 的 start 要写成时间码对应的秒数）、连接片段的 offset 要加父片段的时间码、
29.97 帧素材的丢帧时间码（分号分隔）要按丢帧规则换算。需要 ffprobe（环境变量 JA_FFMPEG_DIR 或系统 PATH）。


Times are handled in frames of the sequence rate (FPS). Media assets are probed with ffprobe.
Connected clips live inside a spine element with a lane; their offset is in the parent's local time.
"""
import json, os, subprocess, urllib.parse
from fractions import Fraction
from xml.sax.saxutils import escape, quoteattr

def _find_ffprobe():
    """先看环境变量 JA_FFMPEG_DIR，再看系统 PATH。"""
    import shutil
    d = os.environ.get("JA_FFMPEG_DIR")
    if d:
        for n in ("ffprobe.exe", "ffprobe"):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return shutil.which("ffprobe") or "ffprobe"


FFPROBE = _find_ffprobe()
FPS = 25


def t(frames):
    """frames (int or Fraction of frames) at FPS -> fcpxml rational time string"""
    fr = Fraction(frames).limit_denominator(100000)
    sec = fr / FPS
    if sec.denominator == 1:
        return f"{sec.numerator}s"
    return f"{sec.numerator}/{sec.denominator}s"


def tsec(x):
    """Fraction seconds -> fcpxml rational time string"""
    x = Fraction(x).limit_denominator(100000)
    return f"{x.numerator}s" if x.denominator == 1 else f"{x.numerator}/{x.denominator}s"


def sec(s):
    return Fraction(s).limit_denominator(1000) * FPS  # seconds -> frames


def file_url(path):
    p = os.path.abspath(path).replace("\\", "/")
    return "file:///" + urllib.parse.quote(p, safe="/:")


class Doc:
    def __init__(self, name):
        self.name = name
        self.res = []
        self.fmt_ids = {}
        self.assets = {}
        self.n = 1
        self.res.append(f'<format id="r1" name="FFVideoFormat1080p25" frameDuration="1/25s" width="1920" height="1080"/>')
        self.n = 2

    def _id(self):
        i = f"r{self.n}"; self.n += 1; return i

    def probe(self, path):
        o = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                       "format=duration:stream=codec_type,width,height,r_frame_rate,channels,sample_rate:stream_tags=timecode:format_tags=timecode:stream_side_data=rotation",
                                       "-of", "json", path], capture_output=True, text=True).stdout)
        v = next((s for s in o.get("streams", []) if s.get("codec_type") == "video"), None)
        a = next((s for s in o.get("streams", []) if s.get("codec_type") == "audio"), None)
        dur = float(o.get("format", {}).get("duration", 0) or 0)
        tc = None
        for st in o.get("streams", []):
            tc = tc or (st.get("tags") or {}).get("timecode")
        tc = tc or (o.get("format", {}).get("tags") or {}).get("timecode")
        self._tc = tc
        return v, a, dur

    def asset(self, path, still=False):
        if path in self.assets:
            return self.assets[path]
        v, a, dur = self.probe(path)
        aid = self._id()
        name = os.path.splitext(os.path.basename(path))[0]
        # 摄影机素材自带起始时间码：asset 的 start 必须写成时间码对应的秒数，片段的 start 也要加上它，否则达芬奇拒绝导入
        tc_sec = Fraction(0)
        if self._tc and v and not still:
            try:
                hh, mm, ss, ff = [int(x) for x in self._tc.replace(";", ":").split(":")]
                num, den = [int(x) for x in v["r_frame_rate"].split("/")]
                base = int(round(num / den))
                if base > 30:          # 50p / 60p 素材的时间码按半速帧计数
                    base //= 2; num //= 2 if num % 2 == 0 else 1
                frames = (hh * 3600 + mm * 60 + ss) * base + ff
                if ";" in self._tc and base == 30:      # 丢帧时间码（29.97）：每分钟丢 2 个帧号，整十分钟不丢；不扣掉的话入点会偏几秒
                    total_min = hh * 60 + mm
                    frames -= 2 * (total_min - total_min // 10)
                tc_sec = Fraction(frames * den, num)
            except Exception:
                tc_sec = Fraction(0)
        attrs = [f'id="{aid}"', f'name={quoteattr(name)}', f'start="{tsec(tc_sec)}"']
        if v:
            w, h = int(v["width"]), int(v["height"])
            rot = 0
            for sd in v.get("side_data_list", []) or []:
                if "rotation" in sd: rot = int(sd["rotation"])
            if abs(rot) == 90: w, h = h, w
            key = (w, h, "still" if still else v.get("r_frame_rate"))
            if key not in self.fmt_ids:
                fid = self._id()
                if still:
                    self.res.append(f'<format id="{fid}" name="FFVideoFormatRateUndefined" width="{w}" height="{h}"/>')
                else:
                    num, den = v["r_frame_rate"].split("/")
                    self.res.append(f'<format id="{fid}" frameDuration="{den}/{num}s" width="{w}" height="{h}"/>')
                self.fmt_ids[key] = fid
            attrs.append(f'format="{self.fmt_ids[key]}" hasVideo="1"')
        if still:
            attrs.append('duration="0s"')
        else:
            attrs.append(f'duration="{Fraction(dur).limit_denominator(1000).numerator}/{Fraction(dur).limit_denominator(1000).denominator}s"')
        if a:
            attrs.append(f'hasAudio="1" audioSources="1" audioChannels="{a.get("channels", 2)}" audioRate="{a.get("sample_rate", 48000)}"')
        self.res.append(f'<asset {" ".join(attrs)}><media-rep kind="original-media" src="{file_url(path)}"/></asset>')
        self.assets[path] = {"id": aid, "dur": dur, "video": bool(v), "audio": bool(a), "name": name, "tc": tc_sec}
        return self.assets[path]


def kf_param(name, keys, fmt, tc=0):
    """keys: list of (frame_local, value) ; fmt formats the value ; tc = asset start timecode in seconds"""
    ks = "".join(f'<keyframe time="{tsec(tc + Fraction(f, FPS))}" value="{fmt(v)}"/>' for f, v in keys)
    return f'<param name="{name}"><keyframeAnimation>{ks}</keyframeAnimation></param>'


class Clip:
    """One clip element. offset/start/duration in sequence frames."""

    def __init__(self, doc, path, offset, duration, start=0, lane=None, still=False, name=None,
                 scale_keys=None, pos_keys=None, volume_db=None, volume_keys=None, opacity=None, mute=False, role=None):
        self.a = doc.asset(path, still=still)
        self.offset, self.duration, self.start = offset, duration, start
        self.lane, self.still, self.name = lane, still, name or self.a["name"]
        self.scale_keys, self.pos_keys = scale_keys, pos_keys
        self.volume_db, self.volume_keys, self.opacity, self.mute, self.role = volume_db, volume_keys, opacity, mute, role
        self.children = []

    def xml(self):
        tag = "video" if self.still else "asset-clip"
        # 连接片段的 offset 写在父片段的源时间里；父片段带起始时间码时要加上它（offset_extra，单位秒）
        attrs = [f'ref="{self.a["id"]}"', f'offset="{tsec(getattr(self, "offset_extra", 0) + Fraction(self.offset, FPS))}"', f'name={quoteattr(self.name)}',
                 f'start="{tsec(self.a.get("tc", 0) + Fraction(self.start, FPS))}"', f'duration="{t(self.duration)}"']
        if self.lane is not None: attrs.append(f'lane="{self.lane}"')
        if self.role: attrs.append(f'audioRole="{self.role}"')
        if self.mute and not self.still: attrs.append('audioRole="dialogue" ')
        inner = []
        if self.opacity is not None:
            inner.append(f'<adjust-blend amount="{self.opacity}"/>')
        if self.scale_keys or self.pos_keys:
            params = ""
            base_scale = self.scale_keys[0][1] if self.scale_keys else 1
            base_pos = self.pos_keys[0][1] if self.pos_keys else (0, 0)
            if self.pos_keys and len(self.pos_keys) > 1:
                params += kf_param("position", [(self.start + f, v) for f, v in self.pos_keys], lambda v: f"{v[0]} {v[1]}", tc=self.a.get("tc", 0))
            if self.scale_keys and len(self.scale_keys) > 1:
                params += kf_param("scale", [(self.start + f, v) for f, v in self.scale_keys], lambda v: f"{v} {v}", tc=self.a.get("tc", 0))
            inner.append(f'<adjust-transform position="{base_pos[0]} {base_pos[1]}" scale="{base_scale} {base_scale}">{params}</adjust-transform>')
        if self.volume_db is not None or self.volume_keys:
            p = ""
            if self.volume_keys:
                p = kf_param("amount", [(self.start + f, v) for f, v in self.volume_keys], lambda v: f"{v}dB", tc=self.a.get("tc", 0))
            inner.append(f'<adjust-volume amount="{self.volume_db if self.volume_db is not None else 0}dB">{p}</adjust-volume>')
        for c in self.children:
            inner.append(c.xml())
        return f'<{tag} {" ".join(attrs)}>{"".join(inner)}</{tag}>'


class Transition:
    def __init__(self, offset, duration, name="Cross Dissolve"):
        self.offset, self.duration, self.name = offset, duration, name

    def xml(self):
        return (f'<transition name="{self.name}" offset="{t(self.offset)}" duration="{t(self.duration)}">'
                f'<filter-video ref="rFX" name="Cross Dissolve"/></transition>')


class Gap:
    def __init__(self, offset, duration):
        self.offset, self.duration = offset, duration
        self.children = []

    def xml(self):
        inner = "".join(c.xml() for c in self.children)
        return f'<gap name="Gap" offset="{t(self.offset)}" duration="{t(self.duration)}">{inner}</gap>'


def write(doc, spine, total, path, project="timeline", event="event"):
    res = list(doc.res)
    res.append('<effect id="rFX" name="Cross Dissolve" uid="FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"/>')
    body = "".join(e.xml() for e in spine)
    x = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n<resources>\n'
         + "\n".join(res) + '\n</resources>\n<library><event name=' + quoteattr(event) + '><project name=' + quoteattr(project) + '>'
         + f'<sequence format="r1" duration="{t(total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k"><spine>'
         + body + '</spine></sequence></project></event></library>\n</fcpxml>\n')
    with open(path, "w", encoding="utf-8") as f:
        f.write(x)
    return path
