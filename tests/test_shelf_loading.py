import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image

from reader.epub import parse_epub
from server import make_server
from test_illustrations import illustrated_epub
from test_reader import queue_book


class ShelfLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = make_server(port=0, data_dir=self.temp.name)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.base = f'http://127.0.0.1:{self.app.server_port}'
        output = io.BytesIO()
        Image.new('RGB', (1200, 1800), 'purple').save(output, format='PNG')
        # Model a legacy embedded cover with megabytes of trailing image metadata.
        self.original_cover = 'data:image/png;base64,' + base64.b64encode(output.getvalue() + b'\0' * (3 * 1024 * 1024)).decode()
        self.book = self.app.store.add(queue_book() | {'cover': self.original_cover})

    def tearDown(self):
        self.app.shutdown()
        self.thread.join()
        self.app.translator.close()
        self.app.server_close()
        self.app.store.db.close()
        self.temp.cleanup()

    def get(self, path, **headers):
        return self.opener.open(urllib.request.Request(self.base + path, headers=headers), timeout=10)

    def test_initial_shelf_is_small_and_does_not_wait_for_translation_statistics(self):
        self.app.store.progress(self.book['id'], 8)
        self.app.store.set_bookmark(self.book['id'], 3)
        with patch.object(self.app.store, 'library_progress', side_effect=AssertionError('Statistics must not block shelf loading')):
            with self.get('/api/books?summary=1') as response:
                raw = response.read()
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertLess(len(raw), 2048)
            summary, = json.loads(raw)
            self.assertEqual(summary['cover'], f'/api/books/{self.book["id"]}/cover')
            self.assertEqual((summary['pageCount'], summary['currentPage'], summary['bookmark']['page']), (10, 8, 3))
            self.assertNotIn('translationProgress', summary)
        with self.get('/api/books') as response:
            full, = json.load(response)
            self.assertIn('translationProgress', full)
        self.assertEqual(self.app.store.book(self.book['id'])['cover'], self.original_cover)

    def test_embedded_cover_is_resized_cached_and_original_is_preserved(self):
        with self.get(f'/api/books/{self.book["id"]}/cover') as response:
            data = response.read()
            self.assertEqual(response.headers.get_content_type(), 'image/jpeg')
            self.assertIn('private', response.headers['Cache-Control'])
            etag = response.headers['ETag']
        self.assertLess(len(data), 50000)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (320, 480))
        with patch.object(self.app.store, 'book', side_effect=AssertionError('Cached covers must not reread full book data')):
            with self.assertRaises(urllib.error.HTTPError) as result:
                self.get(f'/api/books/{self.book["id"]}/cover', **{'If-None-Match': etag})
            self.assertEqual(result.exception.code, 304)
        self.assertEqual(self.app.store.book(self.book['id'])['cover'], self.original_cover)

    def test_epub_asset_covers_and_original_images_are_available(self):
        source = illustrated_epub(True)
        book = self.app.store.add(parse_epub(source), source_bytes=source)
        self.assertTrue(book['cover'].startswith('/api/books/'))
        with self.get(f'/api/books/{book["id"]}/cover') as response:
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertLessEqual(image.width, 320)
                self.assertLessEqual(image.height, 480)
        with self.get(book['cover']) as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertGreater(len(response.read()), 3 * 1024 * 1024)

    def test_removed_books_and_unapproved_hosts_cannot_read_cached_covers(self):
        route = f'/api/books/{self.book["id"]}/cover'
        with self.get(route) as response:
            response.read()
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.get(route, Host='attacker.test')
        self.assertEqual(result.exception.code, 403)
        self.app.store.remove(self.book['id'])
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.get(route)
        self.assertEqual(result.exception.code, 404)
        with self.get('/api/books?summary=1') as response:
            self.assertEqual(json.load(response), [])

    def test_books_without_covers_keep_the_placeholder(self):
        book = self.app.store.add(queue_book() | {'id': 'no-cover'})
        summaries = self.app.store.library()
        self.assertIsNone(next(item for item in summaries if item['id'] == book['id'])['cover'])
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.get('/api/books/no-cover/cover')
        self.assertEqual(result.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
