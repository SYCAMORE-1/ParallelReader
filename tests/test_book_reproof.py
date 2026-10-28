import collections
import tempfile
import threading
import unittest

from reader.reproof import Reproofreader
from reader.storage import Store
from reader.translation import Translator, profile
from reader.translation_rules import finish_translation, translation_plan
from test_per_book_concurrency import book_fixture
from test_reader import until


class BookReproofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = self.store.add(book_fixture('whole-book-review'))
        self.fingerprint = profile(self.store.settings())
        for p in self.book['paragraphs']:
            detail = finish_translation(translation_plan(p['text'], []), ['已校对的旧译文。'])
            self.store.cache(self.book['id'], p['id'], self.fingerprint, detail['text'], detail)
        self.store.progress(self.book['id'], 5)
        self.store.set_bookmark(self.book['id'], 7)
        self.worker = None
        self.release = threading.Event()

    def tearDown(self):
        if self.worker:
            self.worker.close()
        self.release.set()
        if self.worker:
            for thread in self.worker.threads:
                thread.join(3)
        self.store.db.close()
        self.temp.cleanup()

    def drain(self, queue):
        while True:
            work = queue.take(set())
            if work is None:
                break
            key, book, settings, _ = work
            try:
                queue.execute(key, book, settings)
            except Exception as error:
                queue.finish(key, str(error))
            else:
                queue.finish(key, None)

    def test_completed_cache_is_rechecked_and_another_click_starts_another_pass(self):
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            return snapshot['detail']
        self.worker = Translator(self.store, workers=1, reproof_fn=revise)
        first = self.worker.reproofread(self.book, {'scope': 'book'})
        self.assertEqual(first['completed'], 0)
        until(lambda: self.worker.reproof.jobs[first['id']]['state'] == 'completed')
        second = self.worker.reproofread(self.book, {'scope': 'book'})
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(second['round'], first['round'] + 1)
        self.assertEqual(second['completed'], 0)
        until(lambda: self.worker.reproof.jobs[second['id']]['state'] == 'completed')
        self.assertEqual(collections.Counter(calls), {i: 2 for i in range(12)})
        result = self.worker.reproof.status(self.book)[0]
        self.assertEqual((result['completed'], result['total'], result['completedChapters']), (12, 12, 6))
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 5)
        self.assertEqual(self.store.bookmark(self.book['id'])['page'], 7)
        self.assertEqual(self.store.db.execute('select count(*) from translation_revisions').fetchone()[0], 24)

    def test_repeated_start_joins_active_group_and_creation_is_durable(self):
        queue = Reproofreader(self.store)
        first = queue.request(self.book, {'scope': 'book'})
        second = queue.request(self.book, {'scope': 'book'})
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(queue.jobs), 7)
        saved = self.store.reproof_jobs()
        self.assertEqual(len(saved), 7)
        self.assertEqual(len(next(j for j in saved if j['scope'] == 'book')['children']), 6)

    def test_pause_keeps_inflight_result_and_resume_skips_this_pass_completed_work(self):
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            return snapshot['detail']
        queue = Reproofreader(self.store, revise)
        job = queue.request(self.book, {'scope': 'book'})
        key, book, settings, _ = queue.take(set())
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})
        queue.execute(key, book, settings)
        queue.finish(key, None)
        paused = queue.status(self.book)[0]
        self.assertEqual((paused['state'], paused['completed']), ('paused', 1))
        self.assertIn('正在处理', paused['reason'])
        self.assertIsNone(queue.take(set()))
        resumed = queue.request(self.book, {'scope': 'book', 'action': 'resume'})
        self.assertEqual(resumed['id'], job['id'])
        self.drain(queue)
        self.assertEqual(queue.status(self.book)[0]['state'], 'completed')
        self.assertEqual(collections.Counter(calls), {i: 1 for i in range(12)})

    def test_failure_retains_translation_and_only_failed_paragraph_is_retried(self):
        failing, calls = [True], []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            if pid == 4 and failing[0]:
                raise RuntimeError('片段未通过校验')
            return snapshot['detail']
        queue = Reproofreader(self.store, revise)
        first = queue.request(self.book, {'scope': 'book'})
        self.drain(queue)
        self.assertEqual(queue.status(self.book)[0]['state'], 'failed')
        self.assertEqual(self.store.translation(self.book['id'], 4, self.fingerprint), '已校对的旧译文。')
        failing[0] = False
        resumed = queue.request(self.book, {'scope': 'book', 'action': 'resume'})
        self.assertEqual(resumed['id'], first['id'])
        self.drain(queue)
        self.assertEqual(queue.status(self.book)[0]['state'], 'completed')
        self.assertEqual(calls.count(4), 2)
        self.assertTrue(all(calls.count(i) == 1 for i in range(12) if i != 4))

    def test_restart_continues_partial_pass_without_persisting_keys(self):
        self.store.save_settings(self.store.settings() | {'apiKey': 'primary-secret', 'reviewApiKey': 'review-secret'})
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            return snapshot['detail']
        queue = Reproofreader(self.store, revise)
        job = queue.request(self.book, {'scope': 'book'})
        key, book, settings, _ = queue.take(set())
        queue.execute(key, book, settings); queue.finish(key, None)
        serialized = ''.join(row[0] for row in self.store.db.execute('select value from reproof_jobs'))
        self.assertNotIn('secret', serialized)
        self.store.db.close()
        self.store = Store(self.temp.name)
        queue = Reproofreader(self.store, revise)
        self.drain(queue)
        self.assertEqual(queue.status(self.book)[0]['id'], job['id'])
        self.assertEqual(queue.status(self.book)[0]['completed'], 12)
        self.assertEqual(collections.Counter(calls), {i: 1 for i in range(12)})
        self.assertEqual(calls, list(range(12)), 'Restart continues the earliest unfinished chapter first')

    def test_connection_failure_pauses_all_chapters_and_old_results_stay_readable(self):
        queue = Reproofreader(self.store)
        queue.request(self.book, {'scope': 'book'})
        key, _, _, _ = queue.take(set())
        queue.finish(key, '无法连接校对模型')
        result = queue.status(self.book)[0]
        self.assertEqual(result['state'], 'paused')
        self.assertIsNone(queue.take(set()))
        self.assertEqual(self.store.translation(*key), '已校对的旧译文。')

    def test_partial_translation_is_rejected_without_creating_a_pass(self):
        with self.store.db:
            self.store.db.execute('delete from translations where paragraph=7')
        queue = Reproofreader(self.store)
        with self.assertRaisesRegex(ValueError, '先完成整本翻译'):
            queue.request(self.book, {'scope': 'book'})
        self.assertEqual(queue.jobs, {})
        self.assertEqual(self.store.reproof_jobs(), [])

    def test_paused_pass_stays_paused_after_queue_reload(self):
        queue = Reproofreader(self.store)
        job = queue.request(self.book, {'scope': 'book'})
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})
        reloaded = Reproofreader(self.store)
        self.assertIsNone(reloaded.take(set()))
        self.assertEqual(reloaded.status(self.book)[0]['state'], 'paused')
        self.assertEqual(reloaded.request(self.book, {'scope': 'book', 'action': 'resume'})['id'], job['id'])

    def test_manual_chapter_review_does_not_resume_a_paused_book_child(self):
        queue = Reproofreader(self.store, lambda book, pid, settings, snapshot, selection: snapshot['detail'])
        parent = queue.request(self.book, {'scope': 'book'})
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})
        chapter = queue.request(self.book, {'scope': 'chapter', 'chapter': 0})
        self.assertNotIn(chapter['id'], queue.jobs[parent['id']]['children'])
        self.drain(queue)
        whole = queue.status(self.book)[0]
        self.assertEqual((whole['state'], whole['completed']), ('paused', 0))


if __name__ == '__main__':
    unittest.main()
