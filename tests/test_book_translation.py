"""Whole-book jobs share the reading queue and survive service restarts."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from reader.storage import Store
from reader.translation import Translator, profile
from server import make_server
from test_reader import queue_book, until


class BookTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = self.store.add(queue_book())
        self.workers = []
        self.releases = []

    def worker(self, fake, count=1):
        worker = Translator(self.store, workers=count, translate_fn=fake)
        self.workers.append(worker)
        return worker

    def stop(self, worker):
        worker.close()
        for thread in worker.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def gate(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)
        return started, release

    def tearDown(self):
        for worker in self.workers:
            worker.close()
        for release in self.releases:
            release.set()
        for worker in self.workers:
            for thread in worker.threads:
                thread.join(3)
        self.store.db.close()
        self.temp.cleanup()

    def test_existing_translations_are_reused_and_details_saved_without_page_navigation(self):
        fingerprint = profile(self.store.settings())
        self.store.cache(self.book['id'], 2, fingerprint, '已有译文')
        self.store.progress(self.book['id'], 7)
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            return {'text': f'第{pid}段', 'alignment': [{'source': [0], 'target': [0]}]}
        worker = self.worker(fake, count=2)
        worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(sorted(calls), [i for i in range(10) if i != 2])
        self.assertEqual(worker.bulk_status(self.book)['completed'], 10)
        self.assertEqual(worker.bulk_status(self.book)['percent'], 100)
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 7)
        self.assertEqual(self.store.translation_detail(self.book['id'], 9, fingerprint)['alignment'], [{'source': [0], 'target': [0]}])
        worker.window(self.book, 9)
        until(lambda: not worker.active)
        self.assertEqual(len(calls), 9)

    def make_chapters(self, count=5):
        self.book['chapters'] = [{'id': i, 'title': f'Chapter {i + 1}', 'startPage': i * 2, 'endPage': i * 2 + 1} for i in range(count)]
        for paragraph in self.book['paragraphs']:
            paragraph['chapter'] = paragraph['id'] // 2
        for page in self.book['pages']:
            page['chapter'] = page['id'] // 2
        self.book['id'] = 'chapter-test'
        self.book = self.store.add(self.book)

    def test_four_chapters_run_together_in_order_with_a_reserved_reading_worker(self):
        self.make_chapters()
        release = threading.Event()
        self.releases.append(release)
        calls, call_lock = [], threading.Lock()
        def fake(book, pid, settings):
            with call_lock:
                calls.append(pid)
            release.wait(3)
            return '译文'
        worker = self.worker(fake, count=5)
        worker.bulk_action(self.book, 'start')
        until(lambda: len(calls) == 4)
        self.assertEqual(set(calls), {0, 2, 4, 6})
        status = worker.bulk_status(self.book)
        self.assertEqual(status['parallelChapters'], 4)
        self.assertEqual({chapter['id'] for chapter in status['activeChapters']}, {0, 1, 2, 3})
        self.assertEqual(status['totalChapters'], 5)
        # A reading request starts while all four background requests are
        # still blocked, rather than waiting for an entire chapter or batch.
        worker.window(self.book, 9, selected=9, enabled=False)
        until(lambda: 9 in calls)
        self.assertFalse(release.is_set())
        self.assertEqual(len(worker.active), 5)
        self.assertEqual(sum(role == 'bulk' for role in worker.active_roles.values()), 4)
        release.set()
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(sorted(calls), list(range(10)), 'Reading/background overlap must be translated only once')
        for chapter in range(4):
            self.assertLess(calls.index(chapter * 2), calls.index(chapter * 2 + 1))
        self.assertEqual(worker.bulk_status(self.book)['completedChapters'], 5)

    def test_chapter_stays_serial_even_with_four_background_slots(self):
        started, release = self.gate()
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return '译文'
        worker = self.worker(fake, count=5)
        worker.bulk_action(self.book, 'start')
        self.assertTrue(started.wait(2))
        time.sleep(.05)
        self.assertEqual(calls, [0])
        self.assertEqual(len(worker.active), 1)
        release.set()
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(calls, list(range(10)))

    def test_pause_and_restart_keep_parallel_chapter_progress_without_retranslating(self):
        self.make_chapters()
        release = threading.Event()
        self.releases.append(release)
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            release.wait(3)
            return '译文'
        worker = self.worker(fake, count=5)
        worker.bulk_action(self.book, 'start')
        until(lambda: len(calls) == 4)
        self.assertEqual(worker.bulk_action(self.book, 'pause')['state'], 'paused')
        release.set()
        until(lambda: not worker.active)
        self.assertEqual(worker.bulk_status(self.book)['completed'], 4)
        self.stop(worker)
        restored = self.worker(lambda b, p, s: calls.append(p) or '续译', count=5)
        self.assertEqual(restored.bulk_status(self.book)['state'], 'paused')
        restored.bulk_action(self.book, 'resume')
        until(lambda: restored.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(sorted(calls), list(range(10)))

    def test_pause_is_durable_and_resume_skips_finished_inflight_paragraph(self):
        started, release = self.gate()
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            started.set()
            release.wait(3)
            return f'译文{pid}'
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        self.assertTrue(started.wait(2))
        paused = worker.bulk_action(self.book, 'pause')
        self.assertEqual(paused['state'], 'paused')
        self.assertEqual(paused['inFlight'], 1)
        release.set()
        until(lambda: not worker.active)
        self.stop(worker)
        restored = self.worker(lambda b, p, s: calls.append(p) or f'译文{p}')
        self.assertEqual(restored.bulk_status(self.book)['state'], 'paused')
        self.assertEqual(restored.bulk_status(self.book)['completed'], 1)
        time.sleep(.05)
        self.assertEqual(calls, [0])
        restored.bulk_action(self.book, 'resume')
        until(lambda: restored.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(calls, list(range(10)))

    def test_running_job_resumes_after_restart_with_new_database_connection(self):
        started, release = self.gate()
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            started.set()
            release.wait(3)
            return '已保存'
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        self.assertTrue(started.wait(2))
        worker.close()
        release.set()
        self.stop(worker)
        self.assertEqual(self.store.bulk_jobs()[0]['state'], 'running')
        self.store.db.close()
        self.store = Store(self.temp.name)
        restored = self.worker(lambda b, p, s: calls.append(p) or '续译')
        until(lambda: restored.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(calls, list(range(10)))

    def test_reading_has_priority_and_overlapping_work_runs_once(self):
        started, release = self.gate()
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return '译文'
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        self.assertTrue(started.wait(2))
        worker.window(self.book, 8, selected=8)
        release.set()
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(calls[:2], [0, 8])
        self.assertEqual(sorted(calls), list(range(10)))

    def test_failed_paragraphs_are_not_completed_and_can_be_retried_alone(self):
        failing, calls = [True], []
        def fake(book, pid, settings):
            calls.append(pid)
            if pid == 3 and failing[0]:
                raise RuntimeError('模型没有返回有效译文，请重试')
            return '译文'
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] == 'failed')
        status = worker.bulk_status(self.book)
        self.assertEqual((status['completed'], status['failed'], status['percent']), (9, 1, 90))
        self.assertIsNone(self.store.translation(self.book['id'], 3, profile(self.store.settings())))
        self.stop(worker)
        restored = self.worker(fake)
        self.assertEqual(restored.bulk_status(self.book)['failed'], 1)
        failing[0] = False
        restored.bulk_action(self.book, 'retry')
        until(lambda: restored.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(calls.count(3), 3)
        self.assertEqual(restored.bulk_status(self.book)['failed'], 0)
        self.assertEqual(len(calls), 12)

    def test_offline_model_automatically_pauses_and_continue_recovers(self):
        offline, calls = [True], []
        def fake(book, pid, settings):
            calls.append(pid)
            if offline[0]:
                raise RuntimeError('无法连接模型')
            return '译文'
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] == 'paused')
        self.assertEqual(calls, [0, 0])
        self.assertEqual(worker.bulk_status(self.book)['completed'], 0)
        self.assertEqual(worker.bulk_status(self.book)['failed'], 1)
        offline[0] = False
        worker.bulk_action(self.book, 'resume')
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(self.store.bulk_failures(self.book['id'], profile(self.store.settings())), {})

    def test_repeated_invalid_output_is_bounded_by_automatic_pause(self):
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            raise RuntimeError('模型没有返回有效译文，请重试')
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] == 'paused')
        self.assertEqual(calls, [0, 0, 1, 1, 2, 2])
        self.assertEqual(worker.bulk_status(self.book)['failed'], 3)
        self.assertEqual(worker.bulk_status(self.book)['completed'], 0)

    def test_model_change_pauses_old_job_and_never_mixes_saved_translations(self):
        started, release = self.gate()
        old_settings = self.store.settings()
        old_profile = profile(old_settings)
        calls = []
        def fake(book, pid, settings):
            calls.append((pid, settings['model']))
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return settings['model']
        worker = self.worker(fake)
        worker.bulk_action(self.book, 'start')
        self.assertTrue(started.wait(2))
        self.store.save_settings(old_settings | {'model': 'another-model'})
        worker.settings_changed()
        self.assertEqual(worker.bulk_status(self.book)['state'], 'idle')
        release.set()
        until(lambda: not worker.active)
        self.assertEqual(worker.bulk[(self.book['id'], old_profile)]['state'], 'paused')
        self.assertEqual(len(self.store.cached_paragraphs(self.book['id'], old_profile)), 1)
        worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(len([x for x in calls if x[1] == 'another-model']), 10)
        self.store.save_settings(old_settings)
        worker.settings_changed()
        self.assertEqual(worker.bulk_status(self.book)['state'], 'paused')
        self.assertEqual(worker.bulk_status(self.book)['completed'], 1)
        worker.bulk_action(self.book, 'resume')
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        self.assertEqual(len(calls), 20)

    def test_http_progress_actions_and_validation_do_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            started, release = self.gate()
            def fake(book, pid, settings):
                started.set()
                release.wait(3)
                return '译文'
            app = make_server(port=0, data_dir=directory, translate_fn=fake)
            book = app.store.add(queue_book())
            app.store.save_settings(app.store.settings() | {'apiKey': 'private-test-key'})
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{app.server_port}/api/books/{book["id"]}/pretranslate'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(action=None):
                req = urllib.request.Request(url, data=json.dumps({'action': action}).encode() if action is not None else None,
                                             headers={'Content-Type': 'application/json'})
                with opener.open(req) as response:
                    return json.load(response)
            try:
                self.assertEqual(request()['state'], 'idle')
                for invalid in ('unknown', [], 'pause'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        request(invalid)
                    self.assertEqual(caught.exception.code, 400)
                self.assertEqual(request('start')['state'], 'running')
                self.assertTrue(started.wait(2))
                self.assertEqual(request('start')['state'], 'running')
                self.assertEqual(request('pause')['state'], 'paused')
                release.set()
                until(lambda: not app.translator.active)
                status = request()
                self.assertEqual(status['state'], 'paused')
                self.assertNotIn('private-test-key', json.dumps(status))
                with opener.open(url.replace('/pretranslate', '/status?page=0')) as response:
                    self.assertEqual(json.load(response)['bookTranslation']['completed'], status['completed'])
                request('resume')
                until(lambda: request()['state'] == 'completed')
            finally:
                app.translator.close()
                release.set()
                app.shutdown()
                app.server_close()
                for worker in app.translator.threads:
                    worker.join(3)
                app.store.db.close()


if __name__ == '__main__':
    unittest.main()
