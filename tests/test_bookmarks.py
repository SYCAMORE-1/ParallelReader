import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from reader.storage import Store
from server import make_server
from test_reader import queue_book


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = self.store.add(queue_book())

    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def test_auto_position_tracks_reading_until_manual_pin(self):
        self.store.progress(self.book['id'], 3)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 3)
        self.assertFalse(self.store.bookmark(self.book['id'])['manual'])
        self.store.set_bookmark(self.book['id'], 3)
        self.store.progress(self.book['id'], 7)
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 7)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 3)
        self.assertTrue(self.store.bookmark(self.book['id'])['manual'])

    def test_one_bookmark_per_book_replaces_previous_and_survives_reimport_restart(self):
        other = queue_book() | {'id': 'other-book'}
        self.store.add(other)
        self.store.set_bookmark(self.book['id'], 2)
        self.store.set_bookmark(other['id'], 4)
        self.store.set_bookmark(self.book['id'], 8)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bookmarks WHERE book=?', (self.book['id'],)).fetchone()[0], 1)
        self.store.add(queue_book())
        self.store.db.close(); self.store = Store(self.temp.name)
        self.assertEqual(self.store.book(self.book['id'])['bookmark']['page'], 8)
        self.assertEqual(self.store.book(other['id'])['bookmark']['page'], 4)
        self.assertEqual(self.store.library()[0]['bookmark']['manual'], True)
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 0)

    def test_existing_library_migrates_using_saved_reading_position(self):
        self.store.progress(self.book['id'], 7)
        with self.store.db:
            self.store.db.execute('DROP TABLE bookmarks')
        self.store.db.close(); self.store = Store(self.temp.name)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 7)
        self.assertFalse(self.store.bookmark(self.book['id'])['manual'])

    def test_invalid_page_does_not_move_bookmark(self):
        self.store.set_bookmark(self.book['id'], 3)
        for value in [True, -1, 10, '3', 2.5, None]:
            with self.assertRaises(ValueError):
                self.store.set_bookmark(self.book['id'], value)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 3)

    def test_concurrent_progress_cannot_overwrite_manual_bookmark(self):
        start = threading.Barrier(2)
        def pin():
            start.wait(); self.store.set_bookmark(self.book['id'], 3)
        def progress():
            start.wait(); self.store.progress(self.book['id'], 9)
        threads = [threading.Thread(target=fn) for fn in [pin, progress]]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 3)
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 9)

    def test_http_bookmark_and_progress_are_independent_and_origin_guarded(self):
        app = make_server(port=0, data_dir=self.temp.name)
        thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{app.server_port}/api/books/{self.book["id"]}'
        def post(path, data, origin=None):
            req = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', **({'Origin': origin} if origin else {})})
            with opener.open(req) as response: return json.load(response)
        try:
            mark = post('/bookmark', {'page': 4})
            self.assertEqual(mark['page'], 4)
            self.assertEqual(post('/progress', {'page': 8})['bookmark']['page'], 4)
            with opener.open(base) as response:
                book = json.load(response)
            self.assertEqual((book['currentPage'], book['bookmark']['page']), (8, 4))
            with self.assertRaises(urllib.error.HTTPError) as error:
                post('/bookmark', {'page': 1}, 'https://example.invalid')
            self.assertEqual(error.exception.code, 403)
        finally:
            app.shutdown(); app.translator.close(); app.server_close(); thread.join(2)
            for worker in app.translator.threads: worker.join(2)
            app.store.db.close()


if __name__ == '__main__': unittest.main()
