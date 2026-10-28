import copy
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch
from xml.etree import ElementTree as ET

from reader.epub import parse_epub
from reader.export import BookExporter, ink_png
from reader.storage import Store
from reader.translation import profile
from server import make_server


def illustrated_epub(large_cover=False):
    files = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
        'OPS/book.opf': '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Illustrated Handbook</dc:title><dc:creator>Test</dc:creator><dc:language>en</dc:language><meta name="cover" content="cover"/></metadata><manifest><item id="cover" href="images/cover.png" media-type="image/png"/><item id="figure" href="images/figure.png" media-type="image/png"/><item id="front" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="main" href="main.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="front"/><itemref idref="main"/></spine></package>',
        'OPS/cover.xhtml': '<html xmlns="http://www.w3.org/1999/xhtml"><body><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="images/cover.png"/></svg></body></html>',
        'OPS/images/cover.png': ink_png() + (b'\0' * (3 * 1024 * 1024) if large_cover else b''),
        'OPS/images/figure.png': ink_png(),
    }
    body = '<h1>Contents</h1><h1>Introduction</h1><p>Welcome to this handbook.</p>'
    for index in range(1, 4):
        body += f'<h2>{index:03d}. Skill {index}</h2><p>' + 'Description words ' * 90 + '</p>'
        if index < 3:
            body += '<p><img src="images/figure.png" alt="Diagram"/></p>'
    body += '<div hidden="hidden"><img src="images/figure.png"/></div><p><img src="https://example.invalid/remote.png"/></p>'
    files['OPS/main.xhtml'] = '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + body + '</body></html>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


class IllustrationTests(unittest.TestCase):
    def test_images_svg_cover_and_numbered_directory_preserve_all_text_ids(self):
        data = illustrated_epub(True)
        with patch('reader.illustrations.enhance', side_effect=lambda book, *args: book):
            old = parse_epub(data)
        book = parse_epub(data)
        self.assertEqual([p['text'] for p in old['paragraphs']], [p['text'] for p in book['paragraphs']])
        self.assertEqual([p['id'] for p in old['paragraphs']], [p['id'] for p in book['paragraphs']])
        self.assertEqual(len(book['illustrations']), 3)
        self.assertEqual(len(book['images']), 2)
        self.assertEqual([c['title'] for c in book['chapters'] if c['title'][:3].isdigit()], ['001. Skill 1', '002. Skill 2', '003. Skill 3'])
        self.assertTrue(book['cover'].startswith('/api/books/'))
        self.assertEqual(book['pages'][0]['content'][0]['type'], 'image')
        for image in book['images'].values():
            self.assertEqual((image['width'], image['height']), (4, 1))
        starts = {page['paragraphs'][0] for page in book['pages']}
        self.assertTrue({page['paragraphs'][0] for page in old['pages']} <= starts)
        for illustration in book['illustrations'][1:]:
            self.assertIn('afterParagraph', illustration)
            page = next(page for page in book['pages'] if any(entry['type'] == 'image' and entry['id'] == illustration['id'] for entry in page['content']))
            self.assertIn(illustration['afterParagraph'], page['paragraphs'])

    def test_reimport_upgrades_images_and_maps_bookmarks_without_touching_translations(self):
        data = illustrated_epub(True)
        with patch('reader.illustrations.enhance', side_effect=lambda book, *args: book):
            old = parse_epub(data)
        book = parse_epub(data)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                store.add(old)
                fingerprint = profile(store.settings())
                for p in old['paragraphs']:
                    store.cache(old['id'], p['id'], fingerprint, '原有译文' + str(p['id']))
                store.progress(old['id'], len(old['pages']) - 1)
                store.set_bookmark(old['id'], 1, True)
                previous = store.book(old['id'])
                cached = store.translations(old['id'], range(len(old['paragraphs'])), fingerprint)
                upgraded = store.add(book, source_bytes=data)
                self.assertEqual(store.translations(old['id'], range(len(old['paragraphs'])), fingerprint), cached)
                self.assertEqual(upgraded['pages'][upgraded['currentPage']]['paragraphs'][0], previous['pages'][previous['currentPage']]['paragraphs'][0])
                self.assertEqual(upgraded['pages'][upgraded['bookmark']['page']]['paragraphs'][0], previous['pages'][1]['paragraphs'][0])
                self.assertTrue(upgraded['bookmark']['manual'])
                # A tab opened before migration may still send its old page index.
                store.progress(old['id'], len(old['pages']) - 1)
                self.assertEqual(store.book(old['id'])['currentPage'], upgraded['currentPage'])
                store.progress(old['id'], 1, layout_version=1)
                self.assertEqual(store.book(old['id'])['currentPage'], 1)
                for key in upgraded['images']:
                    self.assertTrue(store.image(upgraded, key)[0].startswith(b'\x89PNG'))
            finally:
                store.db.close()

    def test_export_retains_images_in_both_formats(self):
        data = illustrated_epub(True); book = parse_epub(data)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); exporter = None
            try:
                store.add(book, source_bytes=data)
                for p in book['paragraphs']:
                    store.cache(book['id'], p['id'], profile(store.settings()), '测试译文。')
                exporter = BookExporter(store)
                for kind in ['bilingual', 'translation']:
                    with zipfile.ZipFile(exporter.export(book, store.settings(), kind)) as archive:
                        self.assertEqual(len([name for name in archive.namelist() if name.startswith('EPUB/images/')]), 2)
                        figures = sum(len(ET.fromstring(archive.read(name)).findall('.//{http://www.w3.org/1999/xhtml}figure')) for name in archive.namelist() if name.startswith('EPUB/chapter-'))
                        self.assertEqual(figures, 3)
                        self.assertIn('EPUB/cover.png', archive.namelist())
            finally:
                if exporter:
                    exporter.close()
                store.db.close()

    def test_image_http_is_local_cached_and_rejects_unknown_assets(self):
        data = illustrated_epub(); book = parse_epub(data)
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory); app.store.add(book, source_bytes=data)
            thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = f'http://127.0.0.1:{app.server_port}/api/books/{book["id"]}/images/'
            try:
                with opener.open(url + next(iter(book['images']))) as response:
                    self.assertEqual(response.headers.get_content_type(), 'image/png')
                    self.assertEqual(response.read(), ink_png())
                    etag = response.headers['ETag']
                with self.assertRaises(urllib.error.HTTPError) as result:
                    opener.open(urllib.request.Request(url + next(iter(book['images'])), headers={'If-None-Match': etag}))
                self.assertEqual(result.exception.code, 304)
                with self.assertRaises(urllib.error.HTTPError) as result:
                    opener.open(url + 'unknown')
                self.assertEqual(result.exception.code, 404)
            finally:
                app.shutdown(); thread.join(); app.translator.close(); app.server_close(); app.store.db.close()
