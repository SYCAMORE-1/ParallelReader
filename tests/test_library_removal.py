import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from reader.storage import Store
from reader.translation import Translator, profile
from server import make_server
from test_reader import queue_book, until


class LibraryRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = self.store.add(queue_book())

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_removal_persists_and_reimport_restores_translation_and_position(self):
        self.store.progress(self.book['id'], 8)
        self.store.set_bookmark(self.book['id'], 3)
        self.store.cache(self.book['id'], 0, 'saved-profile', '已有译文')
        other = self.store.add(queue_book() | {'id': 'other'})
        self.store.remove(self.book['id'])
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.assertEqual([b['id'] for b in self.store.library()], [other['id']])
        with self.assertRaises(KeyError):
            self.store.book(self.book['id'])
        restored = self.store.add(queue_book())
        self.assertEqual(restored['currentPage'], 8)
        self.assertEqual(restored['bookmark']['page'], 3)
        self.assertTrue(restored['bookmark']['manual'])
        self.assertEqual(self.store.translation(self.book['id'], 0, 'saved-profile'), '已有译文')
        self.assertEqual(len(self.store.library()), 2)

    def test_removal_stops_future_work_without_stopping_other_books(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def translate(book, pid, settings):
            calls.append((book['id'], pid))
            if len(calls) == 1:
                started.set()
                release.wait(5)
            return '已翻译'

        worker = Translator(self.store, workers=1, translate_fn=translate)
        try:
            worker.bulk_action(self.book, 'start')
            self.assertTrue(started.wait(2))
            worker.window(self.book, 5)
            other = self.store.add(queue_book() | {'id': 'other'})
            worker.bulk_action(other, 'start')
            worker.remove_book(self.book['id'])
            self.assertFalse(any(key[0] == self.book['id'] for key in worker.desired))
            with self.assertRaises(KeyError):
                worker.window(self.book, 1)
            with self.assertRaises(KeyError):
                worker.bulk_action(self.book, 'resume')
            with self.assertRaises(KeyError):
                worker.reproofread(self.book, {'scope': 'book'})
            release.set()
            until(lambda: worker.bulk_status(other)['state'] == 'completed')
            self.assertEqual([pid for bid, pid in calls if bid == self.book['id']], [0])
            self.assertEqual(len([1 for bid, _ in calls if bid == other['id']]), 10)
            job = next(j for j in self.store.bulk_jobs() if j['book'] == self.book['id'])
            self.assertEqual(job['state'], 'paused')
            self.assertEqual(self.store.translation(self.book['id'], 0, profile(self.store.settings())), '已翻译')
        finally:
            release.set()
            worker.close()
            for thread in worker.threads:
                thread.join(3)

    def test_queued_reproof_is_paused_durably(self):
        worker = Translator(self.store, workers=1, translate_fn=lambda *args: '已翻译')
        try:
            fingerprint = profile(self.store.settings())
            for pid in range(10):
                self.store.cache(self.book['id'], pid, fingerprint, '原译文')
            with worker.condition:
                job = worker.reproof.request(self.book, {'scope': 'chapter', 'chapter': 0})
                worker.remove_book(self.book['id'])
            saved = next(j for j in self.store.reproof_jobs() if j['id'] == job['id'])
            self.assertEqual(saved['state'], 'paused')
            self.assertFalse(worker.reproof.active)
            self.assertEqual(self.store.translation(self.book['id'], 0, fingerprint), '原译文')
        finally:
            worker.close()
            for thread in worker.threads:
                thread.join(3)

    def test_http_removal_rejects_cross_origin_and_hides_detail(self):
        app = make_server(port=0, data_dir=self.temp.name)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{app.server_port}'

        def remove(origin):
            request = urllib.request.Request(base + '/api/books/' + self.book['id'] + '/remove',
                                             data=b'{}', headers={'Content-Type': 'application/json', 'Origin': origin})
            with opener.open(request) as response:
                return json.load(response)

        try:
            with self.assertRaises(urllib.error.HTTPError) as failure:
                remove('https://untrusted.invalid')
            self.assertEqual(failure.exception.code, 403)
            self.assertEqual(len(self.store.library()), 1)
            self.assertTrue(remove(base)['ok'])
            with opener.open(base + '/api/books') as response:
                self.assertEqual(json.load(response), [])
            with self.assertRaises(urllib.error.HTTPError) as failure:
                opener.open(base + '/api/books/' + self.book['id'])
            self.assertEqual(failure.exception.code, 404)
        finally:
            app.shutdown()
            app.translator.close()
            for worker in app.translator.threads:
                worker.join(3)
            app.server_close()
            app.store.db.close()
            thread.join(3)


if __name__ == '__main__':
    unittest.main()
