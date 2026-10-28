import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from reader.epub import parse_epub
from reader.export import BookExporter, EXPORT_VERSION
from reader.storage import Store
from reader.translation import Translator, profile
from server import make_server
from test_reader import queue_book, until


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = queue_book()
        self.book['title'] = 'Story <with> & quotes “书”'
        self.book['cover'] = 'data:image/png;base64,' + base64.b64encode(b'cover-fixture').decode()
        self.book = self.store.add(self.book)
        self.settings = self.store.settings()
        self.exporter = BookExporter(self.store)

    def complete(self):
        for paragraph in self.book['paragraphs']:
            self.store.cache(self.book['id'], paragraph['id'], profile(self.settings), f'译文 {paragraph["id"]}，保留Morgan和“对话” & <符号>。')

    def tearDown(self):
        self.exporter.close()
        self.store.db.close()
        self.temp.cleanup()

    def test_complete_snapshot_builds_two_valid_epubs_with_cover_toc_and_all_paragraphs(self):
        self.complete()
        for kind in ('bilingual', 'translation'):
            path = self.exporter.export(self.book, self.settings, kind)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist()[0], 'mimetype')
                self.assertEqual(archive.getinfo('mimetype').compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
                self.assertEqual(archive.read('EPUB/cover.png'), b'cover-fixture')
                for name in archive.namelist():
                    if name.endswith(('.xml', '.opf', '.ncx', '.xhtml')):
                        ET.fromstring(archive.read(name))
                chapters = [ET.fromstring(archive.read(name)) for name in archive.namelist() if name.startswith('EPUB/chapter-')]
                for pid in range(10):
                    content = ''.join(''.join(chapter.itertext()) for chapter in chapters)
                    self.assertIn(f'译文 {pid}，保留Morgan和“对话” & <符号>。', content)
                    if kind == 'bilingual':
                        self.assertIn(f'Paragraph {pid}.', content)
                    else:
                        self.assertNotIn(f'Paragraph {pid}.', content)
                identifiers = [node.attrib['id'] for chapter in chapters for node in chapter.iter() if 'id' in node.attrib]
                self.assertEqual(len(set(identifiers)), 10)
            imported = parse_epub(path.read_bytes(), path.name)
            self.assertEqual(len(imported['chapters']), len(self.book['chapters']))
            self.assertGreaterEqual(len(imported['paragraphs']), 10)
            self.assertEqual(imported['cover'], self.book['cover'])

    def test_missing_or_different_profile_never_produces_partial_book(self):
        self.store.cache(self.book['id'], 0, profile(self.settings), '译文')
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            self.exporter.export(self.book, self.settings)
        self.complete()
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            self.exporter.export(self.book, self.settings | {'model': 'other'})
        with self.assertRaises(ValueError):
            self.exporter.export(self.book, self.settings, '../other')

    def test_redactions_are_embedded_ink_images_in_both_formats_with_word_gaps(self):
        self.exporter.close()
        self.complete()
        self.book['paragraphs'][3]['text'] = 'A [redacted], [Redacted X X X], xxx xxxxx xxxxxxxxx xxxx, [xxxx], [xxx xxxxx]; Exxon X-ray & <safe>.'
        target = '资料[已删节]，[已编辑 X X X]，xxx xxxxx xxxxxxxxx xxxx，[xxxx]，[xxx xxxxx]；& <符号>。'
        self.store.cache(self.book['id'], 3, profile(self.settings), target)
        before = self.store.translation(self.book['id'], 3, profile(self.settings))
        ns = {'x': 'http://www.w3.org/1999/xhtml', 'o': 'http://www.idpf.org/2007/opf'}
        for kind in ('bilingual', 'translation'):
            with zipfile.ZipFile(self.exporter.export(self.book, self.settings, kind)) as archive:
                self.assertTrue(archive.read('EPUB/redaction-ink.png').startswith(b'\x89PNG\r\n\x1a\n'))
                opf = ET.fromstring(archive.read('EPUB/package.opf'))
                self.assertIsNotNone(opf.find("o:manifest/o:item[@href='redaction-ink.png'][@media-type='image/png']", ns))
                chapter = ET.fromstring(archive.read('EPUB/chapter-1.xhtml'))
                pair = chapter.find(".//x:section[@id='p-3']", ns) if kind == 'bilingual' else None
                nodes = list(pair) if pair is not None else [chapter.find(".//x:p[@id='p-3']", ns)]
                for node in nodes:
                    images = node.findall('x:img', ns)
                    self.assertEqual(len(images), 9)
                    self.assertTrue(all(image.attrib['alt'] == '已遮盖' and image.attrib['src'] == 'redaction-ink.png' for image in images))
                    self.assertEqual([images[i].tail for i in [2, 3, 4]], [' ', ' ', ' '])
                    self.assertEqual(images[7].tail, ' ')
                    visible = ''.join(node.itertext())
                    self.assertNotIn('[redacted]', visible.lower())
                    self.assertNotIn('xxxx', visible)
                    self.assertNotIn('[已删节]', visible)
                if kind == 'bilingual':
                    self.assertIn('Exxon X-ray & <safe>.', ''.join(nodes[0].itertext()))
                self.assertIn('& <符号>。', ''.join(nodes[-1].itertext()))
        self.assertEqual(self.store.translation(self.book['id'], 3, profile(self.settings)), before)

    def test_old_export_style_is_regenerated_without_retranslating(self):
        self.exporter.close()
        self.complete()
        path = self.exporter.export(self.book, self.settings)
        signature = json.loads(path.with_suffix('.json').read_text())
        signature['version'] = EXPORT_VERSION - 1
        path.with_suffix('.json').write_text(json.dumps(signature))
        path.write_bytes(b'old-export-before-ink-images')
        cached = self.store.translations(self.book['id'], range(10), profile(self.settings))
        self.assertEqual(self.exporter.export(self.book, self.settings), path)
        self.assertEqual(self.store.translations(self.book['id'], range(10), profile(self.settings)), cached)
        with zipfile.ZipFile(path) as archive:
            self.assertIn('EPUB/redaction-ink.png', archive.namelist())
        self.assertEqual(json.loads(path.with_suffix('.json').read_text())['version'], EXPORT_VERSION)

    def test_existing_export_is_reused_and_updated_translation_regenerates_it(self):
        self.complete()
        path = self.exporter.export(self.book, self.settings)
        timestamp = path.stat().st_mtime_ns
        self.assertEqual(path, self.exporter.export(self.book, self.settings))
        self.assertEqual(timestamp, path.stat().st_mtime_ns)
        self.store.cache(self.book['id'], 5, profile(self.settings), '更新后的译文。')
        self.exporter.export(self.book, self.settings)
        with zipfile.ZipFile(path) as archive:
            self.assertIn('更新后的译文。', archive.read('EPUB/chapter-1.xhtml').decode())

    def test_auto_export_after_completion_and_service_restart(self):
        self.complete()
        self.exporter.close()
        self.exporter = BookExporter(self.store)
        until(lambda: len(list(self.exporter.directory.glob('*.epub'))) == 2)
        self.assertFalse(self.exporter.errors)

    def test_interrupted_metadata_write_is_recovered(self):
        self.complete()
        path = self.exporter.export(self.book, self.settings)
        path.with_suffix('.json').write_text('{"version":', encoding='utf-8')
        self.exporter.export(self.book, self.settings)
        self.assertEqual(json.loads(path.with_suffix('.json').read_text())['version'], EXPORT_VERSION)
        self.assertTrue(zipfile.is_zipfile(path))

    def test_extended_reading_window_caches_every_displayed_page_and_prefetches_neighbors(self):
        calls = []
        worker = Translator(self.store, workers=1, translate_fn=lambda book, pid, settings: calls.append(pid) or f'译文{pid}')
        try:
            worker.window(self.book, 2, selected=6, end_page=6)
            until(lambda: len(calls) == 9)
            self.assertEqual(calls[0], 6)
            self.assertEqual(set(calls), set(range(9)))
            status = worker.status(self.book, 2, end_page=6)
            self.assertEqual([page['page'] for page in status['pages']], list(range(9)))
            self.assertTrue(all(item['state'] == 'ready' for item in status['items'].values()))
        finally:
            worker.close()


class ExportHttpTests(unittest.TestCase):
    def test_download_full_snapshot_and_extended_window_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory)
            app.store.add(queue_book())
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{app.server_port}/api/books/queue-test'
            try:
                with self.assertRaises(urllib.error.HTTPError) as response:
                    opener.open(base + '/export')
                self.assertEqual(response.exception.code, 400)
                for pid in range(10):
                    app.store.cache('queue-test', pid, profile(app.store.settings()), f'译文{pid}')
                with opener.open(base + '/export?format=bilingual') as response:
                    self.assertEqual(response.headers.get_content_type(), 'application/epub+zip')
                    self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
                    self.assertTrue(zipfile.is_zipfile(BytesIO(response.read())))
                self.assertTrue(list(app.exporter.directory.glob('*-bilingual.epub')))
                request = urllib.request.Request(base + '/window', json.dumps({'page': 2, 'endPage': 6, 'selected': 6, 'enabled': False}).encode(), {'Content-Type': 'application/json'})
                with opener.open(request) as response:
                    self.assertIn('profile', json.load(response))
                with opener.open(base + '/status?page=2&endPage=6') as response:
                    self.assertEqual(len(json.load(response)['pages']), 9)
                for suffix in ('/status?page=0&endPage=9', '/status?page=4&endPage=3', '/export?format=../../reader.sqlite3'):
                    with self.assertRaises(urllib.error.HTTPError) as response:
                        opener.open(base + suffix)
                    self.assertEqual(response.exception.code, 400)
            finally:
                app.shutdown(); thread.join(); app.translator.close(); app.server_close(); app.store.db.close()
