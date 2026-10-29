"""Safe, dependency-free EPUB 2/3 text import. Never extracts executable content."""
import base64
import hashlib
import html
import posixpath
import re
import zipfile
from io import BytesIO
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_UPLOAD = 100 * 1024 * 1024
MAX_EXPANDED = 150 * 1024 * 1024
BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dt", "dd", "figcaption"}
SKIP = {"script", "style", "nav", "svg", "math"}


def tag(node):
    return node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""


def xml(data):
    text = data.decode("utf-8-sig", errors="replace")
    # XHTML EPUBs sometimes declare HTML entities in a DTD. Resolve only known
    # entities locally; ElementTree never downloads external DTDs.
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]+);", lambda m: html.unescape(m[0]) if m[1] not in {"amp", "lt", "gt", "quot", "apos"} else m[0], text)
    return ET.fromstring(text)


def plain(node):
    if tag(node) in SKIP or node.attrib.get("aria-hidden") == "true" or "hidden" in node.attrib:
        return ""
    result = [node.text or ""]
    for child in node:
        result.append("\n" if tag(child) == "br" else plain(child))
        result.append(child.tail or "")
    return "".join(result)


def clean(text):
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def resolve(base, href):
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(href.split("#")[0])))


def split_long(text, limit=1800):
    """Keep regular paragraphs intact, split unusually long ones at sentences."""
    while len(text) > limit:
        candidates = list(re.finditer(r'[.!?。！？][\s”’"\']+', text[:limit]))
        cut = candidates[-1].end() if candidates and candidates[-1].end() > limit // 3 else text.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def parse_epub(data, filename="book.epub"):
    if len(data) > MAX_UPLOAD:
        raise ValueError("EPUB 超过 100 MB 导入限制")
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("这不是有效的 EPUB 文件") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_EXPANDED or any(i.file_size > 20 * 1024 * 1024 for i in infos):
            raise ValueError("EPUB 解压后过大，无法导入")
        names = set(archive.namelist())
        if "META-INF/container.xml" not in names:
            raise ValueError("EPUB 缺少 META-INF/container.xml")
        container = xml(archive.read("META-INF/container.xml"))
        rootfile = next((n.attrib.get("full-path") for n in container.iter() if tag(n) == "rootfile"), None)
        if not rootfile or rootfile not in names:
            raise ValueError("EPUB 缺少有效的 OPF 内容索引")
        package = xml(archive.read(rootfile))
        metadata = next((n for n in package if tag(n) == "metadata"), None)
        def meta(name, default=""):
            return next((clean(plain(n)) for n in metadata if tag(n) == name), default) if metadata is not None else default
        manifest_node = next((n for n in package if tag(n) == "manifest"), None)
        if manifest_node is None:
            raise ValueError("EPUB 没有内容清单")
        manifest = {n.attrib.get("id"): {**n.attrib, "path": resolve(rootfile, n.attrib.get("href", ""))} for n in manifest_node}
        toc_titles = {}
        for item in manifest.values():
            try:
                if "nav" in item.get("properties", "").split():
                    nav = xml(archive.read(item["path"]))
                    for n in nav.iter():
                        if tag(n) == "a" and n.attrib.get("href"):
                            toc_titles.setdefault(resolve(item["path"], n.attrib["href"]), clean(plain(n)))
                elif item.get("media-type") == "application/x-dtbncx+xml":
                    for point in xml(archive.read(item["path"])).iter():
                        if tag(point) == "navpoint":
                            label = next((clean(plain(n)) for n in point if tag(n) == "navlabel"), "")
                            src = next((n.attrib.get("src") for n in point if tag(n) == "content"), None)
                            if src:
                                toc_titles.setdefault(resolve(item["path"], src), label)
            except (KeyError, ET.ParseError):
                continue
        encrypted = set()
        if "META-INF/encryption.xml" in names:
            encrypted = {unquote(n.attrib.get("URI", "")) for n in xml(archive.read("META-INF/encryption.xml")).iter() if tag(n) == "cipherreference"}
        paragraphs, chapters, pages = [], [], []
        spine = next((n for n in package if tag(n) == "spine"), None)
        if spine is None:
            raise ValueError("EPUB 没有阅读顺序索引")
        warnings = []
        for ref in spine:
            item = manifest.get(ref.attrib.get("idref"))
            if not item or ref.attrib.get("linear") == "no" or "nav" in item.get("properties", "").split():
                continue
            if item["path"] in encrypted:
                raise ValueError("此 EPUB 的正文经过 DRM 加密，请导入未加密的 EPUB")
            try:
                document = xml(archive.read(item["path"]))
            except (KeyError, ET.ParseError):
                warnings.append("无法解析章节：" + item.get("href", ""))
                continue
            body = next((n for n in document.iter() if tag(n) == "body"), document)
            blocks = []
            def visit(node):
                kind = tag(node)
                if kind in SKIP or node.attrib.get("aria-hidden") == "true" or "hidden" in node.attrib:
                    return
                has_block = any(tag(n) in BLOCKS for n in node.iter() if n is not node)
                if kind in BLOCKS and not has_block:
                    text = clean(plain(node))
                    if text:
                        blocks.append((kind, text))
                else:
                    # Preserve text-only divs / body as well as ordinary p blocks.
                    if node.text and clean(node.text):
                        blocks.append(("p", clean(node.text)))
                    for child in node:
                        visit(child)
                        if child.tail and clean(child.tail):
                            blocks.append(("p", clean(child.tail)))
            visit(body)
            if not blocks:
                continue
            heading = next((text for kind, text in blocks if kind.startswith("h")), "")
            title = toc_titles.get(item["path"]) or heading or f"章节 {len(chapters) + 1}"
            chapter = {"id": len(chapters), "title": title, "startPage": len(pages), "startParagraph": len(paragraphs)}
            chapters.append(chapter)
            page_ids, words = [], 0
            for kind, text in blocks:
                for part_index, part in enumerate(split_long(text)):
                    size = max(len(part.split()), len(re.findall(r'[\u3400-\u9fff]', part)) // 2)
                    if page_ids and (words + size > 300 or len(page_ids) >= 7) and words > 90:
                        pages.append({"id": len(pages), "chapter": chapter["id"], "paragraphs": page_ids})
                        page_ids, words = [], 0
                    pid = len(paragraphs)
                    paragraphs.append({"id": pid, "chapter": chapter["id"], "kind": "heading" if kind.startswith("h") else "paragraph", "text": part, "continued": part_index > 0})
                    page_ids.append(pid)
                    words += size
            if page_ids:
                pages.append({"id": len(pages), "chapter": chapter["id"], "paragraphs": page_ids})
            chapter["endPage"] = len(pages) - 1
        if not paragraphs:
            raise ValueError("未找到可阅读的文字；此版本支持文字型、未加密的 EPUB")
        cover = None
        cover_id = next((n.attrib.get("content") for n in metadata if tag(n) == "meta" and n.attrib.get("name") == "cover"), None) if metadata is not None else None
        cover_item = next((item for key, item in manifest.items() if key == cover_id or "cover-image" in item.get("properties", "").split()), None)
        if cover_item and cover_item.get("media-type") in {"image/jpeg", "image/png", "image/webp"}:
            try:
                image = archive.read(cover_item["path"])
                if len(image) < 3 * 1024 * 1024:
                    cover = "data:" + cover_item["media-type"] + ";base64," + base64.b64encode(image).decode()
            except KeyError:
                pass
        start = next((c["startPage"] for c in chapters if re.search(r"\b(prologue|chapter\s+(one|1)|one)\b|序章|第一章", c["title"], re.I)), 0)
        book = {"id": hashlib.sha256(data).hexdigest()[:24], "title": meta("title", filename.removesuffix(".epub")), "author": meta("creator", "未知作者"), "language": meta("language", "en"), "cover": cover, "chapters": chapters, "paragraphs": paragraphs, "pages": pages, "startPage": start, "warnings": warnings}
        book['alreadyTranslated'] = any(
            (tag(node) == 'identifier' and (node.text or '').startswith('urn:parallel:'))
            or any(key.rsplit('}', 1)[-1] == 'role' and value in {'trl', 'translator'} for key, value in node.attrib.items())
            or (tag(node) == 'meta' and node.attrib.get('property') == 'role' and (node.text or '').strip() == 'trl')
            for node in (metadata or []))
        from reader.reading_policy import prepare_book
        book = prepare_book(book)
        from reader.illustrations import enhance
        return enhance(book, archive, manifest, spine, metadata)

