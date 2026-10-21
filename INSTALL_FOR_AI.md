# 给 AI 的安装指令（ja-davinci）

你（Claude）正在帮一位同事把「用 Claude 操作达芬奇」装到他的电脑上。请严格按下面的顺序做。易错的环节都写成了脚本，你负责运行脚本、读结果、用大白话和使用者沟通。

## 边界（先读）

- 只让使用者做「点击」类的事（装软件、点菜单、重开程序），**不要让他敲命令**。命令都由你来跑。
- **命令一律用 PowerShell 执行**，照抄下面的写法：路径用 `$env:LOCALAPPDATA\…` 写成绝对路径，不要用 cmd 的 `%LOCALAPPDATA%`，也不要先进入某个目录再跑相对路径（Claude Code 每条命令跑完会回到原来的目录）。
- 不要运行上游仓库自己的 `install.py`，不要改控制层的版本，不要动 Claude 配置里别的注册项。
- 自检只能在名字带「测试」的工程里跑，不要碰使用者的正式工程。
- 任何一步失败，先把脚本输出的原因用一两句话告诉使用者，再决定下一步；不要反复重试同一条命令。
- 只在 Windows 上验证过。使用者是 Mac 时，先说明「没有验证过」，由他决定要不要继续。

## 第 1 步：确认前提

向使用者确认，或自己检查：

1. **达芬奇的版本**。免费版必须是 21.0.x 或更早；21.1 及以后的免费版不支持脚本，装了也接不上（Studio 版不受限制）。版本可以等第 3 步脚本自动读，但如果使用者已经知道是 21.1 以上的免费版，现在就停下来说明原因。
2. **Python**。运行 `python --version`（不行再试 `py -3 --version`）。需要 3.10 或更新，而且必须是 python.org 的安装包装的——达芬奇的脚本菜单只认这种。没有的话，请使用者去 python.org 下载 3.12 的 Windows 安装包，安装时勾上「Add python.exe to PATH」，装完告诉你。只有 `py -3` 能用时，下面命令里的 `python` 都换成 `py -3`。
3. 达芬奇已经装好。最好现在就开着，脚本能直接读到它的版本和安装位置。
4. **C 盘剩余空间**：控制层和运行环境装在 C 盘，本机实测约占 0.15 GB。用下面这条看还剩多少 GB；剩余不到 1 GB 时，先告诉使用者，请他清理出空间再继续。

   ```powershell
   [math]::Round((Get-PSDrive C).Free / 1GB, 1)
   ```

## 第 2 步：下载本仓库

目标位置：`$env:LOCALAPPDATA\ja-davinci\kit`。

有 git 时（`git --version` 能显示版本号）：

```powershell
$d = "$env:LOCALAPPDATA\ja-davinci\kit"
if (Test-Path "$d\.git") { git -C $d pull --ff-only } else { git clone https://github.com/jefferyadventure519-byte/ja-davinci-mcp.git $d }
```

没有 git 时，下载 zip：

```powershell
$d = "$env:LOCALAPPDATA\ja-davinci\kit"; $z = "$env:TEMP\ja-davinci-mcp.zip"; $x = "$env:TEMP\ja-davinci-mcp-x"
Invoke-WebRequest "https://github.com/jefferyadventure519-byte/ja-davinci-mcp/archive/refs/heads/main.zip" -OutFile $z -UseBasicParsing
if (Test-Path $x) { Remove-Item $x -Recurse -Force }
Expand-Archive $z $x -Force
if (Test-Path $d) { Remove-Item $d -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $d) | Out-Null
Move-Item "$x\ja-davinci-mcp-main" $d
```

下载失败多半是访问不了 github.com，请如实告诉使用者，由他解决网络后再继续。

## 第 3 步：只检查，不改动

```powershell
python "$env:LOCALAPPDATA\ja-davinci\kit\scripts\install.py" --check-only
```

读输出最后的「结果」一段，里面最多有三类：

