"""ja-davinci 自检：对着正在运行的达芬奇，把剪辑流程里常用的操作逐项跑一遍，出一份报告。

用法（用控制层自己的 Python 跑，安装脚本会给出完整命令）：
    <安装目录>\\davinci-resolve-mcp\\.venv\\Scripts\\python.exe scripts\\selftest.py

开始前：
  1. 达芬奇已打开，并且**新建并打开了一个专门的测试工程，工程名里带「测试」或 test**（自检会在里面建素材箱和时间线，不删除）。
  2. 免费版：已在达芬奇里点过「工作区 → 脚本 → resolve_bridge」。
自检只用仓库自带的合成素材（彩条、测试图、正弦音），不碰你的任何素材；不调用已知会让达芬奇闪退的操作。
结果：每一项是「通过 / 不通过 / 记录」。「记录」表示只是把这台机器上的实际行为记下来，不算失败。
"""
import argparse, asyncio, json, os, shutil, subprocess, sys, tempfile, time, urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(KIT, "selftest_media")


def load_report():
    for p in (os.path.join(KIT, "install_report.json"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "ja-davinci", "install_report.json")):
        if os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                pass
    return {}


ap = argparse.ArgumentParser()
ap.add_argument("--server-root", default=None, help="控制层目录（里面有 src/server.py 和 .venv）")
ap.add_argument("--out", default=None, help="报告和测试渲染件的输出目录")
ap.add_argument("--allow-any-project", action="store_true", help="跳过「工程名必须带 测试/test」的保护")
A = ap.parse_args()
REP = load_report()
ROOT = A.server_root or REP.get("server_root")
if not ROOT or not os.path.isfile(os.path.join(ROOT, "src", "server.py")):
    sys.exit("找不到控制层目录：用 --server-root 指定（里面要有 src/server.py）")
TAG = time.strftime("%m%d_%H%M%S")
OUT = A.out or os.path.join(tempfile.gettempdir(), "ja_davinci_selftest_" + TAG)
os.makedirs(OUT, exist_ok=True)
FFMPEG = None
for d in (REP.get("ffmpeg_dir"), os.environ.get("JA_FFMPEG_DIR")):
    if d and os.path.isfile(os.path.join(d, "ffmpeg.exe")):
        FFMPEG = os.path.join(d, "ffmpeg.exe")
FFMPEG = FFMPEG or shutil.which("ffmpeg")

env = dict(os.environ)
env.update({"DAVINCI_RESOLVE_BRIDGE": "1", "PYTHONIOENCODING": "utf-8"})
if REP.get("resolve_script_lib"):
    env["RESOLVE_SCRIPT_LIB"] = REP["resolve_script_lib"]
if FFMPEG:
    env["PATH"] = os.path.dirname(FFMPEG) + os.pathsep + env.get("PATH", "")

R = []


def rec(tid, what, ok, detail=""):
    R.append({"id": tid, "item": what, "result": {True: "通过", False: "不通过", None: "记录"}[ok], "detail": str(detail)[:400]})
    print("%-4s %-4s %s | %s" % (tid, R[-1]["result"], what, str(detail)[:240]), flush=True)


def crop_gray(path, t, x, y, w, h):
    """用 ffmpeg 取一帧的某个区域（灰度），返回 (宽, 高, bytes)；没有 ffmpeg 返回 None。画面按 1920×1080 取。"""
    if not FFMPEG:
        return None
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1", "-vf", "scale=1920:1080,crop=%d:%d:%d:%d" % (w, h, x, y),
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    return (w, h, r.stdout) if len(r.stdout) == w * h else None


