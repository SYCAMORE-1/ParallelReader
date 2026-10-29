# Parallel Reader®

**Read across languages. Stay in the story.**  
**跨越语言，专注阅读。**

A portable bilingual EPUB reader with contextual translation, proofreading, and in-image translation.  
一款支持语境翻译、译文校对和图片内翻译的便携式双语 EPUB 阅读器。

![Parallel Reader — Bilingual Reading / 双语对照阅读](docs/screenshots/reader.png)

**New computer / agent handoff:** start with [HANDOFF.md](HANDOFF.md). It covers a fresh clone, startup and checks, private-library restoration, and official Tailscale downloads/setup for personal iPhone access. [AGENTS.md](AGENTS.md) points coding agents to this guide.

**新电脑 / Agent 接手：**先阅读 [HANDOFF.md](HANDOFF.md)，其中包含 clone 后运行和验证、私人书库恢复，以及 Tailscale 官方下载和 iPhone 配置流程。[AGENTS.md](AGENTS.md) 提供 agent 入口。

## English

### Features

- Four reading layouts: original, Chinese translation, side-by-side, and alternating paragraphs.
- Paginated, continuous scrolling, and immersive reading with chapter navigation.
- Separate typography settings for each language and multiple reading themes.
- Context-aware translation using book metadata, surrounding passages, and available glossaries.
- Separate translation, proofreading, and optional word-lookup models, configured by the user.
- Choose LLM or Azure Translate for book translation; choose the word/context translation service independently.
- Whole-book processing with progress, pause/resume, retries, and repeated proofreading passes.
- Up to four text chapters and two images concurrently per book.
- In-image translation with original/translated image switching and QR-code preservation.
- Sentence correspondence highlighting and contextual word lookup without reading ahead.
- Bookshelf cover views, reading/translation progress, one bookmark per book, and return-to-bookmark.
- Personal iPhone access over Tailscale, using the same local bookshelf and reading position from another network.
- Local translation caching and Chinese-only or bilingual EPUB export, including translated illustrations.
- Chinese originals and detected translated editions open directly in scrolling mode, without translation requests.
- The bookshelf archive button creates an AES-256 encrypted ZIP with volumes capped at 500 MiB. Set your own password, download all volumes, then select them together with Import to restore and merge. Identical saved content is skipped; different originals or translations remain separate editions.

### Encrypted bookshelf archives

Click the archive download icon beside the bookshelf view buttons. Enter and confirm your own password to package the entire current shelf as an **AES-256 encrypted ZIP**, automatically split into volumes of at most **500 MiB**. Download and keep every volume. The password is used only for that operation and is not saved in settings, archive manifests or logs.

On the destination, choose **Import books / bookshelf**, select the single `.zip` or every `.zip.0001`, `.zip.0002`, etc. volume from one archive, then enter its password. All files are decrypted and verified before the shelf is merged. Matching original files, saved translations, bookmarks and reading state are skipped; editions with different saved content remain separate, even when titles match. The combined shelf can be archived again. Restored background jobs stay paused and the destination's model settings are retained.

Archives omit model settings and keys, logs, old backups and regenerated EPUB exports. Generated encrypted volumes are kept in `private-transfer/shelf-archives/`; old archives can be removed after downloading. The web importer accepts these encrypted bookshelf archives. The older per-book ZIP tools below use a separate format and restoration workflow.

Chinese originals, EPUBs containing Chinese text and detected translated editions open in scrolling mode with only the original-content layout available. The other three content modes are disabled. Original English editions retain their translation and layout controls.

### Program and private library are separate

The public repository and program release contain **no books, saved translations, private model endpoints, or API keys**. Model endpoint and model-name defaults are empty.

All private library resources live in `.data/`: original EPUB files, the library database, translations, translated images, exported EPUBs, bookmarks, reading positions, and local model configuration. This directory is ignored by Git and excluded from public builds.

To use your library on another computer:

1. Close the reader before copying, or create a consistent snapshot with the export tool below.
2. Copy `.data/` into the new program's root folder.
3. Start the program. The bookshelf, saved translations, reading positions, and bookmarks will be available.

For a compact private transfer folder that excludes old backups and logs, removes API keys, and pauses unfinished tasks:

```powershell
python tools/export_library.py --output private-transfer/.data
```

The transfer folder is private data. Keep it separate from public program releases. Local model configuration remains in the library; check it before sharing.

