# ja-davinci · 让 Claude 直接操作达芬奇

装好以后，你可以在 Claude 里用大白话提需求，Claude 会直接在你**已经打开的达芬奇（DaVinci Resolve）**里干活：导入素材、搭时间线、写校色、落字幕轨、渲染，并且自己核对结果。

这个仓库本身不是控制程序。它做三件事：

1. **装**：把上游开源项目 [davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)（MIT 许可）的一个**固定版本 v4.10.4** 装到你的电脑上并接进 Claude。
2. **测**：用自带的彩条和测试音跑一遍 23 项自检，确认你这台电脑上真的能用。
3. **带规则**：把我们实际剪片子踩过的坑整理成一份规则，Claude 操作达芬奇时会自动带上，不用你重新踩一遍。

它**不带剪辑模板**，也不保证「一句话剪出片子」。片子怎么剪，还是你和 Claude 一起定。

## 先看这一条：达芬奇版本

| 你的达芬奇 | 能不能用 |
|---|---|
| 免费版 21.0.x 及更早 | 能。实测过的是 21.0.4.5 |
| 免费版 21.1 及以后 | **不能**。官方从 21.1 起把 Python 脚本功能改成了 Studio 专有 |
| Studio 版 | 理论上能（安装时加 `--edition studio`），没有实测过 |

用免费版的话，装好后**不要升级达芬奇**。

## 一句话安装

在 Claude Code 里粘贴这句话：

```
请按 https://github.com/jefferyadventure519-byte/ja-davinci-mcp 里 INSTALL_FOR_AI.md 的步骤，帮我安装并测试 ja-davinci
```

Claude 会依次完成：检查环境、下载、安装、注册、自检，最后把结果告诉你。过程中需要你动手的只有几次点击。

开始前请准备好：

| 项目 | 说明 |
|---|---|
| Windows 电脑 | 只在 Windows 上验证过 |
| 达芬奇 | 版本要求见上表；最好先打开 |
| Python 3.10 或更新 | 必须是 [python.org](https://www.python.org/downloads/) 的安装包装的，安装时勾选「Add python.exe to PATH」 |
| 能访问 github.com | 安装要从 GitHub 下载 |
| ffmpeg（可选） | 没有也能装；出预览、抽帧核对时需要 |

## 装好以后怎么用

1. 打开达芬奇，打开一个工程。
2. 点一次「工作区 → 脚本 → resolve_bridge」。**每次打开达芬奇都要点。**
3. 在 Claude 的新会话里直接说你要做什么。

## 仓库里有什么

| 位置 | 内容 |
|---|---|
| `INSTALL_FOR_AI.md` | 给 Claude 看的安装步骤 |
| `scripts/install.py` | 安装脚本。先备份再改配置；发现已有注册不会擅自覆盖；支持 `--check-only` 只检查、`--uninstall` 卸载 |
| `scripts/selftest.py` | 自检脚本。只在名字带「测试」的工程里运行，只用自带的合成素材 |
| `skill/ja-davinci/` | 给 Claude 的操作规则和踩坑清单（25 条） |
| `tools/fcpx.py` | 把时间线写成 FCPXML 一次导入达芬奇 |
| `tools/sub_style.py` | 把字体、大小、位置写进字幕轨样式（脚本接口本身做不到） |
| `selftest_media/` | 自检用的合成素材（彩条、测试图、正弦音、4 条测试字幕），共 2.4 MB |

## 已知的限制

- 免费版每次打开达芬奇都要手动点一次桥接；达芬奇闪退后也要重开再点。
- 字幕轨样式里的描边、投影、颜色脚本写不了，要在检查器里手动设一次。
- 达芬奇 21.0 之前的版本、Mac、Studio 版都没有实测。
- 控制层是第三方开源项目，更新很快。这里固定在测过的 v4.10.4，不自动跟最新。

## 卸载

在仓库目录里让 Claude 运行 `python scripts/install.py --uninstall`：去掉 Claude 里的注册和规则技能。下载的文件在 `%LOCALAPPDATA%\ja-davinci`，直接删目录即可。达芬奇脚本目录里的 `resolve_bridge*` 几个文件可以留着，不点就不会运行。

## 致谢

控制层来自 [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)（MIT 许可）。本仓库不包含它的代码，安装时从它的官方仓库下载。
