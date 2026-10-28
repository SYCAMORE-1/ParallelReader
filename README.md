# Parallel Reader®

**A portable bilingual EPUB reader with context-aware translation.**  
**一款支持语境翻译的便携式双语 EPUB 阅读器。**

---

## English

Parallel Reader combines EPUB reading, AI translation, and proofreading in one local application. Read the original and translated text together, look up words in context, and prepare translations in the background.

### Features

- **Four reading layouts** — Original text, translation only, side-by-side comparison, and alternating original/translated paragraphs.
- **Flexible navigation** — Paginated and continuous scrolling modes, immersive reading, a chapter directory, and previous/next chapter navigation.
- **Independent typography** — Adjust fonts, sizes, and weights separately for the original and translated text. Choose from multiple reading themes.
- **Context-aware translation** — Uses the book title, surrounding passages, and available book glossaries to improve terminology and interpretation.
- **Translation and proofreading** — Supports separate models for initial translation and proofreading, plus manual proofreading of selected sentences, chapters, or an entire book.
- **Background processing** — Pretranslate books with visible progress, pause/resume, and retries. Each book supports up to four text chapters and two images concurrently.
- **In-image translation** — Recognizes text in illustrations and places translations directly into the images. Switch between original and translated images while preserving QR codes.
- **Sentence comparison and word lookup** — Highlight corresponding sentences across languages. Double-click an English word to view its basic meaning, abbreviated part of speech, and contextual meaning. Contextual word lookup uses the current sentence and preceding context to avoid spoilers.
- **Bookshelf and bookmarks** — Browse list or cover views with separate reading and translation progress indicators. Each book retains one bookmark, and reopening it returns to that position.
- **Local storage and EPUB export** — Keep saved translations, translated images, reading positions, and bookmarks locally. Export Chinese-only or bilingual EPUB files with translated illustrations.

### Getting started

The portable edition is designed for **Windows 10/11 x64** and includes the required runtime and Chinese fonts.

1. Extract the entire package.
2. Run `start.bat`.
3. Import an EPUB file and configure a compatible model service when translation is needed.

Use `start-mobile.bat` to access the reader from a phone on the same local network. Keep the computer and reader service running.

Saved translations are available offline. Generating new translations requires access to a configured model service; model weights are not included.

### Current scope

Supports unencrypted EPUB files. Complex page layouts, rotated image text, and small annotations may require comparison with the original. AI-generated translations may contain errors.

---

## 中文

Parallel Reader 将 EPUB 阅读、AI 翻译与校对整合在一个本地应用中。你可以对照阅读原文和译文、结合语境查词，也可以提前在后台完成整本书的翻译。

### 功能特性

- **四种阅读布局** — 支持原文、纯译文、双列对照，以及译文紧跟原文的段落跟随模式。
- **灵活的阅读导航** — 支持翻页、连续滚动、沉浸阅读、章节目录和上一章／下一章跳转。
- **独立排版设置** — 原文与译文的字体、字号和粗细可分别调整，提供多种阅读主题。
- **结合语境翻译** — 参考书名、相邻段落和书中可用的术语表，改善术语选择和语义理解。
- **翻译与校对流程** — 支持分别配置初译和校对模型，可手动重新校对选中的句子、章节或整本书。
- **后台预翻译** — 显示处理进度，支持暂停、继续和失败重试；每本书最多同时处理四个文本章节和两张图片。
- **图片内文字翻译** — 识别插图中的文字，将译文直接排入图片；支持原图／译图切换，并保留二维码。
- **句子对照与语境查词** — 点击句子可高亮对应译句；双击英文单词可查看基本义、英文词性缩写和文中含义。语境查词仅使用当前句及此前上下文，避免读取后文造成剧透。
- **书架与书签** — 提供列表和封面视图，分别显示阅读与翻译进度。每本书保留一个书签，再次打开时返回书签位置。
- **本地保存与 EPUB 导出** — 在本机保存译文、译图、阅读位置和书签，支持导出包含译图的纯中文或双语 EPUB。

### 开始使用

便携版面向 **Windows 10/11 64 位系统**，已包含所需运行环境和中文字体。

1. 完整解压程序包。
2. 双击 `start.bat`。
3. 导入 EPUB，需要翻译时配置兼容的模型服务。

手机访问可运行 `start-mobile.bat`，让手机与电脑连接同一局域网，并保持电脑及阅读器服务运行。

已保存的译文可以离线阅读。生成新译文需要连接已配置的模型服务，程序包不包含模型权重。

### 当前范围

支持未加密的 EPUB 文件。复杂版式、旋转文字和细小图注可能需要对照原文检查；AI 生成的译文也可能存在错误。