### Small book packages for private transfers

Run `pack-books.bat` to create independent book ZIPs in `private-transfer/book-packages-<date>/`. Each includes the original EPUB, translated images, saved translation versions, drafts, progress and bookmarks. Exported EPUBs, backups, logs, the duplicate central database and model settings are excluded. Fresh snapshots include committed SQLite WAL data, so packaging does not require stopping the source reader. Work saved after the snapshot will belong to a later package.

Books larger than the default 500 MiB volume limit become `.zip.0001`, `.zip.0002`, etc. Every volume has a SHA-256 checksum in `library-manifest.json`. Unchanged books produce the same archive bytes, making it easy to upload only changed or new books. For a private GitHub Release, attach the book packages and manifest separately; keep them out of the public program Release.

On the destination computer, close the reader and put the downloaded packages and manifest in one folder. Drag that folder onto `restore-books.bat`. It verifies all selected volumes and restores the downloaded books; it refuses incomplete volumes, corrupt data, unsafe paths and existing copies of the same book. Normal single-file ZIPs can also be extracted directly into `.data/`. Start the reader to import the restored book folders. Configure models on the destination only when new translation is needed.

```powershell
runtime/python.exe tools/package_library.py --part-mib 500
runtime/python.exe tools/package_library.py --book "Book title" --output private-transfer/selected-books
runtime/python.exe tools/restore_library_packages.py --packages private-transfer/selected-books --target .data
```

`--book` accepts an exact title or book ID and may be repeated. Packaging selects books currently on the shelf; `--include-removed` also includes removed books whose folders remain. Restoring without `--book` skips books for which no volumes were downloaded, but rejects a partially downloaded book. Use a new library or move out the previous copy before restoring an updated copy of an existing book; the tool never overwrites reading progress.

### Getting started

On Windows 10/11 x64, download this repository using **Code → Download ZIP**, then extract the entire folder and run `start.bat`. The `main` branch includes the portable Python runtime, Pillow and Chinese fonts, so no Python or pip installation is required. Keep the startup window open while reading. Do not run the launcher from inside the ZIP.

The Windows portable release works the same way. Use `start-mobile.bat` for access from a phone on the same local network. If startup fails, run `check-portable.bat` to check the runtime, imports, fonts and any existing library. Release packages also verify their file checksums.

| Where you read | Launcher | What to open |
| --- | --- | --- |
| This computer | `start.bat` | The local browser window |
| Phone on the same Wi-Fi | `start-mobile.bat` | The LAN URL shown in the startup window |
| Your iPhone on mobile data or another Wi-Fi | `start-tailscale.bat` | The Tailscale URL shown in the startup window; connect Tailscale on both devices first |

For development with your own Python 3.10+ installation instead of the bundled runtime:

```powershell
python -m pip install -r requirements.txt
python server.py --open
```

Import an unencrypted EPUB and select LLM or Azure Translate in Settings. LLM book translation uses the book model configuration. LLM word/context explanations require their own endpoint and model; changing the book service does not change the lookup service.

Choose **Import books / bookshelf** to select EPUB files or the encrypted archive volumes described above. The former bookshelf **＋** button is now the archive download button. Each book receives a folder in `.data/books/`, named after its title with a short edition identifier. It contains `original.epub`, translated images, exported EPUBs, and a credential-free `book.sqlite3` transfer snapshot with saved translations, drafts, reading position and bookmarks. The shared `.data/reader.sqlite3` remains the live library database.

To move selected books, close the reader first, copy only their folders into the other installation's `.data/books/`, and start that reader. Copied books are discovered automatically, even without the original shared database. Moving a folder out hides that book from its original bookshelf while retaining its shared database records. Folder labels can be renamed; keep the contents and `book.json` together. Stop the reader before copying either installation. Snapshots refresh periodically while running and are finalised on normal shutdown; for a manual flush, with the reader closed, run `runtime/python.exe tools/organize_library.py --apply`.

Existing flat libraries are organised automatically on startup. For an explicit migration with a database backup, close the reader and run `runtime/python.exe tools/organize_library.py --apply`; omit `--apply` to preview folder names. Existing source files, translations, versions, reading positions and bookmarks are preserved. Previously missing resources are reported and are not silently discarded.

Saved content can be read offline. New translations require the selected service; proofreading requires an LLM. Word lookup defaults to Azure Translate and can independently use an LLM for fuller explanations. Model weights are not included. Content needed for a request is sent to the selected service.