- 「不能继续的原因」：停下，把原因讲给使用者听。最常见的是达芬奇版本太新。
- 「提醒」：把提醒讲清楚，问使用者要不要继续。没有 ffmpeg 不影响安装，但自检里的两项画面核对会记为「记录」。如果提醒的是「没找到达芬奇」，先请使用者装好并打开达芬奇，再重跑本步。
- 「需要人来做的」：不是问题，转告使用者就行，不用停下来问。这一步最常见的是「不要把达芬奇升级到 21.1 或更新的免费版」。

## 第 4 步：安装

```powershell
python "$env:LOCALAPPDATA\ja-davinci\kit\scripts\install.py"
```

**这条命令可能要跑几分钟**（要从 GitHub 下载控制层、装依赖）。Claude Code 前台命令默认 2 分钟就会被中止，所以请放到后台运行、等它结束再读输出；不放后台的话，把这条命令的超时设到最大（10 分钟）。

它会依次：下载固定版本的控制层（上游开源项目 davinci-resolve-mcp v4.10.4）、建运行环境、把桥接脚本装进达芬奇、在 Claude 里注册、装上踩坑规则。逐条读「完成 / 失败 / 跳过」：

- 「在 Claude 里注册」失败并提示已有同名注册：说明这台电脑以前装过。把旧注册指向的位置告诉使用者，问他是否替换；同意后加 `--force` 重跑。
- 提示桌面版配置里也有同名注册：告诉使用者两处同时存在会重复加载，建议只留一处，由他决定，不要自己去删。
- 使用者有 ffmpeg 但脚本没找到：加 `--ffmpeg-dir <ffmpeg.exe 所在目录>` 重跑。
- 使用者用的是 Studio 版：加 `--edition studio`（不装桥接，直接走外部脚本；这条路没有实测过，要如实说明）。
- 结果里「需要人来做的」（重开达芬奇点桥接、新建测试工程、重开 Claude）不用现在停下来，第 5、7 步再请使用者做。

## 第 5 步：请使用者做三次点击

1. 关掉达芬奇再打开（让它认到新装的脚本）。
2. 新建并打开一个工程，名字里带「测试」，比如「Claude测试」。
3. 点菜单「工作区 → 脚本 → resolve_bridge」（有两个同名的点任意一个）。

如果菜单里没有 resolve_bridge，只有 resolve_bridge_canary：免费版 21.1 以上就是这个现象，接不上；21.0.x 及更早版本出现这个现象，说明达芬奇没找到 Python，回到第 1 步检查 Python 是不是 python.org 装的。

## 第 6 步：自检

安装输出的最后一行有「自检命令」，原样运行。在 PowerShell 里要在最前面加 `&`，写成 `& "…\python.exe" "…\selftest.py"`，不加会报「表达式或语句中存在意外的标记」，命令不会运行。它用仓库自带的彩条和测试音，在测试工程里把导入、校色、放大、字幕落轨、渲染等 23 项跑一遍，约一两分钟。

把结果讲给使用者：通过几项、不通过几项、记录几项（自检只有这三种结果）。「记录」不是失败，只是把这台机器上的实际行为记下来；没有 ffmpeg 时，两项像素核对也记为「记录」。有「不通过」时，把那一项的细节念出来，对照 `skill/ja-davinci/rules.md` 判断原因；判断不了就如实说判断不了，并把报告文件的位置给他。

达芬奇版本低于 21.0 时，字幕压制的两项会显示为「记录」，这是预期的。

## 第 7 步：收尾

告诉使用者：

1. **完全退出并重开 Claude**，新会话里才有达芬奇的工具。
2. 以后**每次打开达芬奇都要点一次**「工作区 → 脚本 → resolve_bridge」。
3. **不要把达芬奇升级到 21.1 或更新的免费版**。
4. 在新会话里说要用达芬奇做什么就行，规则技能 `ja-davinci` 会自动带上。

最后用下面的格式汇报，数字照实填：

```
安装结果：成功 / 没装完（原因：…）
达芬奇版本：…        控制层：davinci-resolve-mcp 4.10.4
自检：通过 … 项，不通过 … 项，记录 … 项
需要你做的：…
没有验证的：…
```
