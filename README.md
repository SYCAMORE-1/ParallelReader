# Parallel Reader®

**Read across languages. Stay in the story.**  
**跨越语言，专注阅读。**

A portable bilingual EPUB reader with contextual translation, proofreading, and in-image translation.  
一款支持语境翻译、译文校对和图片内翻译的便携式双语 EPUB 阅读器。

![Parallel Reader — Bilingual Reading / 双语对照阅读](docs/screenshots/reader.png)

## English

### Features

- Four reading layouts: original, Chinese translation, side-by-side, and alternating paragraphs.
- Paginated, continuous scrolling, and immersive reading with chapter navigation.
- Separate typography settings for each language and multiple reading themes.
- Context-aware translation using book metadata, surrounding passages, and available glossaries.
- Separate translation, proofreading, and optional word-lookup models, configured by the user.
- Keyless Microsoft web translation, plus optional Azure Translator API with your own credentials.
- Whole-book processing with progress, pause/resume, retries, and repeated proofreading passes.
- Up to four text chapters and two images concurrently per book.
- In-image translation with original/translated image switching and QR-code preservation.
- Sentence correspondence highlighting and contextual word lookup without reading ahead.
- Bookshelf cover views, reading/translation progress, one bookmark per book, and return-to-bookmark.
- Local translation caching and Chinese-only or bilingual EPUB export, including translated illustrations.

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

### Getting started

The Windows x64 portable package includes its runtime and Chinese fonts. Extract the entire package and run `start.bat`. Use `start-mobile.bat` for access from a phone on the same local network.

For source-based use:

```powershell
python -m pip install -r requirements.txt
python server.py --open
```

Import an unencrypted EPUB and configure compatible model services in Settings. An optional separate word-lookup model can be configured; leaving its endpoint and model name empty uses the translation model.

Saved content can be read offline. New translations require the selected service; proofreading and contextual word explanations require LLM mode. Model weights are not included. Content needed for a request is sent to the service you configure.

### Microsoft and Azure translation

For **no-configuration translation**, select **Microsoft translation (no setup)** in Settings. It uses Microsoft's public web translator, automatically obtains a temporary session, and needs no Azure account, Endpoint, or Key. Session credentials are kept only in memory. A network connection is required; upstream changes, verification pages, or rate limits may temporarily interrupt this option.

Microsoft web translation supports the same text-only reading and whole-book processing workflow described below. Its translations are cached separately from both LLM and Azure API translations. This option is distinct from the Azure cloud API's F0 free tier.

For your own Azure resource, select **Azure Translator API (own Key)** in **Settings → Translation service** and enter its **Endpoint**, **Key**, and, when required, **Region**. Global resources may leave Region blank; regional and multi-service resources require it. Use **Test connection** before saving; this sends a short text to Azure. The implementation uses the Translator Text API v3.0 with automatic source-language detection.

Azure mode supports paragraph translation, sentence correspondence, whole-book background translation, pause/resume, retries, local caching, and EPUB export. **Images remain original and are excluded from translation progress.** LLM proofreading, contextual word explanations, and image translation are unavailable in this mode. Azure does not use the LLM's context/glossary prompts. Switching back restores the LLM configuration; existing LLM translations remain saved separately. Switching services starts a separate translation cache, so existing LLM translations do not count toward Azure progress.

Credentials are stored locally in `.data/`; the API never returns saved keys to the browser. Public builds contain no credentials, and the private-library export tool removes keys from its copy. Azure usage is subject to your resource's pricing and quota. See [Microsoft's authentication documentation](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/authentication).

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
- 提供免配置的微软网页翻译，以及自备 Key 的 Azure Translator API 文字翻译。
- 全书后台处理，支持进度显示、暂停、继续、失败重试和多轮校对。
- 每本书最多同时处理四个文本章节和两张图片。
- 译文直接排入图片，可切换原图／译图，并保留二维码。
- 中英文句子对应高亮，双击单词查看不读取后文的语境解释。
- 封面书架、阅读／翻译进度、每书一个书签，从书架打开时返回书签。
- 译文缓存保存在本机，可导出包含译图的中文或双语 EPUB。

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

### 开始使用

Windows 64 位便携版已内置运行环境和中文字体。完整解压后双击 `start.bat`；手机同局域网访问使用 `start-mobile.bat`。

从源码运行：

```powershell
python -m pip install -r requirements.txt
python server.py --open
```

导入未加密 EPUB 后，在设置中填写兼容的模型服务。查词接口和模型名称同时留空时，查词使用翻译模型。

已有译文可离线阅读；新增翻译需要连接所选服务，校对和语境查词需要大模型模式。程序不包含模型权重，处理所需内容会发送到你配置的服务。

### 微软翻译与 Azure API

想要**开箱即用、无需配置**，在设置中选择**微软翻译（免配置）**即可。它使用微软公开的网页翻译服务，自动获取临时会话，无需 Azure 账号、Endpoint 或 Key；会话凭据仅保存在内存中。此功能需要联网，网页变化、验证页面或限流可能造成暂时不可用。

微软免配置翻译支持下述文字阅读和整书处理流程，缓存与大模型、Azure API 分开保存。此选项与 Azure 云 API 的 F0 免费额度是两种接入方式。

使用自己的 Azure 资源时，在**设置 → 翻译服务**中选择 **Azure Translator API（自备 Key）**，填写 **Endpoint、Key** 及所需的 **Region**。全局资源可留空 Region；区域资源和多服务资源需要填写。保存前可点击**测试连接**，向 Azure 发送一句简短文本。程序使用 Translator Text API v3.0，自动识别原文语言。

Azure 模式支持段落翻译、句子对应、全书后台翻译、暂停／继续、重试、本地缓存和 EPUB 导出。**图片保留原图，不计入翻译进度**；此模式不提供大模型校对、语境查词或图片翻译，也不使用大模型的上下文／术语表提示词。切回大模型后可恢复原配置，已有的大模型译文仍单独保存。两种服务分别缓存，原有大模型译文不会计入 Azure 的翻译进度。

密钥只保存在本机 `.data/` 中，接口不会把已保存的密钥返回浏览器。公开程序包不含密钥；私人书库导出工具会清空副本中的密钥。Azure 使用量受所用资源的计费与配额限制。配置说明见 [Microsoft 官方身份验证文档](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/authentication)。

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
├─ start.bat
├─ start-mobile.bat
└─ .data/                  # PRIVATE, Git-ignored, never publicly bundled / 私人书库
   ├─ reader.sqlite3
   ├─ sources/
   ├─ translated-images/
   └─ exports/
```

Third-party font and runtime licenses are included with their respective resources.  
第三方字体和运行时的许可随相应资源提供。