### Personal remote access from an iPhone

Install Tailscale yourself on the server PC and iPhone, sign in to the same personal account, and connect both devices. Close the previously running reader, then run **`start-tailscale.bat`**. The startup window shows the computer's private IPv4 URL to open in Safari over mobile data or another Wi-Fi network. The reader checks the existing connection and accepts only local access and traffic through the detected Tailscale address; the normal LAN interface is not enabled.

The same bookshelf, translations, reading position and model configuration remain in use. Keep the computer online and the reader running. This launcher does not install, sign in to or configure Tailscale. Missing, disconnected and occupied-port cases stop with instructions. See [the setup guide](docs/tailscale.md), including firewall troubleshooting and custom ports.

For a different port, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Tailscale -Port 8766`. The iPhone must use the port printed in that startup window. Support has been checked with simulated client states, HTTP access tests and a portable build; a real off-network iPhone connection still requires a connected Tailscale installation.

### Saved translations and service switching

Translations remain stored separately by service/configuration. Changing the translation service does not delete existing versions. If the new configuration has no saved text for the current book but another local version exists, the reader asks whether to keep reading that saved version or translate with the new service. If no local version exists, translation starts normally. The **Local translation** button shows the saved version's source and paragraph count and lets you change it later; choices are remembered per book and configuration.

When reading a saved version, cached paragraphs and their sentence correspondence are reused without translation requests. Missing paragraphs use the current service and stay in that service's own cache. EPUB export uses the selected reading version, including those filled gaps. Whole-book background processing and proofreading retain their own configuration-specific progress.

A single word/context service setting controls both basic meanings and contextual explanations, independently of the book service. Azure Translate provides dictionary meanings, English parts of speech and a contextual translation using the current sentence and up to ten preceding sentences. Oversized context is shortened to the service's request limit. Dictionary parts of speech describe common uses; the contextual result is a machine translation, with the whole translated sentence shown if word markup is unavailable. Choose **LLM** in the word/context service setting for richer explanations, using a separately configured lookup model. The popup identifies the provider in one small line.

### Translation services

Select **LLM** or **Azure Translate** under **Book translation**. LLM supports contextual translation, proofreading and image translation. Azure Translate handles text; images remain original and are excluded from its translation progress. Both support saved paragraph translations, sentence correspondence, whole-book processing, pause/resume and EPUB export.

Select **LLM** or **Azure Translate** separately under **Word / context translation**. This choice controls both fields in the lookup popup and does not follow the book service. LLM lookup uses its own endpoint, model and optional key.

### Keeping the private library compact

The library directory groups resources under `books/<title -- edition>/`. Its shared `reader.sqlite3` stores live settings and cached state; each book's `book.sqlite3` contains a separate portable snapshot without model credentials. Snapshots duplicate the selected book's database records so a book folder can be moved independently. `library-catalog.json` retains older import information. SQLite may also create temporary `-wal` and `-shm` files while running.

Builds and test output belong in `dist/` and `test-results/`, outside `.data/`. To inspect generated files that can be removed, then clean them with the reader closed:

```powershell
python tools/clean_library.py
python tools/clean_library.py --apply --prune-history
```

Cleanup keeps originals, current saved translation versions, referenced illustrations and reading/job state. It removes unreferenced translated images, older export files and, with `--prune-history`, previous proofreading snapshots. Current LLM/Azure translation versions remain available. It verifies the database contents before and after compaction; it does not change future history retention.

### Public builds and checks

```powershell
python tools/audit_public.py
python tools/build_release.py --zip
python -m unittest discover -s tests
```

Public builds always use an empty library. They include application sources, permitted fonts, runtime dependencies, documentation, and synthetic tests. Private book resources and model configuration are not bundled.

This beta supports unencrypted EPUBs. Complex layouts, small or rotated image text, and model-generated translations may require comparison with the original.

## 中文

### 功能特性

- 原文、中文、双列对照和段落跟随四种阅读布局。
- 翻页、连续滚动和沉浸阅读，支持章节导航。
- 中英文字体、字号和粗细独立调整，多种阅读主题。
- 结合书籍信息、上下文和可用术语表进行翻译。
- 初译、校对和可选的独立查词模型由使用者自行配置。
- 图书翻译提供 LLM 和 Azure Translate；单词／语境翻译独立选择服务。
- 全书后台处理，支持进度显示、暂停、继续、失败重试和多轮校对。
- 每本书最多同时处理四个文本章节和两张图片。
- 译文直接排入图片，可切换原图／译图，并保留二维码。
- 中英文句子对应高亮，双击单词查看不读取后文的语境解释。
- 封面书架、阅读／翻译进度、每书一个书签，从书架打开时返回书签。
- 支持自己的 iPhone 通过 Tailscale 异地访问，沿用电脑上的书架和阅读进度。
- 译文缓存保存在本机，可导出包含译图的中文或双语 EPUB。
- 中文原著和已识别译本无需再次翻译，直接滚动阅读，其余三种内容显示模式置灰。
- 书架支持自行设置密码的 AES-256 加密分卷 ZIP，每卷最多 500 MiB；可导入并合并书架，不同版本分别保留。

### 网页一键迁移书架

点击书架视图按钮左侧的归档下载图标，自行输入并确认密码，打包整个当前书架。自动生成 **AES-256 加密 ZIP**，每卷最多 **500 MiB**，可逐卷下载，也可点“下载全部分卷”。保存所有分卷及密码；密码只用于当次操作，不保存在配置、归档清单或日志中。

目标电脑点击“导入图书 / 书架”，选择单个 `.zip` 或同一次打包的全部 `.zip.0001`、`.zip.0002` 等分卷，输入密码后恢复。无需停止阅读器；完整解密和校验后合并书架。原书、各版本译文、书签、阅读位置等保存内容相同才跳过；书名相同但原书或保存内容不同的版本都会保留，合并后可再次完整打包。恢复的后台任务暂停，模型设置沿用目标电脑。

归档不包含模型密钥、模型接口配置、日志、旧备份或可重新生成的导出缓存。加密卷保存在工程 `private-transfer/shelf-archives/`，下载后可自行清理旧归档。网页导入支持本阅读器新导出的加密书架归档；下方旧版每书 ZIP 工具使用独立格式和恢复流程。

中文原著、正文已经是中文的 EPUB，以及带翻译元数据的译本会直接进入滚动阅读，四种内容显示中仅原文可用。英文原著仍保留原有阅读和翻译功能。

### 程序与私人书库分离

公开代码和程序发布包**不包含原书、已保存译文、私有模型地址或 API Key**。模型地址和模型名称的默认值均为空。

所有私人书库资源统一保存在 `.data/`：原始 EPUB、书库数据库、译文、译图、导出 EPUB、书签、阅读进度和本地模型配置。该目录已被 Git 忽略，并从公开构建中排除。

在另一台电脑使用已有书库：

1. 先关闭阅读器再复制；也可使用下方工具生成一致性快照。
2. 把 `.data/` 放到新程序的根目录。
3. 启动程序，即可读取书架、已保存译文、阅读位置和书签。

如果需要不带旧备份和日志的私人迁移副本，可运行：

```powershell
python tools/export_library.py --output private-transfer/.data
```

该工具保留书库资源，清空副本中的 API Key，并暂停未完成任务。迁移副本仍属于私人数据，不应混入公开发布包；分享前请检查其中的本地模型配置。

### 小体积按书迁移包

双击 `pack-books.bat`，在 `private-transfer/book-packages-日期/` 中生成每本书独立的 ZIP。保留原书、译图、各版本译文、初译、进度和书签；省略导出缓存、日志、历史备份、重复的总数据库和模型配置。打包使用包含已提交 WAL 数据的一致快照，不必停止源阅读器；快照之后新增的进度留待下次打包。

单本书超过默认 **500 MiB** 时，自动生成 `.zip.0001`、`.zip.0002` 等分卷，并在 `library-manifest.json` 中保存每卷的 SHA-256 校验值。书内容和状态不变时，重复打包得到相同文件，后续可只上传新增或变化的书。需要放在 GitHub 时，请使用私人数据仓库的 Release；公开程序 Release 继续只放程序。

恢复时先关闭目标阅读器，把下载的书包和清单放在同一文件夹，再将文件夹拖到 `restore-books.bat` 上。工具校验后恢复下载完整的书，缺卷、损坏或已存在同一本书时会停止，不会覆盖旧进度。普通单个 ZIP 也可直接解压到 `.data/`；启动后自动导入书文件夹，模型配置沿用目标程序的设置。

```powershell
runtime/python.exe tools/package_library.py --part-mib 500
runtime/python.exe tools/package_library.py --book "完整书名" --output private-transfer/selected-books
runtime/python.exe tools/restore_library_packages.py --packages private-transfer/selected-books --target .data
```

`--book` 可写完整书名或编号，可重复选择多本。默认只打包当前书架；`--include-removed` 同时包含已从书架移除但文件夹还在的书。恢复时不指定 `--book`，整本未下载的书会跳过，部分分卷缺失则会报错。更新已有同一本书时，请恢复到新书库或先迁出旧版本。

### 开始使用

Windows 10/11 64 位电脑可直接在仓库点击 **Code → Download ZIP**，完整解压后双击 `start.bat`。`main` 分支包含便携 Python、Pillow 和中文字体，无需安装 Python 或 pip。阅读时保持启动窗口打开，不要在压缩包内直接运行。

Windows 便携发布包使用方法相同；手机同局域网访问使用 `start-mobile.bat`。启动失败时运行 `check-portable.bat`，检查运行环境、程序导入、字体及已有书库；正式构建的发布包还会校验文件完整性。

| 使用场景 | 启动入口 | 访问方式 |
| --- | --- | --- |
| 本机阅读 | `start.bat` | 电脑上自动打开的本机页面 |
| 手机与电脑在同一 Wi-Fi | `start-mobile.bat` | 启动窗口显示的局域网网址 |
| 自己的 iPhone 使用流量或其他 Wi-Fi | `start-tailscale.bat` | 两端先连接 Tailscale，再打开启动窗口给出的私网网址 |

开发时如需使用自己安装的 Python 3.10+，可运行：

```powershell
python -m pip install -r requirements.txt
python server.py --open
```

导入未加密 EPUB 后，在设置中选择 LLM 或 Azure Translate。图书 LLM 与查词 LLM 分别配置接口和模型，改变图书服务不会改变查词服务。

点击“导入图书 / 书架”选择 EPUB 或上述加密书架分卷，书架原来的 **＋** 已改为归档下载按钮。新书会自动在 `.data/books/` 下创建“书名 -- 短编号”文件夹，里面统一保存 `original.epub` 原书、`translated-images/` 译图、`exports/` 导出译本，以及 `book.sqlite3` 单书迁移快照。总数据库 `.data/reader.sqlite3` 继续负责日常读写；单书快照会额外保存该书的译文、初译、校对记录、进度和书签，不携带模型密钥。

**按书搬运：先正常关闭阅读器，只复制想搬走的书名文件夹到另一套程序的 `.data/books/`，再启动。** 目标程序会自动恢复这些书，无需搬走原来的总数据库；没复制的书会留在原处。把某书文件夹移出后，原书架会隐藏它，总数据库中的记录仍保留。文件夹可以改名，但要把里面的文件和 `book.json` 一起搬运。两边阅读器都应先关闭；运行中约每十秒同步快照，正常退出时完成最后同步。需要手动同步时，关闭阅读器后运行 `runtime/python.exe tools/organize_library.py --apply`。

旧版平铺书库会在启动时整理。也可以先关闭阅读器，再运行 `runtime/python.exe tools/organize_library.py --apply`，先备份数据库再迁移；去掉 `--apply` 只预览目录名称。整理保留原书、全部已有译本、进度和书签，并报告迁移前就已缺失的资源。

已有译文可离线阅读；新增翻译需要连接所选服务，正文校对需要大模型。单词翻译默认使用 Azure Translate，也可独立选择 LLM。程序不包含模型权重，处理所需内容会发送到所选服务。

### iPhone 个人异地访问

需要使用时，自行在服务器电脑和 iPhone 安装 Tailscale，登录同一个个人账号并打开连接。先正常关闭已经运行的阅读器，再双击 **`start-tailscale.bat`**，在 iPhone 的 Safari 打开启动窗口显示的电脑私网 IPv4 网址。手机使用流量或其他 Wi-Fi 均可；电脑需要保持开机、联网，阅读器持续运行。

沿用同一个书架、译文、阅读进度和模型配置。启动器自动检查客户端状态，只启用本机与检测到的 Tailscale 地址，普通局域网接口不启用；未安装、未连接或端口占用时会提示后退出。程序不安装或配置 Tailscale。[详细使用说明](docs/tailscale.md)包含自定义端口、iPhone 设置和防火墙排查。

需要更换端口时运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Tailscale -Port 8766`，手机访问的网址使用窗口实际显示的端口。已通过模拟客户端状态、HTTP 访问限制与便携版构建验证；真实 iPhone 外网连接需要在安装并连接 Tailscale 后验证。

