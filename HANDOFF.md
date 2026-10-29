# Parallel Reader：新电脑与 Agent 交接

更新日期：2026-10-29。适用于在另一台电脑 clone 后接手的 Codex 或其他 coding agent。先按本文启动和验证，再根据用户当前要求开展工作。

## 1. 当前目标和已有状态

这是本地 EPUB 阅读器，支持翻译、译图、书签及加密分卷书架迁移。当前异地访问目标是：**用户自己的 iPhone，在手机流量或其他 Wi-Fi 下访问服务器电脑上的同一个书架**。采用 Tailscale 私网，个人使用，无需建设多用户平台或另开公网隧道。

Tailscale 功能代码基线为 `5b8c308`，本文在其后新增；以当前 `git log` 为准，不要为运行项目回退到这个旧提交。

- `start-tailscale.bat`、客户端状态检测和访问限制已经实现，并包含在便携构建中。
- 上一次完整回归通过 **310 项测试**，便携版构建及未安装客户端时安全退出均已验证。这是历史记录，新电脑需要自行运行适用检查。
- 原开发电脑按用户要求没有安装 Tailscale，也没有验证真实 iPhone 外网链路。不要把模拟测试通过报告为手机实机联通。
- 当前任务若仅是 clone、写代码或本地阅读，无需安装 Tailscale。用户在目标电脑明确要求配置远程访问时，可在其授权范围内执行安装流程；登录、UAC 和 iPhone 的系统确认由用户操作。

## 2. Clone 与运行环境

主要交付环境是 Windows 10/11 x64。仓库已经包含便携 Python、Pillow、ZIP 加密依赖和中文字体；正常使用不需要再安装 Python、Node.js 或 pip。

在用户选择的工作目录运行：

```powershell
git clone https://github.com/SYCAMORE-1/ParallelReader.git
Set-Location -LiteralPath .\ParallelReader
git status --short --branch
.\runtime\python.exe -X utf8 tools\verify_release.py
```

所有后续相对路径均以实际 clone 根目录为准，路径可以包含空格。不要复制另一台电脑的绝对目录、进程编号、模型地址或 Tailscale 地址。

`verify_release.py` 检查依赖、字体和已有书库；普通源码 clone 没有 release manifest 是正常情况。已有书库若报告资源缺失，先保留文件并诊断，不要删除数据库或自动清理。首次 clone 没有 `.data/reader.sqlite3` 也是正常的。

如果是 macOS、Linux 或其他架构，不要运行 Windows 的 `runtime/python.exe`。请按 README 使用该平台的 Python 3.10+ 独立环境和 `requirements.txt`，并另外核对该平台的 Tailscale CLI；本文的 BAT/PowerShell 主流程针对 Windows。

## 3. 先验证本机阅读

| 场景 | 启动方式 |
| --- | --- |
| 电脑本机 | `.\start.bat` |
| 手机同 Wi-Fi | `.\start-mobile.bat` |
| 自己的 iPhone 异地使用 | `.\start-tailscale.bat`，先完成第 5 节 |

本机的默认网址是 `http://127.0.0.1:8765/`。启动窗口需要保持打开。可先使用不会自动打开浏览器的命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -NoBrowser
```

在另一个终端验证：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/health
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/books
```

健康接口应返回 `ok: true`。首次 clone 的书架为空是预期结果。然后在浏览器确认书架及导入入口正常显示。