def diff_bbox(g1, g2, th=50):
    """两帧相减，返回差异像素的包围盒 (x0, x1, y0, y1, 个数)。用「带字幕」和「不带字幕」两次渲染相减，背景再亮也只剩字幕。"""
    w, h, a = g1; b = g2[2]
    idx = [i for i in range(len(a)) if abs(a[i] - b[i]) > th]
    if not idx:
        return None
    xs = [i % w for i in idx]; ys = [i // w for i in idx]
    return (min(xs), max(xs), min(ys), max(ys), len(idx))


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=os.path.join(ROOT, ".venv", "Scripts", "python.exe"), args=[os.path.join(ROOT, "src", "server.py")], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            ntools = len((await s.list_tools()).tools)

            async def call(tool, action, timeout=180, **p):
                try:
                    res = await asyncio.wait_for(s.call_tool(tool, {"action": action, "params": p} if p else {"action": action}), timeout=timeout)
                except Exception as e:
                    return {"_err": "%s %s" % (type(e).__name__, str(e)[:200])}
                txt = " ".join(getattr(x, "text", "") for x in res.content)
                try:
                    j = json.loads(txt)
                except Exception:
                    return {"_err": "返回的不是 JSON：" + txt[:200]}
                d = j.get("result", j) if isinstance(j, dict) else {"_err": str(j)[:200]}
                if isinstance(d, dict) and d.get("error"):
                    e = d["error"]
                    d = {"_err": (e.get("message", "") if isinstance(e, dict) else str(e))[:260], "_code": e.get("code") if isinstance(e, dict) else None,
                         **{k: v for k, v in d.items() if k != "error"}}
                return d
            ok = lambda d: isinstance(d, dict) and "_err" not in d and d.get("success", True) is not False

            async def render(name, a, b, burn=True):
                d = await call("render", "prepare_render_job", target_dir=OUT, custom_name=name, from_preset="YouTube - 1080p", require_temp_target=False,
                               settings={"SelectAllFrames": False, "MarkIn": a, "MarkOut": b, "ExportSubtitle": burn, "SubtitleFormat": "BurnIn"})
                job = d.get("job_id")
                if not job:
                    return None, d.get("_err", "没有拿到渲染任务号")
                await call("render", "start", job_ids=[job])
                st = {}
                for _ in range(90):
                    st = await call("render", "get_job_status", job_id=job)
                    if st.get("CompletionPercentage") == 100 or "_err" in st:
                        break
                    await asyncio.sleep(2)
                await call("render", "delete_job", job_id=job)
                f = os.path.join(OUT, name + ".mp4")
                return (f if os.path.isfile(f) and os.path.getsize(f) > 10000 else None), st.get("JobStatus")

            d = await call("resolve_control", "get_version")
            ver = d.get("version_string", "")
            rec("S01", "连上达芬奇并读到版本", ok(d), "%s %s｜控制层 %s，工具 %d 个%s" % (d.get("product"), ver, init.serverInfo.version, ntools, "" if ok(d) else "｜" + d.get("_err", "")))
            if not ok(d):
                print("\n连不上。免费版请确认已在达芬奇里点了「工作区 → 脚本 → resolve_bridge」；达芬奇 21.1 及以后的免费版不支持脚本，接不上。")
                return
            try:
                vt = tuple(int(x) for x in ver.split(".")[:2])
            except Exception:
                vt = (0, 0)
            d = await call("project_manager", "get_current")
            pname = d.get("name") or ""
            guard = A.allow_any_project or ("测试" in pname) or ("test" in pname.lower())
            rec("S02", "当前工程是测试工程（名字带「测试」或 test）", guard, pname)
            if not guard:
                print("\n为了不碰你的正式工程，自检到此停止。请在达芬奇里新建并打开一个工程，名字里带「测试」，再跑一次。")
                return

            folder = "_ja_selftest_" + TAG
            d = await call("media_pool", "add_subfolder", name=folder, parent_path="Master")
            d2 = await call("media_pool", "set_current_folder", path="Master/" + folder)
            rec("S03", "建素材箱并切过去", ok(d) and ok(d2), d.get("_err") or d2.get("_err") or folder)
            files = ["clipA_25.mp4", "clipB_2997df.mp4", "overlay_alpha.mov", "voice.wav", "test.srt"]
            d = await call("media_pool", "import_media", paths=[os.path.join(MEDIA, x).replace("\\", "/") for x in files])
            rec("S04", "导入 5 个素材（含一份 SRT）", ok(d) and d.get("imported") == 5, "imported=%s %s" % (d.get("imported"), d.get("_err", "")))
            d = await call("folder", "get_clips", path="Master/" + folder)
            clips = {c["name"]: c["id"] for c in d.get("clips", [])} if ok(d) else {}
            srt_id = clips.get("test")
            rec("S05", "SRT 在媒体池里被认成字幕素材", bool(srt_id), sorted(clips))

            tpl = open(os.path.join(MEDIA, "timeline_template.fcpxml"), encoding="utf-8").read()
            url = "file:///" + urllib.parse.quote(MEDIA.replace("\\", "/"), safe="/:") + "/"
            fx = os.path.join(OUT, "selftest.fcpxml")
            open(fx, "w", encoding="utf-8").write(tpl.replace("__MEDIA_URL__", url))
            d = await call("timeline", "import_timeline_checked", path=fx.replace("\\", "/"), timeline_name="ja_selftest_" + TAG, import_source_clips=True, require_temp_path=False)
            rec("S06", "导入时间线文件（FCPXML：两段主片段 + 一段 B-roll + 透明叠加层 + 人声）", ok(d) and (d.get("media") or {}).get("offline") == 0, "media=%s %s" % (d.get("media"), d.get("_err", "")))
            d = await call("timeline", "get_current")
            t0 = d.get("start_frame", 0)
            rec("S07", "时间线长度 250 帧", ok(d) and d.get("end_frame", 0) - t0 == 250, "%s %s-%s" % (d.get("name"), t0, d.get("end_frame")))
            cnt = {}
            for tt in ("video", "audio", "subtitle"):
                cnt[tt] = (await call("timeline", "get_track_count", track_type=tt)).get("count")
            rec("S08", "轨道数：视频 3 条", cnt.get("video") == 3, cnt)
            d = await call("timeline", "clip_where", track_type="video", track_index=2)
            cl = (d.get("clips") or [{}])[0]
            rec("S09", "29.97 丢帧时间码素材的入点落在 1.00 秒", ok(d) and abs((cl.get("source_start_seconds") or -9) - 1.0) < 0.05, "source_start_seconds=%s" % cl.get("source_start_seconds"))

            cdl = {"NodeIndex": "1", "Slope": "1.2 1.0 0.8", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1.0"}
            d = await call("timeline_item_color", "safe_set_cdl", track_type="video", track_index=1, item_index=0, cdl=cdl)
            rec("S10", "给单个片段写校色（CDL）", ok(d), d.get("_err", "") or "ok")
            d = await call("timeline_item", "set_transform", track_type="video", track_index=1, item_index=1, ZoomX=1.5, ZoomY=1.5, Pan=480, Tilt=86.4)
            d2 = await call("timeline_item", "get_transform", track_type="video", track_index=1, item_index=1)
            rec("S11", "写缩放和位置（放大镜头）并读回", ok(d) and abs((d2.get("ZoomX") or 0) - 1.5) < 1e-3 and abs((d2.get("Pan") or 0) - 480) < 1e-3, {k: d2.get(k) for k in ("ZoomX", "Pan", "Tilt")})

            await call("timeline", "add_track", track_type="subtitle")
            for tt in ("video", "audio"):
                for i in range(1, (cnt.get(tt) or 0) + 1):
                    await call("timeline", "set_track_lock", track_type=tt, index=i, locked=True)
            d = await call("media_pool", "append_to_timeline", clip_ids=[srt_id])
            d1 = await call("timeline_item", "get_start", track_type="subtitle", track_index=1, item_index=0)
            d2 = await call("timeline_item", "get_end", track_type="subtitle", track_index=1, item_index=3)
            for tt in ("video", "audio"):
                for i in range(1, (cnt.get(tt) or 0) + 1):
                    await call("timeline", "set_track_lock", track_type=tt, index=i, locked=False)
            rec("S12", "SRT 落到原生字幕轨（先锁住画面轨和音轨再追加），位置和 SRT 一致", ok(d) and d1.get("start") == t0 + 10 and d2.get("end") == t0 + 240,
                "首条起点 %s（应为 %s），末条止点 %s（应为 %s）%s" % (d1.get("start"), t0 + 10, d2.get("end"), t0 + 240, d.get("_err", "")))
            d = await call("timeline", "get_transcript", with_timecodes=True)
            rec("S13", "读回字幕文字 4 条", ok(d) and d.get("cue_count") == 4, "cue_count=%s" % d.get("cue_count"))
            d = await call("timeline_item", "set_clip_enabled", track_type="video", track_index=3, item_index=0, enabled=False)
            d2 = await call("timeline_item", "get_clip_enabled", track_type="video", track_index=3, item_index=0)
            rec("S14", "停用单个片段并读回", ok(d) and d2.get("enabled") is False, "enabled=%s" % d2.get("enabled"))
            await call("timeline_item", "set_clip_enabled", track_type="video", track_index=3, item_index=0, enabled=True)

            f, st = await render("selftest_burn_" + TAG, t0, t0 + 100)
            rec("S15", "渲染 4 秒 MP4", bool(f), "%s %s" % (st, os.path.getsize(f) if f else ""))
            if f:
                f0, _ = await render("selftest_nosub_" + TAG, t0, t0 + 100, burn=False)
                g, g0 = crop_gray(f, 1.0, 0, 540, 1920, 540), (crop_gray(f0, 1.0, 0, 540, 1920, 540) if f0 else None)
                if g is None or g0 is None:
                    rec("S16", "原生字幕压进画面（需要 ffmpeg 才能核对像素）", None, "没找到 ffmpeg 或对照渲染失败，跳过像素核对")
                else:
                    bb = diff_bbox(g, g0)
                    # 上游记录过 19.x 上「压制字幕」的渲染设置不起作用，所以 21.0 之前的版本只记录不判失败
                    rec("S16", "原生字幕压进画面（带字幕与不带字幕两次渲染相减）", (bool(bb) and bb[4] > 300) if vt >= (21, 0) else None,
                        "字幕像素 %s%s" % (bb[4] if bb else 0, "" if vt >= (21, 0) else "（达芬奇 %s，低于 21.0，只记录）" % ver))

            drt = os.path.join(OUT, "selftest.drt").replace("\\", "/")
            d = await call("timeline", "export_timeline_checked", path=drt, format="DRT", require_temp_path=False)
            rec("S17", "把时间线导出成 DRT", ok(d) and os.path.isfile(drt), d.get("_err", "") or os.path.getsize(drt))
            if os.path.isfile(drt):
                styled = drt[:-4] + "_styled.drt"
                r1 = subprocess.run([sys.executable, os.path.join(KIT, "tools", "sub_style.py"), drt, styled, "--font", "Microsoft YaHei", "--style", "Regular", "--size", "88", "--y", "60"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                rec("S18", "改写 DRT 里的字幕样式（微软雅黑、大小 88、位置 Y 60）", r1.returncode == 0, (r1.stdout + r1.stderr).strip()[-200:])
                await call("media_pool", "set_current_folder", path="Master/" + folder)
                d = await call("media_pool", "import_timeline", path=styled)
                rec("S19", "把改写后的 DRT 导回成新时间线", ok(d), d.get("name") or d.get("_err"))
                cur = await call("timeline", "get_current")
                t1 = cur.get("start_frame", 0)
                f2, st = await render("selftest_styled_" + TAG, t1, t1 + 60)
                f3, _ = await render("selftest_styled_nosub_" + TAG, t1, t1 + 60, burn=False)
                g = crop_gray(f2, 1.0, 0, 880, 1920, 200) if f2 else None
                g3 = crop_gray(f3, 1.0, 0, 880, 1920, 200) if f3 else None
                if g is None or g3 is None:
                    rec("S20", "写入样式后字幕的实际尺寸（需要 ffmpeg）", None, "渲染 %s；没有 ffmpeg 或渲染失败，跳过" % st)
                else:
                    bb = diff_bbox(g, g3)
                    if bb:
                        h = bb[3] - bb[2]; cy = 200 - (bb[2] + bb[3]) / 2
                        rec("S20", "写入样式后：字高约 40 像素、行中心离底约 60 像素", (abs(cy - 60) < 5 and 32 <= h <= 50) if vt >= (21, 0) else None, "字高 %d，行中心离底 %.1f" % (h, cy))
                    else:
                        rec("S20", "写入样式后字幕的实际尺寸", False if vt >= (21, 0) else None, "底部没找到字幕像素")
                d = await call("timeline", "set_track_enable", track_type="video", index=3, enabled=False)
                d2 = await call("timeline", "get_track_enabled", track_type="video", index=3)
                rec("S21", "在 DRT 导回的时间线上停用轨道（已知：报成功但不生效，要改用停用片段）", None, "读回 enabled=%s" % d2.get("enabled"))
                await call("timeline", "set_track_enable", track_type="video", index=3, enabled=True)

            d = await call("timeline", "list")
            names = [t["name"] for t in d.get("timelines", [])] if ok(d) else []
            d2 = await call("media_pool", "get_current_folder")
            rec("S22", "控制层自动留的归档副本，以及当前素材箱是否被切到 Archive", None, "归档副本 %d 条；当前素材箱=%s" % (sum("_archived_" in x for x in names), d2.get("name")))
            d = await call("project_manager", "save")
            rec("S23", "保存工程", ok(d), d.get("_err", "") or "ok")

asyncio.run(main())
n_ok = sum(1 for r in R if r["result"] == "通过"); n_bad = sum(1 for r in R if r["result"] == "不通过"); n_rec = sum(1 for r in R if r["result"] == "记录")
json.dump({"time": TAG, "server_root": ROOT, "ffmpeg": FFMPEG, "results": R, "summary": {"pass": n_ok, "fail": n_bad, "record": n_rec}},
          open(os.path.join(OUT, "selftest_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "selftest_report.md"), "w", encoding="utf-8") as fh:
    fh.write("# ja-davinci 自检报告（%s）\n\n通过 %d 项，不通过 %d 项，记录 %d 项。\n\n| 编号 | 项目 | 结果 | 细节 |\n|---|---|---|---|\n" % (TAG, n_ok, n_bad, n_rec))
    for r in R:
        fh.write("| %s | %s | %s | %s |\n" % (r["id"], r["item"], r["result"], r["detail"].replace("|", "｜").replace("\n", " ")))
print("\n通过 %d / 不通过 %d / 记录 %d" % (n_ok, n_bad, n_rec))
print("报告：" + os.path.join(OUT, "selftest_report.md"))
sys.exit(1 if n_bad or not R else 0)