### 已有译文与服务切换

不同服务／配置的译文仍分开保存，切换服务不会删除已有版本。如果新配置还没有当前书籍的译文，但本地已有其他版本，阅读器会询问是继续阅读已有译文，还是使用新服务翻译；没有任何本地译文时则正常开始翻译。**本地译文**按钮会显示来源和已保存段落数量，可随时切换，选择按书籍和配置记住。

阅读已有版本时，已保存段落和句子对应直接复用，不发送翻译请求。缺失段落由当前服务补齐，并保存在当前服务自己的缓存中。EPUB 导出使用所选阅读版本及已补齐的段落；整书后台处理和校对仍单独显示当前配置的任务进度。

单词翻译独立于正文服务和所选译本。Azure Translate 从词典获取基本义及英文词性缩写，并仅结合当前句及之前最多十句翻译选中单词；上下文过长时会按服务请求上限缩短。词性展示词典常见用法，语境结果属于机器翻译；单词标记丢失时会明确显示为所在句译文。需要更充分的辨义解释时，可在单词／语境翻译设置中选择 **LLM**。

### 翻译服务

**图书翻译**提供 **LLM** 和 **Azure Translate**。LLM 支持语境翻译、校对及图片翻译；Azure Translate 处理文字，图片保留原图，不计入其翻译进度。两者均支持段落保存、句子对应、整书处理、暂停／继续和 EPUB 导出。