启动前先检查是否已经有阅读器运行；端口被占用时，应核对所属进程和工作目录，复用正确实例，或正常关闭旧实例后再启动。不要终止无关进程。自定义端口示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Port 8766 -NoBrowser
```

此时健康检查和浏览器地址也要改为窗口显示的实际端口。

## 4. Clone 不会带来原电脑的书库

Git 只同步程序与文档；`.data/`、私人图书、译文、模型配置和迁移压缩包均不在仓库中。不要把空书架判断为 clone 失败，也不要去公开 Release 猜测私人书库下载地址。

恢复已有书库，按用户提供的材料选择：

1. **网页迁移**：启动程序，点击“导入图书 / 书架”，选择原阅读器导出的单个加密 ZIP，或同一次归档的全部 `.zip.0001`、`.zip.0002` 等分卷。密码由用户自行输入。验证后与现有书架合并，只有保存内容相同的书才跳过，其他版本保留。
2. **整体复制**：两端阅读器正常关闭后，将用户提供的 `.data/` 放到新程序根目录。目标已有 `.data/` 时先确认迁移方式，不能直接覆盖；合并优先用网页导入。
3. **按书复制**：正常关闭阅读器，只复制选定的 `.data/books/书名 -- 短编号/` 完整文件夹；保留 `book.json`、`original.epub`、`book.sqlite3` 以及图片和导出目录。已有同名目录不要覆盖。

每书文件夹是迁移单元，共享的 `.data/reader.sqlite3` 是运行数据库。运行中存在的 SQLite WAL/SHM 文件不能直接丢弃。不要一边翻译一边手工移动书籍文件夹。

模型地址、模型名称和可选密钥在新电脑的设置中检查。原电脑可达的内网模型不一定能被新电脑访问。已有译文可离线读，新增翻译由服务器电脑访问模型；手机不必直接访问模型端口。不要为冒烟验证擅自重译整本私人图书。

## 5. Tailscale 从哪里下载、怎样设置

### 5.1 Windows 服务器电脑

使用官方来源，不把安装包提交到仓库，也不固定一个很快过期的版本号：

- [Windows 官方下载页](https://tailscale.com/download/windows)
- [Windows 官方安装和登录步骤](https://tailscale.com/docs/install/windows)
- [官方稳定包列表](https://pkgs.tailscale.com/stable/)：定位 Windows 区域，下载当前 `.exe` 安装器；MSI 应匹配目标电脑架构。

在目标电脑的安装已获用户授权时，下载当前官方安装器并运行；安装完成后在系统托盘打开 Tailscale，选择 Log in，由用户通过浏览器登录自己的账号。安装阅读器或执行 `--tailscale` 本身不会安装 Tailscale。只下载准备时，不要顺便执行安装器。

agent 下载前应从以上官方页面解析当时的实际下载链接，避免凭记忆构造 EXE 版本地址。可以在下载后使用 `Get-AuthenticodeSignature` 核对安装器签名；不要自动接受无法验证来源的文件。

### 5.2 iPhone

通过 [官方 iOS 下载入口](https://tailscale.com/download/ios) 跳转到 App Store，或在 App Store 搜索官方 Tailscale。按 [iOS 安装说明](https://tailscale.com/docs/install/ios)，由用户登录与电脑相同的个人账号，允许 iPhone 提示的 VPN 配置并打开连接。agent 不需要获取用户的账号密码或验证码。

### 5.3 确认电脑已连接

已安装客户端可能还未加入 PATH，读取标准安装位置的方法如下。这些命令只读状态，不发起登录或修改网络：

```powershell
$readerTsCommand = Get-Command tailscale.exe -ErrorAction SilentlyContinue
$readerTsExe = if ($readerTsCommand) { $readerTsCommand.Source } else { Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe' }
if (-not (Test-Path -LiteralPath $readerTsExe)) { throw 'Tailscale client not installed on this PC.' }
$readerTsStatus = & $readerTsExe status --json | ConvertFrom-Json
$readerTsStatus.BackendState
$readerTsStatus.Self.TailscaleIPs
```

预期状态为 `Running`，并且本机信息中有 Tailscale IPv4 地址。状态输出可能包含个人网络信息，不要保存到公开 Git。

程序从 `Self.TailscaleIPs` 自动识别本机地址，只采用唯一的有效 Tailscale IPv4。没有客户端、需要登录、离线、没有 IPv4 或端口占用时，会给出提示并退出；不会自动执行安装、`tailscale up` 或修改防火墙。

## 6. 启动异地访问与手机验收

先正常关闭之前运行的阅读器，等待后台在途请求完成保存。然后双击：

```powershell
.\start-tailscale.bat
```

或使用可指定端口的方式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Tailscale -Port 8765 -NoBrowser
```

启动窗口显示两类地址：本机网址以及 `iPhone / Tailscale` 私网网址。iPhone 的 Safari 打开后者，不能用手机自身的 `127.0.0.1`。网址里的主机地址必须是电脑的 Tailscale IPv4，端口必须与启动窗口一致。

验收时让 iPhone 关闭 Wi-Fi、使用蜂窝数据，保持 Tailscale 连接，依次确认：

1. `/api/health` 可访问、书架可打开。
2. 能打开一本用户允许用于验证的书并阅读已有内容。
3. 手机保存的阅读位置、书签能在电脑的同一书架中看到。
4. 只有在用户要求测试翻译时，才验证缺失段落的翻译和模型连通；不要把手机访问成功等同于模型已经可用。

这一路径使用私网 IP，不要求域名、Serve、Funnel 或公网端口映射。不要为了接通而关闭 Host/Origin 检查或改为公开入口。它是个人可信设备访问：能被 Tailscale 规则允许访问该端口的设备，可以使用完整书架和设置。

电脑需要保持开机、联网和阅读器运行。若手机连不上，先分清本机服务、Tailscale 连接、防火墙和模型的不同问题；只对实际需要的服务和网络检查放行规则，不关闭整个防火墙。详见 [Tailscale 排查说明](docs/tailscale.md)。

## 7. 接手代码时看哪些文件

| 文件 | 职责 |
| --- | --- |
| `server.py` | HTTP/API、Host/Origin 限制、`--tailscale` 入口 |
| `start.ps1` 与三个 `start*.bat` | Windows 启动和参数转发 |
| `reader/tailscale_access.py` | 只读识别客户端状态、地址和请求来源，启动前检查端口 |
| `reader/storage.py`、`reader/book_folders.py` | SQLite、按书组织与迁移快照 |
| `reader/translation.py`、`reader/image_translation.py` | 正文翻译、校对和译图任务 |
| `reader/reading_policy.py` | 中文／已译 EPUB 直接阅读策略 |
| `reader/shelf_archive.py` | 用户自设密码的加密书架分卷与合并 |
| `public/` | 阅读界面、导入、归档及浏览器阅读偏好 |
| `tools/build_release.py` | 不含私人书库的 Windows 便携构建 |
| `tests/test_tailscale_access.py` | 客户端模拟、启动器和网络限制回归 |

## 8. 验证命令与产物

只改文档时，检查链接、命令对应的文件和公开数据审计即可。修改启动器或网络行为时运行相关测试；更广泛的业务修改再运行完整回归：

```powershell
.\runtime\python.exe -X utf8 -m unittest discover -s tests -p test_tailscale_access.py -v
.\runtime\python.exe -X utf8 -m unittest discover -s tests -v
.\runtime\python.exe -X utf8 tools\audit_public.py
```

需要重建便携程序时，输出到新的目录，不覆盖旧发布包：

```powershell
.\runtime\python.exe -X utf8 tools\build_release.py --zip
```

`dist/`、`test-results/`、私人迁移文件夹和 `.data/` 都属于忽略项；不要为了让另一台电脑拿到它们而强制加入 Git。构建不携带书籍或模型配置。不要向公开仓库写入私人模型 URL、IP、账号、密钥、真实书籍原文或书库标识。

## 9. 可直接发给新电脑 Agent 的任务

> 请先阅读仓库根目录的 AGENTS.md 和 HANDOFF.md。使用实际 clone 路径及自带 Python，先验证环境和本地阅读器；保留已有书库与任务。按我当前要求准备 Tailscale 个人 iPhone 访问，从官方来源下载客户端，登录和手机系统确认交给我。不要改成公网或多用户方案。最后报告实际启动命令、电脑与手机访问网址、已验证结果和仍需我操作的步骤；不要声称未执行的手机实机测试已经通过。

当前用户如果只要求本地运行，应只执行相关部分；若已明确要求在目标机安装并配置 Tailscale，按该授权继续，不需要因这份文档重复询问同一项许可。

Codex 的仓库入口约定参考 [OpenAI 官方 AGENTS.md 说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)；不支持该约定的 agent 可从 README 的交接链接直接阅读本文。
