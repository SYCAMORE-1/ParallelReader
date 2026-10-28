"""Build portable EPUB 3 books from complete, durable translation snapshots."""
import base64
import hashlib
import json
import re
import struct
import threading
import zipfile
import zlib
from datetime import datetime, timezone
from html import escape

from reader.translation import profile

EXPORT_VERSION = 4
# Keep the exported markers consistent with the app's reading view.
REDACTION_MARKERS = re.compile(r'\[\s*(?:redacted|已删节|已刪節|机密|機密|已编辑|已編輯|已遮盖|已遮蓋|已删除|已刪除|已隐去|已隱去)(?:[\s:：\-–—]+[x\s]*)?\s*\]|\[\s*(?:x{2,}(?:\s+x+)*|x(?:\s+x+)+)\s*\]|(?<![A-Za-z0-9_])x{2,}(?![A-Za-z0-9_])', re.I)


def ink_png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 4, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + b'\x00\x00\x00' * 4)) + chunk(b'IEND', b''))


def text(value):
    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value)))


def reading_text(value):
    """Embed real ink images so reader font/color overrides cannot reveal x's."""
    def mark(marker):
        width = max(1.2, len(marker) * .55)
        fallback_width = min(240, round(width * 18))
        return (f'<img class="redaction-mark" src="redaction-ink.png" alt="已遮盖" '
                f'width="{fallback_width}" height="16" style="width:{width:.2f}em;height:0.9em;max-width:100%;max-width:calc(100% - 1.2em);vertical-align:-0.1em;"/>')
    value, parts, cursor = str(value), [], 0
    for match in REDACTION_MARKERS.finditer(value):
        parts.append(text(value[cursor:match.start()]))
        marker = match[0]
        if not re.match(r'^\[\s*x', marker, re.I):
            parts.append(mark(marker))
        else:
            groups, offset = list(re.finditer('x+', marker, re.I)), 0
            for index, group in enumerate(groups):
                start = 0 if index == 0 else group.start()
                end = len(marker) if index == len(groups) - 1 else group.end()
                parts.extend((text(marker[offset:start]), mark(marker[start:end])))
                offset = end
        cursor = match.end()
    parts.append(text(value[cursor:]))
    return ''.join(parts)