**单词／语境翻译**单独选择 **LLM** 或 **Azure Translate**，一个选项同时控制基本义和语境结果，不随图书服务变化。查词 LLM 使用独立的接口、模型和可选 Key。悬浮框用一行小字显示“由 LLM 提供”或“由 Azure Translate 提供”。

### 保持私人书库精简

`.data/` 按 `books/书名 -- 短编号/` 组织书籍资源。共享 `reader.sqlite3` 保存配置与实时状态；每书的 `book.sqlite3` 额外保存独立迁移快照，方便只搬选中的书，因此会占用一份该书状态副本的空间。`library-catalog.json` 保留旧版导入信息。运行时出现的 `-wal`、`-shm` 是 SQLite 的临时文件。

发布包和测试产物放在 `.data/` 外的 `dist/`、`test-results/`。关闭阅读器后，可先检查、再清理无用生成文件：

```powershell
python tools/clean_library.py
python tools/clean_library.py --apply --prune-history
```

清理保留原书、当前各版本译文、正在引用的译图、阅读和任务状态；删除未引用译图、较旧导出文件，添加 `--prune-history` 时同时删除旧校对快照。LLM／Azure 的当前已保存译本保留可读。工具会逐表校验整理前后的数据库，再压缩空间；不会改变未来的历史保留规则。

