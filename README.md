# Parallel Reader®

**Read across languages. Stay in the story.**  
**跨越语言，专注阅读。**

A portable bilingual EPUB reader with context-aware translation, proofreading, and in-image text translation.

一款支持语境翻译、译文校对和图片内文字翻译的便携式双语 EPUB 阅读器。

**Version / 版本：** `v0.1.0-beta`  
**Platform / 平台：** Windows 10 / 11 x64

![Parallel Reader — Bilingual Reading / 双语对照阅读](./download.png)

---

## English

### Overview

Parallel Reader brings EPUB reading, AI translation, and proofreading together in a local application.

Read the original and translated text side by side, explore word meanings without reading ahead, and prepare entire books for offline reading.

### Features

#### Four reading layouts

Switch between:

- **Original** — Read the original text.
- **Translation** — Read the Chinese translation.
- **Side by side** — Compare original and translated paragraphs in two columns.
- **Follow** — Display each translated paragraph directly below its original.

#### Flexible reading experience

- Paginated and continuous scrolling modes.
- Immersive reading with reduced interface distractions.
- Chapter directory and previous/next chapter navigation.
- Independently adjustable fonts, sizes, and weights for each language.
- Multiple themes, including light, parchment yellow, green, and dark backgrounds.

#### Context-aware translation and proofreading

- Uses book titles and surrounding passages as translation references.
- Incorporates relevant terminology from available book glossaries.
- Supports separate models for initial translation and proofreading.
- Preserves personal names in their original spelling.
- Allows selected sentences, individual chapters, or an entire book to be proofread again.
- Saves successful revisions directly into the existing translation.

#### Background book processing

- Pretranslate entire books while continuing to read.
- View processing progress and retry failed items.
- Pause and resume unfinished tasks.
- Resume saved tasks after restarting the application.
- Process up to **four text chapters and two images concurrently per book**.
- Reuse saved translations and intermediate results where possible.

Different books have independent processing slots.

#### In-image translation

- Recognizes text inside illustrations.
- Places translated text directly into the images.
- Includes images in whole-book translation and proofreading.
- Switches between original and translated images.
- Supports image enlargement for closer inspection.
- Preserves QR codes and their embedded logos.

#### Sentence comparison and word lookup

- Click a sentence to highlight its corresponding translation.
- Double-click an English word to view its basic meaning, abbreviated part of speech, and contextual meaning.
- Contextual word lookup uses the current sentence and up to ten preceding sentences, without reading ahead.

#### Bookshelf and bookmarks

- List and cover thumbnail views.
- Separate reading and translation/proofreading progress indicators.
- One bookmark per book, with new bookmarks replacing the previous one.
- Bookmark indicators in the chapter directory.
- Reopening a book from the bookshelf returns to its bookmark.

#### Local storage and EPUB export

- Saves translations, translated images, reading positions, and bookmarks locally.
- Reuses saved content when reopening a book.
- Exports Chinese-only and bilingual EPUB files.
- Includes translated illustrations in exported EPUB files.

### Getting started

The portable edition includes the required runtime and Chinese fonts. No Python installation is required.

1. Download and extract the entire portable package.
2. Place it in a folder where your Windows account has write access.
3. Run `start.bat`.
4. Open or import an EPUB file.
5. Configure a compatible model service when translation is needed.

Keep the application’s startup window open while reading or processing books.

### Mobile access

Run `start-mobile.bat` and open the address shown in the startup window on your phone.

The phone and computer must be on the same local network. The computer and reader service must remain running.

### Offline use and data

Saved translations can be read offline. Generating new translations, proofreading, and word explanations requires access to a configured model service.

Model weights are not included in the portable package.

The application stores its library and saved content in the `.data` folder. Preserve this folder when moving your library to another computer.

Book content needed for translation or proofreading is sent to the model service you configure.

### Current limitations

This is a beta release.

- Supports unencrypted EPUB files.
- Complex EPUB layouts may not be reproduced exactly.
- Small, rotated, or partially obscured image text may require manual comparison.
- In-image translation may have layout imperfections on complex backgrounds.
- AI-generated translations and explanations may contain errors.

