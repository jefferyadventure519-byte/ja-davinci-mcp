"""ja-davinci 安装脚本：把「让 Claude 操作达芬奇」需要的东西装好，并把每一步的结果写成一份报告。

它做的事（每一步都可以单独跳过）：
  1. 检查环境：Windows、Python、达芬奇的版本、git、ffmpeg。
  2. 从上游官方仓库下载**固定版本**的控制层（davinci-resolve-mcp v4.10.4，MIT 许可），不用本仓库转存的副本。
  3. 给控制层建独立的 Python 运行环境并装依赖。
  4. 把软件内桥接脚本装进达芬奇的脚本目录（免费版靠它）。
  5. 在 Claude Code 的配置里注册这个控制层（先备份，原子写入，读回核对；已有同名注册且内容不同时不动它，除非加 --force）。
  6. 把踩坑规则作为一个技能装到 ~/.claude/skills/ja-davinci。

用法：
    python scripts/install.py --check-only          只检查环境，不改任何东西
    python scripts/install.py                       完整安装（默认装到 %LOCALAPPDATA%\\ja-davinci）
    python scripts/install.py --dir D:\\ja-davinci    指定安装目录
    python scripts/install.py --uninstall           去掉注册和技能（下载的文件留着，自己删目录即可）
只用 Python 标准库。只在 Windows 上验证过。
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UPSTREAM = "https://github.com/samuelgursky/davinci-resolve-mcp"
PIN_TAG, PIN_VERSION, PIN_COMMIT = "v4.10.4", "4.10.4", "8911bd24aee76894238924317a461005dd013d94"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
SERVER_NAME = "davinci-resolve"
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kit": KIT, "pin": PIN_TAG, "steps": [], "blockers": [], "warnings": [], "human_todo": []}


def step(name, ok, detail=""):
    REPORT["steps"].append({"step": name, "ok": ok, "detail": str(detail)[:600]})
    print("[%s] %s%s" % ({True: "完成", False: "失败", None: "跳过"}[ok], name, ("：" + str(detail)[:300]) if detail else ""), flush=True)


def run(cmd, timeout=900, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 999, "", "%s: %s" % (type(e).__name__, e)


def ps(script):
    rc, out, err = run(["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script], timeout=60)
    return out.strip() if rc == 0 else ""


def find_resolve(explicit=None):
    cands = [explicit, ps("(Get-Process Resolve -ErrorAction SilentlyContinue | Select-Object -First 1).Path"),
             r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            info = ps("$v=(Get-Item -LiteralPath '%s').VersionInfo; $v.ProductVersion; $v.ProductName" % c.replace("'", "''")).splitlines()
            return {"exe": c, "version": (info[0].strip() if info else ""), "product": (info[1].strip() if len(info) > 1 else "")}
    return None


def registered_pythons():
    out = []
    try:
        import winreg
        for hive, hn in ((winreg.HKEY_CURRENT_USER, "当前用户"), (winreg.HKEY_LOCAL_MACHINE, "所有用户")):
            try:
                k = winreg.OpenKey(hive, r"SOFTWARE\Python\PythonCore"); i = 0
                while True:
                    try:
                        out.append("%s %s" % (winreg.EnumKey(k, i), hn)); i += 1
                    except OSError:
                        break
            except OSError:
                pass
    except ImportError:
        pass
    return out


def check_env(args):
    env = {"os": sys.platform, "python": sys.version.split()[0], "python_exe": sys.executable}
    if sys.platform != "win32":
        REPORT["warnings"].append("这套安装只在 Windows 上验证过；Mac 上达芬奇找 Python 的方式不同，没有测。")
    if sys.version_info < (3, 10):
        REPORT["blockers"].append("运行本脚本的 Python 是 %s，控制层需要 3.10 或更新。请从 python.org 安装 3.11–3.13 后重试。" % env["python"])
    env["registered_pythons"] = registered_pythons()
    if sys.platform == "win32" and not env["registered_pythons"]:
        REPORT["warnings"].append("注册表里没有登记任何 Python。达芬奇的「工作区 → 脚本」菜单只认 python.org 安装包装的 Python；没有的话桥接脚本不会出现在菜单里。")
    rs = find_resolve(args.resolve_exe)
    env["resolve"] = rs
    if not rs:
        REPORT["warnings"].append("没找到达芬奇（没在运行，也不在默认安装位置）。装好并打开达芬奇后再跑一次，或用 --resolve-exe 指定 Resolve.exe 的位置。")
    else:
        m = re.match(r"(\d+)\.(\d+)", rs["version"] or "")
        vt = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        env["resolve_version_tuple"] = list(vt)
        studio = "studio" in (rs["product"] or "").lower() or args.edition == "studio"
        if vt >= (21, 1) and not studio:
            REPORT["blockers"].append("这台电脑的达芬奇是 %s。达芬奇 21.1 起免费版不再支持 Python 脚本，免费版接不上；只有 21.0.x 及更早的免费版，或 Studio 版可以用。如果你用的是 Studio，请加 --edition studio 再跑。" % rs["version"])
        elif vt and vt < (18, 5):
            REPORT["warnings"].append("达芬奇 %s 比控制层声明支持的 18.5 还旧，部分功能可能不可用。" % rs["version"])
        REPORT["human_todo"].append("不要把达芬奇升级到 21.1 或更新的免费版，升了就接不上（除非换成 Studio）。")
    env["git"] = shutil.which("git")
    ff = None
    for d in (args.ffmpeg_dir, os.path.join(args.dir, "ffmpeg", "bin")):
        if d and os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            ff = d
    env["ffmpeg_dir"] = ff or (os.path.dirname(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else None)
    if not env["ffmpeg_dir"]:
        REPORT["warnings"].append("没找到 ffmpeg。安装和基本操作不受影响，但出预览、抽帧核对、自检里的像素核对需要它。可以从 ffmpeg.org 的下载页拿 Windows 版，解压后用 --ffmpeg-dir 指给本脚本。")
    step("检查环境", not REPORT["blockers"], "Python %s；达芬奇 %s；git %s；ffmpeg %s" % (env["python"], (rs or {}).get("version", "没找到"), "有" if env["git"] else "没有", "有" if env["ffmpeg_dir"] else "没有"))
    return env


def fetch_upstream(root, env):
    dst = os.path.join(root, "davinci-resolve-mcp")
    pkg = os.path.join(dst, "package.json")
    if os.path.isfile(pkg):
        try:
            if json.load(open(pkg, encoding="utf-8")).get("version") == PIN_VERSION:
                step("下载控制层 " + PIN_TAG, True, "已存在，版本一致：" + dst); return dst
        except Exception:
            pass
        step("下载控制层 " + PIN_TAG, False, dst + " 已存在但不是 " + PIN_VERSION + "；请先把这个目录改名或删掉再跑"); return None
    os.makedirs(root, exist_ok=True)
    if env.get("git"):
        rc, out, err = run([env["git"], "clone", "--branch", PIN_TAG, "--depth", "1", UPSTREAM + ".git", dst])
        if rc == 0:
            rc2, head, _ = run([env["git"], "-C", dst, "rev-parse", "HEAD"])
            if head.strip() != PIN_COMMIT:
                step("下载控制层 " + PIN_TAG, False, "下载到的提交 %s 和固定的 %s 不一致，已停止" % (head.strip()[:12], PIN_COMMIT[:12])); return None
            step("下载控制层 " + PIN_TAG, True, "git 克隆到 " + dst); return dst
        shutil.rmtree(dst, ignore_errors=True)
    url = UPSTREAM + "/archive/refs/tags/" + PIN_TAG + ".zip"
    try:
        tmp = os.path.join(tempfile.gettempdir(), "davinci-resolve-mcp-%s.zip" % PIN_VERSION)
        urllib.request.urlretrieve(url, tmp)
        with zipfile.ZipFile(tmp) as z:
            top = z.namelist()[0].split("/")[0]
            z.extractall(root)
        os.replace(os.path.join(root, top), dst)
        if json.load(open(pkg, encoding="utf-8")).get("version") != PIN_VERSION:
            raise RuntimeError("解压出来的版本不是 " + PIN_VERSION)
        step("下载控制层 " + PIN_TAG, True, "下载压缩包并解压到 " + dst); return dst
    except Exception as e:
        step("下载控制层 " + PIN_TAG, False, "从 GitHub 下载失败（%s）。多半是网络访问不了 github.com。" % e); return None


def make_venv(server_root, args):
    py = os.path.join(server_root, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        rc, out, err = run([sys.executable, "-m", "venv", os.path.join(server_root, ".venv")])
        if rc != 0:
            step("建运行环境", False, err[-300:]); return None
    pkgs = ["mcp[cli]>=1.30,<2", "pyaaf2>=1.7.1"]
    for index in ([args.pip_index] if args.pip_index else [None, PIP_MIRROR]):
        cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + (["-i", index] if index else []) + pkgs
        rc, out, err = run(cmd, timeout=1200)
        if rc == 0:
            rc2, out2, _ = run([py, "-c", "import importlib.metadata as m; print(m.version('mcp'))"])
            step("建运行环境并装依赖", True, "mcp %s%s" % (out2.strip(), "（用了镜像源）" if index else "")); return py
    step("建运行环境并装依赖", False, "pip 安装失败：" + err[-300:]); return None


def install_bridge(server_root, py):
    rc, out, err = run([py, os.path.join(server_root, "scripts", "install_resolve_bridge.py")], cwd=server_root)
    if rc != 0:
        step("把桥接脚本装进达芬奇", False, (err or out)[-300:]); return False
    try:
        j = json.loads(out[out.find("{"):out.rfind("\n}") + 2])
        lists_py = j.get("python", {}).get("resolve_will_list_python_scripts")
        step("把桥接脚本装进达芬奇", True, "装到 %d 处；达芬奇能否列出 Python 脚本：%s" % (len(j.get("installed", [])), lists_py))
        if lists_py is False:
            REPORT["warnings"].append("桥接脚本已装，但达芬奇可能列不出 Python 脚本：" + str(j.get("python", {}).get("advice")))
    except Exception:
        step("把桥接脚本装进达芬奇", True, "脚本已运行（输出没解析出来）")
    REPORT["human_todo"].append("重开达芬奇并打开一个工程，然后点「工作区 → 脚本 → resolve_bridge」。以后每次打开达芬奇都要点这一次。")
    return True


def atomic_write_json(path, obj, indent):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def register(server_root, env, args, remove=False):
    cfg = args.claude_config or os.path.join(os.path.expanduser("~"), ".claude.json")
    entry = {"command": os.path.join(server_root, ".venv", "Scripts", "python.exe"), "args": [os.path.join(server_root, "src", "server.py")], "env": {}}
    if args.edition != "studio":
        entry["env"]["DAVINCI_RESOLVE_BRIDGE"] = "1"
    rs = env.get("resolve")
    if rs and os.path.isfile(os.path.join(os.path.dirname(rs["exe"]), "fusionscript.dll")):
        entry["env"]["RESOLVE_SCRIPT_LIB"] = os.path.join(os.path.dirname(rs["exe"]), "fusionscript.dll")
        REPORT["resolve_script_lib"] = entry["env"]["RESOLVE_SCRIPT_LIB"]
    if env.get("ffmpeg_dir") and not shutil.which("ffmpeg"):
        entry["env"]["PATH"] = env["ffmpeg_dir"] + ";%PATH%"
    if os.path.isfile(cfg):
        raw = open(cfg, encoding="utf-8").read()
        try:
            j = json.loads(raw)
        except Exception as e:
            step("在 Claude 里注册", False, "%s 不是合法的 JSON，没有改动：%s" % (cfg, e)); return False
        indent = 2 if raw.startswith("{\n  ") else None
    else:
        j, indent = {}, 2
    servers = j.setdefault("mcpServers", {})
    old = servers.get(SERVER_NAME)
    if remove:
        if old is None:
            step("去掉 Claude 里的注册", None, "本来就没有"); return True
        if os.path.isfile(cfg):
            shutil.copy2(cfg, cfg + ".bak_ja_davinci_" + time.strftime("%Y%m%d_%H%M%S"))
        servers.pop(SERVER_NAME); atomic_write_json(cfg, j, indent)
        step("去掉 Claude 里的注册", SERVER_NAME not in json.load(open(cfg, encoding="utf-8")).get("mcpServers", {}), cfg); return True
    if old == entry:
        step("在 Claude 里注册", True, "已经是这份配置，没有改动"); return True
    if old is not None and not args.force:
        step("在 Claude 里注册", False, "已经有一个同名的 %s 注册，指向 %s。没有动它；确认要替换的话加 --force 重跑（会先备份）。" % (SERVER_NAME, old.get("command"))); return False
    if os.path.isfile(cfg):
        shutil.copy2(cfg, cfg + ".bak_ja_davinci_" + time.strftime("%Y%m%d_%H%M%S"))
    servers[SERVER_NAME] = entry
    atomic_write_json(cfg, j, indent)
    back = json.load(open(cfg, encoding="utf-8")).get("mcpServers", {})
    good = back.get(SERVER_NAME) == entry and len(back) == len(servers)
    step("在 Claude 里注册", good, "%s（其余 %d 个注册原样保留）" % (cfg, len(back) - 1))
    desk = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
    try:
        if SERVER_NAME in json.load(open(desk, encoding="utf-8")).get("mcpServers", {}):
            REPORT["warnings"].append("桌面版配置 %s 里也有一个同名注册，两处都在会让同一套工具被加载两次。本脚本没有动它，请只留一处。" % desk)
    except Exception:
        pass
    REPORT["human_todo"].append("完全退出并重开 Claude，新会话里才看得到达芬奇的工具。")
    return good


def install_skill(args, remove=False):
    dst = os.path.join(args.skills_dir or os.path.join(os.path.expanduser("~"), ".claude", "skills"), "ja-davinci")
    if remove:
        if os.path.isdir(dst):
            shutil.rmtree(dst); step("去掉技能 ja-davinci", True, dst)
        else:
            step("去掉技能 ja-davinci", None, "本来就没有")
        return
    src = os.path.join(KIT, "skill", "ja-davinci")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), os.path.join(dst, f)); n += 1
    with open(os.path.join(dst, "kit_location.txt"), "w", encoding="utf-8") as fh:
        fh.write(KIT + "\n")
    step("安装技能 ja-davinci（踩坑规则）", True, "%d 个文件 -> %s" % (n, dst))


def main():
    ap = argparse.ArgumentParser(description="ja-davinci 安装脚本")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ja-davinci"))
    ap.add_argument("--check-only", action="store_true"); ap.add_argument("--uninstall", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--edition", choices=["free", "studio"], default="free", help="达芬奇是免费版（走软件内桥接）还是 Studio（直接外部脚本）")
    ap.add_argument("--resolve-exe", default=None); ap.add_argument("--ffmpeg-dir", default=None); ap.add_argument("--pip-index", default=None)
    ap.add_argument("--skip-bridge", action="store_true"); ap.add_argument("--skip-register", action="store_true"); ap.add_argument("--skip-skill", action="store_true")
    ap.add_argument("--claude-config", default=None, help="测试用：改写这个文件而不是 ~/.claude.json")
    ap.add_argument("--skills-dir", default=None, help="测试用：技能装到这个目录而不是 ~/.claude/skills")
    args = ap.parse_args()
    args.dir = os.path.abspath(args.dir)
    REPORT["install_dir"] = args.dir
    env = check_env(args)
    REPORT["env"] = env; REPORT["ffmpeg_dir"] = env.get("ffmpeg_dir")
    if args.uninstall:
        register(os.path.join(args.dir, "davinci-resolve-mcp"), env, args, remove=True); install_skill(args, remove=True)
    elif not args.check_only and not REPORT["blockers"]:
        root = fetch_upstream(args.dir, env)
        py = make_venv(root, args) if root else None
        if py:
            REPORT["server_root"] = root
            step("把桥接脚本装进达芬奇", None, "按参数跳过") if (args.skip_bridge or args.edition == "studio") else install_bridge(root, py)
            step("在 Claude 里注册", None, "按参数跳过") if args.skip_register else register(root, env, args)
            step("安装技能", None, "按参数跳过") if args.skip_skill else install_skill(args)
            REPORT["selftest_command"] = '"%s" "%s"' % (py, os.path.join(KIT, "scripts", "selftest.py"))
            REPORT["human_todo"].append("在达芬奇里新建并打开一个名字带「测试」的工程，再跑自检。")
    REPORT["ok"] = (not REPORT["blockers"]) and all(s["ok"] is not False for s in REPORT["steps"])
    for p in ([os.path.join(args.dir, "install_report.json")] if os.path.isdir(args.dir) else []) + [os.path.join(KIT, "install_report.json")]:
        try:
            json.dump(REPORT, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    print("\n===== 结果：%s =====" % ("可以继续" if REPORT["ok"] else "有问题，见下"))
    for k, title in (("blockers", "不能继续的原因"), ("warnings", "提醒"), ("human_todo", "需要人来做的")):
        if REPORT[k]:
            print(title + "：")
            for i, x in enumerate(dict.fromkeys(REPORT[k]), 1):
                print("  %d. %s" % (i, x))
    if REPORT.get("selftest_command"):
        print("自检命令：" + REPORT["selftest_command"])
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