### 公开构建与检查

```powershell
python tools/audit_public.py
python tools/build_release.py --zip
python -m unittest discover -s tests
```

公开构建始终使用空书架，仅包含程序源码、许可允许分发的字体、运行依赖、文档和人工构造的测试。私人图书资源和模型配置不会被打包。

当前为 Beta 版本，支持未加密 EPUB。复杂版式、细小或旋转文字，以及模型生成的译文可能需要对照原文检查。

## Directory layout / 目录结构

```text
ParallelReader/
├─ server.py
├─ reader/                 # Application logic / 程序逻辑
├─ public/                 # UI and licensed fonts / 界面与授权字体
├─ docs/                   # Public documentation and demo screenshot / 公开文档与示例截图
├─ tests/                  # Synthetic tests / 人工构造的测试
├─ tools/                  # Public build and private transfer tools / 构建与迁移工具
├─ runtime/                # Portable Python, Pillow, ZIP encryption and licenses / 便携运行环境与许可
├─ start.bat
├─ start-mobile.bat
├─ start-tailscale.bat      # Personal remote access / 个人异地访问
├─ check-portable.bat
└─ .data/                  # PRIVATE, Git-ignored, never publicly bundled / 私人书库
   ├─ reader.sqlite3
   └─ books/
      └─ Book Title -- edition/
         ├─ book.json
         ├─ original.epub
         ├─ book.sqlite3
         ├─ translated-images/
         └─ exports/
```

Third-party font and runtime licenses are included with their respective resources.  
第三方字体和运行时的许可随相应资源提供。