Original text and images remain available for comparison.

---

## 中文

### 简介

Parallel Reader 将 EPUB 阅读、AI 翻译和译文校对整合在一个本地应用中。

你可以对照阅读原文与译文，在不读取后文的情况下查看单词的语境含义，也可以提前在后台完成整本书的翻译，供之后离线阅读。

### 功能特性

#### 四种阅读布局

随时切换以下模式：

- **原文** — 阅读英文原文。
- **中文** — 阅读中文译文。
- **双列** — 在左右两栏中对照原文和译文。
- **跟随** — 每段原文后紧接对应译文。

#### 灵活的阅读体验

- 支持翻页和连续滚动。
- 支持减少界面干扰的沉浸阅读。
- 提供章节目录和上一章／下一章导航。
- 原文与译文的字体、字号和粗细可独立调整。
- 提供浅色、羊皮纸黄、绿色和深色等多种主题。

#### 结合语境的翻译与校对

- 参考书名和相邻段落理解当前内容。
- 使用书中可用术语表里的相关释义。
- 支持分别配置初译模型和校对模型。
- 保留人名的原始拼写。
- 支持重新校对选中的句子、单个章节或整本书。
- 校对成功后，直接更新并保存现有译文。

#### 全书后台处理

- 在继续阅读的同时预翻译整本书。
- 显示处理进度，并支持重试失败项。
- 支持暂停和继续未完成任务。
- 应用重启后可接续已保存的任务。
- **每本书最多同时处理四个文本章节和两张图片。**
- 尽可能复用已保存的译文和中间结果。

不同书籍拥有独立的处理名额。

#### 图片内文字翻译

- 识别插图中的文字。
- 将译文直接排入图片。
- 图片自动参与整本翻译和全书校对。
- 支持原图／中文译图切换。
- 支持放大图片，方便核对细节。
- 保留二维码及其中的标识。

#### 句子对照与语境查词

- 点击句子，高亮对应的原文或译文。
- 双击英文单词，查看基本义、英文词性缩写和文中含义。
- 语境查词仅使用当前句及之前最多十句，不读取后文，减少剧透。

#### 书架与书签

- 提供列表和封面缩略图视图。
- 分别显示阅读进度和翻译／校对进度。
- 每本书保留一个书签，新书签会替换旧书签。
- 在章节目录中显示书签标记。
- 从书架重新打开图书时，直接返回书签位置。

#### 本地保存与 EPUB 导出

- 在本机保存译文、译图、阅读位置和书签。
- 再次打开图书时复用已保存内容。
- 支持导出纯中文和双语 EPUB。
- 导出的 EPUB 包含翻译后的插图。

### 开始使用

便携版已包含所需运行环境和中文字体，无需安装 Python。

1. 下载并完整解压便携包。
2. 将文件夹放在当前 Windows 用户具有写入权限的位置。
3. 双击 `start.bat`。
4. 打开或导入 EPUB 文件。
5. 需要翻译时，在设置中配置兼容的模型服务。

阅读和后台处理期间，请保持启动窗口打开。

### 手机访问

运行 `start-mobile.bat`，在手机浏览器中打开启动窗口显示的地址。

手机与电脑需要连接同一局域网，电脑及阅读器服务需要保持运行。

### 离线使用与数据

已保存的译文可以离线阅读。生成新译文、重新校对和单词解释需要连接已配置的模型服务。

便携包不包含模型权重。

书库和已保存内容位于 `.data` 文件夹。迁移到其他电脑时，请保留该文件夹。

翻译或校对所需的书籍内容会发送到你配置的模型服务。

### 当前限制

当前版本为 Beta 测试版。

- 支持未加密的 EPUB 文件。
- 复杂 EPUB 版式可能无法完整还原。
- 图片中的细小、旋转或被遮挡文字可能需要人工核对。
- 复杂背景上的图片翻译可能存在排版偏差。
- AI 生成的译文和解释可能存在错误。

原文和原图始终可供对照。
