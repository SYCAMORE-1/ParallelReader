"""Recover illustrations and handbook navigation without changing text paragraph IDs."""
import hashlib
import re
import struct
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from reader.epub import BLOCKS, SKIP, clean, plain, resolve, split_long, tag, xml

IMAGE_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}


def dimensions(archive, path):
    with archive.open(path) as stream:
        data = stream.read(65536)
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        return struct.unpack('!II', data[16:24])
    if data[:6] in {b'GIF87a', b'GIF89a'} and len(data) >= 10:
        return struct.unpack('<HH', data[6:10])
    if data.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 9 <= len(data):
            if data[offset] != 255:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0, 255, 216, 217}:
                continue
            length = int.from_bytes(data[offset:offset + 2], 'big')
            if marker in {192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207}:
                height, width = struct.unpack('!HH', data[offset + 3:offset + 7])
                return width, height
            if length < 2:
                break
            offset += length
    if data[:4] == b'RIFF' and data[8:16] == b'WEBPVP8X' and len(data) >= 30:
        return int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1
    return None, None


def enhance(book, archive, manifest, spine, metadata):
    by_path = {item['path']: item for item in manifest.values()}
    assets, illustrations, headings = {}, [], []
    cursor = 0

    def asset(path):
        item = by_path.get(path)
        if not item or item.get('media-type') not in IMAGE_TYPES or path not in archive.namelist():
            return None
        key = hashlib.sha256(path.encode()).hexdigest()[:20]
        if key not in assets:
            width, height = dimensions(archive, path)
            assets[key] = {'path': path, 'mime': item['media-type'], 'width': width, 'height': height}
        return key

    for ref in spine:
        item = manifest.get(ref.attrib.get('idref'))
        if not item or ref.attrib.get('linear') == 'no' or 'nav' in item.get('properties', '').split():
            continue
        try:
            document = xml(archive.read(item['path']))
        except (KeyError, ET.ParseError):
            continue
        body = next((node for node in document.iter() if tag(node) == 'body'), document)
        blocks, events = [], []

        def capture(node, position):
            if node.attrib.get('aria-hidden') == 'true' or 'hidden' in node.attrib:
                return
            kind = tag(node)
            if kind in {'img', 'image'}:
                href = node.get('src') if kind == 'img' else node.get('href') or node.get('{http://www.w3.org/1999/xlink}href')
                if href and not urlsplit(href).scheme and not urlsplit(href).netloc:
                    key = asset(resolve(item['path'], href))
                    if key:
                        events.append((position, key, clean(node.get('alt', ''))))
            elif kind not in SKIP or kind == 'svg':
                for child in node:
                    capture(child, position)

        def visit(node):
            kind = tag(node)
            if node.attrib.get('aria-hidden') == 'true' or 'hidden' in node.attrib:
                return
            if kind in SKIP:
                if kind == 'svg':
                    capture(node, len(blocks))
                return
            if kind in {'img', 'image'}:
                capture(node, len(blocks))
                return
            has_block = any(tag(child) in BLOCKS for child in node.iter() if child is not node)
            if kind in BLOCKS and not has_block:
                value = clean(plain(node))
                position = len(blocks)
                if value:
                    blocks.append((kind, value))
                capture(node, position + (1 if value and clean(node.text or '') else 0))
            else:
                if node.text and clean(node.text):
                    blocks.append(('p', clean(node.text)))
                for child in node:
                    visit(child)
                    if child.tail and clean(child.tail):
                        blocks.append(('p', clean(child.tail)))

        visit(body)
        starts = []
        for kind, value in blocks:
            starts.append(cursor)
            if kind in {'h1', 'h2'}:
                headings.append((cursor, value))
            for part in split_long(value):
                if cursor >= len(book['paragraphs']) or book['paragraphs'][cursor]['text'] != part:
                    raise ValueError('插图恢复时正文结构不一致，原有译文与进度已保留')
                cursor += 1
        for position, key, alt in events:
            before = starts[position] if position < len(starts) else cursor
            illustrations.append({'id': len(illustrations), 'asset': key, 'alt': alt, 'beforeParagraph': before})

    if cursor != len(book['paragraphs']):
        raise ValueError('插图恢复时正文数量不一致，原有译文与进度已保留')

    # Some converted handbooks have an empty NCX and one huge XHTML file.
    # Recover their actual numbered sections while keeping every text ID intact.
    if len(book['chapters']) == 1 and sum(bool(re.match(r'^\d{1,3}[.)]\s*\S', title)) for _, title in headings) >= 3:
        old_page_starts = {page['paragraphs'][0] for page in book['pages'] if page['paragraphs']}
        boundaries = [(0, '书名页')]
        for pid, title in headings:
            if title.casefold() in {'contents', 'table of contents'}:
                continue
            if pid == 0:
                boundaries[0] = (pid, title)
            elif not boundaries or boundaries[-1][0] != pid:
                boundaries.append((pid, title))
        chapters, pages = [], []
        for index, (start, title) in enumerate(boundaries):
            end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(book['paragraphs'])
            chapter = {'id': index, 'title': title, 'startPage': len(pages), 'startParagraph': start}
            ids = []
            for pid in range(start, end):
                paragraph = book['paragraphs'][pid]
                paragraph['chapter'] = index
                if ids and pid in old_page_starts:
                    pages.append({'id': len(pages), 'chapter': index, 'paragraphs': ids})
                    ids = []
                ids.append(pid)
            if ids:
                pages.append({'id': len(pages), 'chapter': index, 'paragraphs': ids})
            chapter['endPage'] = len(pages) - 1
            chapters.append(chapter)
        book.update(chapters=chapters, pages=pages, startPage=0)

    before, after = {}, {}
    chapter_starts = {chapter['startParagraph'] for chapter in book['chapters']}
    for illustration in illustrations:
        pid = illustration['beforeParagraph']
        if pid > 0 and pid in chapter_starts:
            illustration['afterParagraph'] = pid - 1
            after.setdefault(pid - 1, []).append(illustration['id'])
        else:
            before.setdefault(pid, []).append(illustration['id'])
    for page in book['pages']:
        content = []
        for pid in page['paragraphs']:
            content.extend({'type': 'image', 'id': index} for index in before.get(pid, []))
            content.append({'type': 'paragraph', 'id': pid})
            content.extend({'type': 'image', 'id': index} for index in after.get(pid, []))
        page['content'] = content
    if book['pages']:
        book['pages'][-1]['content'].extend({'type': 'image', 'id': index} for index in before.get(len(book['paragraphs']), []))

    cover_id = next((node.get('content') for node in metadata if tag(node) == 'meta' and node.get('name') == 'cover'), None) if metadata is not None else None
    cover_item = next((value for key, value in manifest.items() if key == cover_id or 'cover-image' in value.get('properties', '').split()), None)
    if cover_item:
        key = asset(cover_item['path'])
        if key:
            book['coverAsset'] = key
            if not book.get('cover'):
                book['cover'] = f'/api/books/{book["id"]}/images/{key}'
    book.update(images=assets, illustrations=illustrations, imageVersion=1)
    return book
