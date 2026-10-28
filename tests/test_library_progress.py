import json
import tempfile
import threading
import unittest
import urllib.request

from reader.storage import Store
from reader.reproof import Reproofreader
from reader.translation import draft_settings, profile
from server import make_server
from test_reader import queue_book, until


class LibraryProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = self.store.add(queue_book())

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_only_current_final_translations_count_and_drafts_are_deduplicated(self):
        for pid in [0, 3]:
            self.store.cache(self.book['id'], pid, 'final', '译文')
        for pid in [0, 1]:
            self.store.cache_draft(self.book['id'], pid, 'draft', {'text': '初译'})
        self.store.cache(self.book['id'], 2, 'draft', '旧版初译')
        self.store.cache(self.book['id'], 9, 'other-profile', '其他模型译文')
        result = self.store.library_progress('final', 'draft')[self.book['id']]
        self.assertEqual((result['completed'], result['drafted'], result['total'], result['percent']), (2, 4, 10, 20.0))
        self.assertTrue(result['reviewEnabled'])
        self.assertEqual(self.store.library('final', 'draft')[0]['translationProgress'], result)

    def test_removed_books_excluded_and_translation_without_review_is_supported(self):
        other = self.store.add(queue_book() | {'id': 'removed'})
        self.store.remove(other['id'])
        self.store.cache_draft(self.book['id'], 0, 'draft', {'text': '未校对'})
        self.assertEqual(self.store.library_progress('final', 'draft')[self.book['id']]['percent'], 0.0)
        for pid in range(10):
            self.store.cache(self.book['id'], pid, 'primary', '已翻译')
        result = self.store.library_progress('primary')
        self.assertNotIn(other['id'], result)
        self.assertEqual(result[self.book['id']]['percent'], 100.0)
        self.assertFalse(result[self.book['id']]['reviewEnabled'])

    def test_new_review_round_counts_its_own_work_and_survives_pause_and_restart(self):
        fingerprint = profile(self.store.settings())
        for pid in range(10):
            self.store.cache(self.book['id'], pid, fingerprint, '已有译文')
        self.store.progress(self.book['id'], 8)
        self.store.set_bookmark(self.book['id'], 3)
        queue = Reproofreader(self.store)
        first = queue.request(self.book, {'scope': 'book'})
        stats = lambda: self.store.library_progress(fingerprint)[self.book['id']]
        self.assertEqual((stats()['mode'], stats()['percent'], stats()['round']), ('reproof', 0.0, 1))
        key, *_ = queue.take(set())
        queue.finish(key, None)
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})
        self.assertEqual((stats()['state'], stats()['percent']), ('paused', 10.0))
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.assertEqual(stats()['percent'], 10.0)
        queue = Reproofreader(self.store)
        resumed = queue.request(self.book, {'scope': 'book', 'action': 'resume'})
        self.assertEqual(resumed['id'], first['id'])
        while (work := queue.take(set())) is not None:
            queue.finish(work[0], None)
        self.assertEqual((stats()['state'], stats()['percent']), ('completed', 100.0))
        second = queue.request(self.book, {'scope': 'book'})
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual((stats()['round'], stats()['percent']), (2, 0.0))
        self.assertEqual(len(self.store.cached_paragraphs(self.book['id'], fingerprint)), 10)
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 8)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 3)
        self.assertNotIn('mode', self.store.library_progress('other-profile')[self.book['id']])

    def test_live_api_shows_paused_review_progress_instead_of_full_translation_cache(self):
        fingerprint = profile(self.store.settings())
        for pid in range(10):
            self.store.cache(self.book['id'], pid, fingerprint, '已有译文')
        started, release = threading.Event(), threading.Event()
        def revise(*args):
            started.set()
            release.wait(5)
            return {'text': '本轮校对译文'}
        app = make_server(port=0, data_dir=self.temp.name, reproof_fn=revise)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{app.server_port}'
        def request(path, value=None):
            req = urllib.request.Request(base + path, data=json.dumps(value).encode() if value is not None else None,
                                         headers={'Content-Type': 'application/json'})
            with opener.open(req) as response:
                return json.load(response)
        try:
            path = '/api/books/' + self.book['id']
            request(path + '/reproofread', {'scope': 'book'})
            self.assertTrue(started.wait(2))
            stats = request('/api/library-progress')['books'][self.book['id']]
            self.assertEqual((stats['mode'], stats['percent']), ('reproof', 0.0))
            self.assertEqual(request(path + '/pretranslate')['percent'], 100.0)
            request(path + '/reproofread', {'scope': 'book', 'action': 'pause'})
            release.set()
            until(lambda: app.store.library_progress(fingerprint)[self.book['id']]['completed'] == 1)
            stats = request('/api/library-progress')['books'][self.book['id']]
            self.assertEqual((stats['state'], stats['percent']), ('paused', 10.0))
            self.assertEqual(request('/api/books')[0]['translationProgress'], stats)
        finally:
            release.set()
            app.shutdown()
            app.translator.close()
            for worker in app.translator.threads:
                worker.join(3)
            app.server_close()
            app.store.db.close()
            thread.join(3)

    def test_lightweight_api_matches_shelf_and_book_progress(self):
        settings = self.store.settings()
        fingerprint, draft = profile(settings), profile(draft_settings(settings))
        self.store.cache(self.book['id'], 0, fingerprint, '已校对')
        self.store.cache_draft(self.book['id'], 1, draft, {'text': '初译'})
        app = make_server(port=0, data_dir=self.temp.name)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{app.server_port}'
        def get(path):
            with opener.open(base + path) as response:
                return json.load(response)
        try:
            compact = get('/api/library-progress')
            self.assertEqual(compact['profile'], fingerprint)
            stats = compact['books'][self.book['id']]
            self.assertEqual(get('/api/books')[0]['translationProgress'], stats)
            bulk = get('/api/books/' + self.book['id'] + '/pretranslate')
            for key in ['completed', 'total', 'percent', 'drafted', 'reviewEnabled', 'profile']:
                self.assertEqual(stats[key], bulk[key])
            self.assertNotIn('cover', compact)
            self.assertEqual(stats['percent'], 10.0)
        finally:
            app.shutdown()
            app.translator.close()
            for worker in app.translator.threads:
                worker.join(3)
            app.server_close()
            app.store.db.close()
            thread.join(3)
