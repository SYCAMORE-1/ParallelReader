import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from reader.reproof import Reproofreader, review_existing
from reader.storage import Store
from reader.translation import Translator, profile
from reader.translation_rules import finish_translation, translation_plan
from server import make_server
from test_reader import queue_book, until


class ReproofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = queue_book()
        self.book['chapters'] = [{'id': i, 'title': f'Chapter {i + 1}', 'startPage': i * 5, 'endPage': i * 5 + 4} for i in range(2)]
        self.book['paragraphs'][3]['text'] = '😀 Morgan waited. He left at once. The door closed.'
        for p in self.book['paragraphs']:
            p['chapter'] = p['id'] // 5
        for page in self.book['pages']:
            page['chapter'] = page['id'] // 5
        self.store.add(self.book)
        self.store.progress(self.book['id'], 3)
        self.fingerprint = profile(self.store.settings())
        for p in self.book['paragraphs']:
            plan = translation_plan(p['text'], ['Morgan'])
            values = ['__PERSON_000__在等。', '他立刻走了。', '门关上了。'] if p['id'] == 3 else ['原译文。']
            detail = finish_translation(plan, values)
            self.store.cache(self.book['id'], p['id'], self.fingerprint, detail['text'], detail)
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            for thread in worker.threads:
                thread.join(3)
        self.store.db.close()
        self.temp.cleanup()

    def worker(self, fn):
        worker = Translator(self.store, workers=1, reproof_fn=fn)
        self.workers.append(worker)
        return worker

    def sentence_request(self):
        detail = self.store.translation_detail(self.book['id'], 3, self.fingerprint)
        start = len(detail['sourceSentences'][0])
        return {'scope': 'sentence', 'paragraph': 3, 'start': start, 'end': start + len(detail['sourceSentences'][1])}

    def test_reproof_restores_redacted_source_without_name_lookup_or_model(self):
        source = 'xxx xxxx, xxxxxxxx'
        self.book['paragraphs'][3]['text'] = source
        snapshot = {'text': '第63章', 'detail': None}
        with patch('reader.translation.request_json') as request, patch('reader.reproof.person_names') as names:
            result = review_existing(self.book, 3, self.store.settings(), snapshot, None, self.store)
            self.assertEqual(result['text'], source)
            self.assertEqual(result['sourceSentences'], result['targetSentences'])
            request.assert_not_called()
            names.assert_not_called()

    def test_selected_sentence_changes_only_its_target_and_uses_codepoint_boundaries(self):
        snapshot = self.store.translation_snapshot(self.book['id'], 3, self.fingerprint)
        selection = self.sentence_request()
        def revised(book, pid, settings, plan, draft):
            self.assertEqual(''.join(plan['source']), 'He left at once. ')
            self.assertEqual(draft['text'], snapshot['detail']['targetSentences'][1])
            return finish_translation(plan, ['他马上就走了。'])
        with patch('reader.reproof.person_names', return_value=['Morgan']), patch('reader.translation.proofread', side_effect=revised):
            result = review_existing(self.book, 3, self.store.settings(), snapshot, selection, self.store)
        self.assertEqual(result['targetSentences'][0], snapshot['detail']['targetSentences'][0])
        self.assertEqual(result['targetSentences'][2], snapshot['detail']['targetSentences'][2])
        self.assertEqual(result['targetSentences'][1], '他马上就走了。')
        self.assertEqual(''.join(result['sourceSentences']), self.book['paragraphs'][3]['text'])
        self.assertEqual(len(result['alignment']), 3)

    def test_chapter_rechecks_cached_paragraphs_and_preserves_other_chapter_and_position(self):
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            return {**snapshot['detail'], 'text': '新校对。', 'targetSentences': ['新校对。']}
        worker = self.worker(revise)
        job = worker.reproofread(self.book, {'scope': 'chapter', 'chapter': 1})
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'completed')
        self.assertEqual(calls, [5, 6, 7, 8, 9])
        self.assertEqual(self.store.book(self.book['id'])['currentPage'], 3)
        self.assertEqual(self.store.translation(self.book['id'], 0, self.fingerprint), '原译文。')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM translation_revisions').fetchone()[0], 5)

    def test_failure_keeps_original_and_retry_skips_completed_paragraphs(self):
        failing = [True]
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            if pid == 7 and failing[0]:
                raise RuntimeError('校对接口暂时不可用')
            return {**snapshot['detail'], 'text': '已润色。', 'targetSentences': ['已润色。']}
        worker = self.worker(revise)
        request = {'scope': 'chapter', 'chapter': 1}
        job = worker.reproofread(self.book, request)
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'failed')
        self.assertEqual(self.store.translation(self.book['id'], 7, self.fingerprint), '原译文。')
        self.assertEqual(worker.bulk_status(self.book)['failed'], 0)
        failing[0] = False
        self.assertEqual(worker.reproofread(self.book, request)['id'], job['id'])
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'completed')
        self.assertEqual(calls.count(6), 1)
        self.assertEqual(calls.count(7), 3)

    def test_queued_job_survives_restart_without_storing_keys(self):
        self.store.save_settings(self.store.settings() | {'apiKey': 'primary-secret', 'reviewApiKey': 'review-secret'})
        queue = Reproofreader(self.store)
        job = queue.request(self.book, {'scope': 'chapter', 'chapter': 1})
        duplicate = queue.request(self.book, {'scope': 'chapter', 'chapter': 1})
        self.assertEqual(job['id'], duplicate['id'])
        serialized = self.store.db.execute('SELECT value FROM reproof_jobs').fetchone()[0]
        self.assertNotIn('secret', serialized)
        self.store.db.close()
        self.store = Store(self.temp.name)
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            return snapshot['detail']
        worker = self.worker(revise)
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'completed')
        self.assertEqual(calls, [5, 6, 7, 8, 9])

    def test_compare_and_swap_rebases_without_overwriting_newer_neighbour(self):
        queue = Reproofreader(self.store)
        job = queue.request(self.book, self.sentence_request())
        key, book, settings, _ = queue.take(set())
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(snapshot)
            result = copy.deepcopy(snapshot['detail'])
            if len(calls) == 1:
                newer = copy.deepcopy(snapshot['detail'])
                newer['targetSentences'][2] = '另一处刚保存的修改。'
                newer['text'] = ''.join(newer['targetSentences'])
                self.store.cache(book['id'], pid, self.fingerprint, newer['text'], newer)
            result['targetSentences'][1] = '选句新译。'
            result['text'] = ''.join(result['targetSentences'])
            return result
        queue.review_fn = revise
        queue.execute(key, book, settings)
        queue.finish(key, None)
        detail = self.store.translation_detail(book['id'], 3, self.fingerprint)
        self.assertEqual(len(calls), 2)
        self.assertEqual(detail['targetSentences'][2], '另一处刚保存的修改。')
        self.assertEqual(detail['targetSentences'][1], '选句新译。')
        self.assertEqual(queue.jobs[job['id']]['state'], 'completed')

    def test_invalid_and_partial_sentence_ranges_are_rejected(self):
        queue = Reproofreader(self.store)
        for value in [{'scope': 'chapter', 'chapter': True}, {'scope': 'chapter', 'chapter': 99}, self.sentence_request() | {'start': 1}, self.sentence_request() | {'end': 9999}]:
            with self.assertRaises(ValueError):
                queue.request(self.book, value)

    def test_offline_reviewer_pauses_chapter_without_requesting_every_paragraph(self):
        offline = [True]
        calls = []
        def revise(book, pid, settings, snapshot, selection):
            calls.append(pid)
            if offline[0]:
                raise RuntimeError('无法连接模型')
            return snapshot['detail']
        worker = self.worker(revise)
        request = {'scope': 'chapter', 'chapter': 1}
        job = worker.reproofread(self.book, request)
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'paused')
        self.assertEqual(calls, [5, 5])
        self.assertEqual(self.store.translation(self.book['id'], 5, self.fingerprint), '原译文。')
        offline[0] = False
        worker.reproofread(self.book, request)
        until(lambda: worker.reproof.jobs[job['id']]['state'] == 'completed')
        self.assertEqual(calls[2:], [5, 6, 7, 8, 9])

    def test_chapter_does_not_consume_reserved_reading_slot(self):
        queue = Reproofreader(self.store)
        queue.request(self.book, {'scope': 'chapter', 'chapter': 1})
        self.assertIsNone(queue.take(set(), background_available=False))
        queue.request(self.book, self.sentence_request())
        self.assertIsNotNone(queue.take(set(), background_available=False))

    def test_async_http_keeps_old_text_visible_until_saved(self):
        started, release = threading.Event(), threading.Event()
        def revise(book, pid, settings, snapshot, selection):
            started.set(); release.wait(5)
            detail = copy.deepcopy(snapshot['detail']); detail['targetSentences'][1] = '已原地更新。'
            detail['text'] = ''.join(detail['targetSentences'])
            return detail
        app = make_server(port=0, data_dir=self.temp.name, reproof_fn=revise)
        thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{app.server_port}/api/books/{self.book["id"]}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            req = urllib.request.Request(base + '/reproofread', data=json.dumps(self.sentence_request()).encode(), headers={'Content-Type': 'application/json'})
            with opener.open(req) as response:
                self.assertEqual(response.status, 202); job = json.load(response)
            self.assertTrue(started.wait(2))
            with opener.open(base + '/status?page=3') as response:
                data = json.load(response)
            self.assertEqual(data['items']['3']['state'], 'ready')
            self.assertNotIn('已原地更新', data['items']['3']['text'])
            self.assertEqual(data['items']['3']['reproofreading'], 'running')
            release.set()
            until(lambda: app.translator.reproof.jobs[job['id']]['state'] == 'completed')
            self.assertIn('已原地更新', self.store.translation(self.book['id'], 3, self.fingerprint))
        finally:
            release.set(); app.shutdown(); app.translator.close(); app.server_close(); thread.join(2)
            for worker in app.translator.threads:
                worker.join(3)
            app.store.db.close()


if __name__ == '__main__':
    unittest.main()