def write_epub(path, book, translations, settings, kind, image_loader=None, translated_images=None):
    language = {'简体中文': 'zh-Hans', '繁體中文': 'zh-Hant', '日本語': 'ja'}[settings['targetLanguage']]
    bilingual = kind == 'bilingual'
    title = book['title'] + (' · 双语对照' if bilingual else ' · ' + settings['targetLanguage'])
    def document(body, name, lang=language):
        return ('<?xml version="1.0" encoding="utf-8"?>'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{text(lang)}" lang="{text(lang)}">'
                f'<head><title>{text(name)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body>{body}</body></html>')
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="redaction-ink" href="redaction-ink.png" media-type="image/png"/>']
    spine, links, ncx = [], [], []
    image_files = {}
    translated_images = translated_images or {}
    for key, asset in book.get('images', {}).items():
        extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[asset['mime']]
        image_files[key] = f'images/{key}.{extension}'
    before, after_images = {}, {}
    for illustration in book.get('illustrations', []):
        target, pid = (after_images, illustration['afterParagraph']) if 'afterParagraph' in illustration else (before, illustration['beforeParagraph'])
        target.setdefault(pid, []).append(illustration)
    def figures(pid, after=False):
        parts = []
        for entry in (after_images if after else before).get(pid, []):
            asset = entry['asset']
            original = f'<figure id="illustration-{entry["id"]}"><img src="{image_files[asset]}" alt="{text(entry.get("alt") or "原书插图")}"/></figure>'
            translated = f'<figure id="translated-illustration-{entry["id"]}"><img src="images/{asset}-translated.png" alt="中文译图"/></figure>' if asset in translated_images else ''
            parts.append((original + translated) if bilingual else (translated or original))
        return ''.join(parts)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr('EPUB/style.css', 'body{line-height:1.8;margin:5%;}p{margin:0 0 1em;orphans:2;widows:2;}h1,h2{page-break-after:avoid;}section.pair{margin-bottom:1.6em;}p.original{margin-bottom:.5em;}p.translation{margin-top:0;}img{max-width:100%;max-height:95vh;}img.redaction-mark{display:inline;vertical-align:-.1em;max-width:100%;border:1px solid #333833;border-radius:.07em;box-sizing:border-box;}')
        archive.writestr('EPUB/redaction-ink.png', ink_png())
        for key, filename in image_files.items():
            if image_loader is None:
                raise ValueError('插图源文件不可用，请重新导入原书')
            data, mime = image_loader(book, key)
            archive.writestr('EPUB/' + filename, data)
            manifest.append(f'<item id="image-{key}" href="{filename}" media-type="{mime}"/>')
        for key, data in translated_images.items():
            filename = f'images/{key}-translated.png'
            archive.writestr('EPUB/' + filename, data)
            manifest.append(f'<item id="translated-image-{key}" href="{filename}" media-type="image/png"/>')
        cover = re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,(.+)', book.get('cover') or '', re.S)
        cover = cover.groups() if cover else None
        if not cover and book.get('coverAsset') and image_loader:
            data, mime = image_loader(book, book['coverAsset'])
            cover = (mime, base64.b64encode(data).decode())
        if cover:
            mime, encoded = cover
            extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[mime]
            filename = 'cover.' + extension
            archive.writestr('EPUB/' + filename, base64.b64decode(encoded, validate=True))
            archive.writestr('EPUB/cover.xhtml', document(f'<div><img src="{filename}" alt="{text(book["title"])}"/></div>', title))
            manifest.extend([f'<item id="cover-image" href="{filename}" media-type="{mime}" properties="cover-image"/>', '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>'])
            spine.append('<itemref idref="cover-page"/>')
        by_chapter = {}
        for paragraph in book['paragraphs']:
            by_chapter.setdefault(paragraph['chapter'], []).append(paragraph)
        for index, chapter in enumerate(book['chapters']):
            name, filename, identity = chapter['title'], f'chapter-{index + 1}.xhtml', f'chapter-{index + 1}'
            body = [f'<h1>{text(name)}</h1>']
            for paragraph in by_chapter.get(chapter['id'], []):
                pid = paragraph['id']
                body.append(figures(pid))
                target = reading_text(translations[pid])
                tag = 'h2' if paragraph.get('kind') == 'heading' else 'p'
                if bilingual:
                    body.append(f'<section class="pair" id="p-{pid}"><{tag} class="original" xml:lang="{text(book.get("language") or "en")}" lang="{text(book.get("language") or "en")}">{reading_text(paragraph["text"])}</{tag}><{tag} class="translation" xml:lang="{language}" lang="{language}">{target}</{tag}></section>')
                else:
                    body.append(f'<{tag} id="p-{pid}">{target}</{tag}>')
                body.append(figures(pid, after=True))
            if index == len(book['chapters']) - 1:
                body.append(figures(len(book['paragraphs'])))
            archive.writestr('EPUB/' + filename, document(''.join(body), name))
            manifest.append(f'<item id="{identity}" href="{filename}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{identity}"/>')
            links.append(f'<li><a href="{filename}">{text(name)}</a></li>')
            ncx.append(f'<navPoint id="{identity}" playOrder="{index + 1}"><navLabel><text>{text(name)}</text></navLabel><content src="{filename}"/></navPoint>')
        archive.writestr('EPUB/nav.xhtml', document('<nav epub:type="toc" id="toc"><h1>目录</h1><ol>' + ''.join(links) + '</ol></nav>', title))
        identifier = 'urn:parallel:' + book['id'] + ':' + profile(settings) + ':' + kind
        archive.writestr('EPUB/toc.ncx', '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="' + identifier + '"/></head><docTitle><text>' + text(title) + '</text></docTitle><navMap>' + ''.join(ncx) + '</navMap></ncx>')
        modified = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        original_language = f'<dc:language>{text(book.get("language") or "en")}</dc:language>' if bilingual else ''
        archive.writestr('EPUB/package.opf', '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                         f'<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{text(title)}</dc:title><dc:creator>{text(book["author"])}</dc:creator><dc:language>{language}</dc:language>{original_language}<meta property="dcterms:modified">{modified}</meta>'
                         '</metadata><manifest>' + ''.join(manifest) + '</manifest><spine toc="ncx">' + ''.join(spine) + '</spine></package>')


class BookExporter:
    """Export all completed books automatically, including books finished before upgrade."""
    def __init__(self, store):
        self.store, self.lock = store, threading.RLock()
        self.directory = store.directory / 'exports'
        self.directory.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.errors = {}
        self.thread = threading.Thread(target=self.run, name='epub-export', daemon=True)
        self.thread.start()

    def export(self, book, settings, kind='bilingual', reading=False):
        if kind not in {'bilingual', 'translation'}:
            raise ValueError('不支持的 EPUB 格式')
        fingerprint = profile(settings)
        with self.lock:
            ids = [paragraph['id'] for paragraph in book['paragraphs']]
            translations = self.store.translations(book['id'], ids, fingerprint)
            if reading:
                from reader.reading_sources import translations as reading_translations
                source, saved = reading_translations(self.store, book, ids, settings)
                if source['selectionRequired']:
                    raise ValueError('请先选择要阅读和导出的译文')
                fingerprint = source['selectedProfile']
                translations = {pid: item['text'] for pid, item in saved.items()}
            if any(not translations.get(pid, '').strip() for pid in ids):
                raise ValueError('整本翻译尚未完成，请等待所有段落就绪后再下载 EPUB')
            translated, image_digests = {}, {}
            service = getattr(self.store, 'image_service', None)
            if service:
                for asset in service.book_jobs.assets(book):
                    result = service.ready_result(book, asset, settings)
                    if not result:
                        raise ValueError('图片翻译或译图排版尚未完成，请等待图片就绪后再下载 EPUB')
                    translated[asset] = (self.store.directory / result['render']['file']).read_bytes()
                    image_digests[asset] = result['render']['digest']
            snapshot = {'translations': translations, 'images': book.get('images', {}), 'illustrations': book.get('illustrations', []), 'translatedImages': image_digests}
            digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            path = self.directory / f'{book["id"]}-{fingerprint}-{kind}.epub'
            stamp = path.with_suffix('.json')
            signature = {'version': EXPORT_VERSION, 'digest': digest}
            try:
                if path.is_file() and json.loads(stamp.read_text(encoding='utf-8')) == signature:
                    return path
            except (OSError, ValueError):
                pass  # An interrupted metadata write must not block future exports.
            temporary = path.with_suffix('.tmp')
            stamp_temporary = stamp.with_name(stamp.name + '.tmp')
            try:
                write_epub(temporary, book, translations, settings, kind, self.store.image, translated)
                temporary.replace(path)
                stamp_temporary.write_text(json.dumps(signature), encoding='utf-8')
                stamp_temporary.replace(stamp)
            finally:
                temporary.unlink(missing_ok=True)
                stamp_temporary.unlink(missing_ok=True)
            return path

    def run(self):
        while not self.stop.is_set():
            settings = self.store.settings()
            fingerprint = profile(settings)
            for entry in self.store.library():
                if self.stop.is_set():
                    return
                try:
                    book = self.store.book(entry['id'])
                    completed = self.store.cached_paragraphs(book['id'], fingerprint)
                    if all(p['id'] in completed for p in book['paragraphs']):
                        for kind in ('bilingual', 'translation'):
                            self.export(book, settings, kind)
                        self.errors.pop(book['id'], None)
                except Exception as error:
                    self.errors[entry['id']] = str(error)
            self.stop.wait(5)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)
