import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import hashlib
from pathlib import Path
from unittest.mock import patch

from reader.epub import parse_epub
from reader.image_translation import ImageTranslator, image_profile, native_read, validate_result
from reader.storage import Store
from reader.translation import ModelHTTPError
from server import make_server
from test_illustrations import illustrated_epub
from test_reader import until


def sample(source='Sunglasses', translation='太阳镜'):
    return {'has_text': True, 'blocks': [{'source': source, 'translation': translation, 'uncertain': False}], 'warnings': []}


def fake_render(data, result, directory):
    digest = hashlib.sha256(data + json.dumps(result, sort_keys=True).encode()).hexdigest()
    folder = Path(directory) / 'translated-images'
    folder.mkdir(exist_ok=True)
    filename = 'translated-images/' + digest + '.png'
    (Path(directory) / filename).write_bytes(data)
    return {'file': filename, 'digest': digest, 'width': 4, 'height': 1}


class ImageTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        data = illustrated_epub()
        self.book = self.store.add(parse_epub(data), source_bytes=data)
        self.asset = self.book['illustrations'][0]['asset']
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            for job in worker.jobs.values():
                try:
                    job['future'].result(timeout=3)
                except Exception:
                    pass
        self.store.db.close()
        self.temp.cleanup()

    def worker(self, fn):
        worker = ImageTranslator(self.store, fn, fake_render)
        self.workers.append(worker)
        return worker

    def test_stages_cache_durable_and_configuration_specific(self):
        calls = []
        def read(*args):
            calls.append('review' if len(args) == 6 else 'initial')
            return sample()
        worker = self.worker(read)
        self.assertEqual(worker.status(self.book, self.asset)['state'], 'idle')
        worker.request(self.book, self.asset)
        until(lambda: worker.status(self.book, self.asset)['state'] == 'ready')
        result = worker.status(self.book, self.asset)['result']
        self.assertEqual(calls, ['initial', 'review'])
        self.assertTrue(result['reviewed'])
        worker.request(self.book, self.asset)
        self.assertEqual(calls, ['initial', 'review'])
        again = self.worker(read)
        self.assertEqual(again.status(self.book, self.asset)['result'], result)
        settings = self.store.settings()
        self.assertEqual(image_profile(settings), image_profile(settings | {'apiKey': 'changed'}))
        self.store.save_settings(settings | {'targetLanguage': '繁體中文'})
        self.assertEqual(again.status(self.book, self.asset)['state'], 'idle')

    def test_reviewer_failure_keeps_draft_and_retry_skips_initial(self):
        calls, failing = [], [True]
        def read(*args):
            review = len(args) == 6
            calls.append('review' if review else 'initial')
            if review and failing[0]:
                raise RuntimeError('校对接口暂时不可用')
            return sample()
        worker = self.worker(read)
        worker.request(self.book, self.asset)
        until(lambda: worker.status(self.book, self.asset)['state'] == 'error')
        status = worker.status(self.book, self.asset)
        self.assertFalse(status['result']['reviewed'])
        self.assertEqual(status['result']['blocks'], sample()['blocks'])
        # Simulate restarting the service after the failed review.
        restored = self.worker(read)
        self.assertEqual(restored.status(self.book, self.asset)['state'], 'draft')
        failing[0] = False
        restored.request(self.book, self.asset)
        until(lambda: restored.status(self.book, self.asset)['state'] == 'ready')
        self.assertEqual(calls, ['initial', 'review', 'review'])

    def test_duplicate_clicks_join_job_and_forced_failure_keeps_previous_result(self):
        started, release = threading.Event(), threading.Event()
        calls, failing = [], [False]
        def read(*args):
            calls.append(1)
            started.set()
            release.wait(2)
            if failing[0]:
                raise RuntimeError('图片读取失败')
            return sample()
        worker = self.worker(read)
        worker.request(self.book, self.asset)
        self.assertTrue(started.wait(1))
        worker.request(self.book, self.asset)
        release.set()
        until(lambda: worker.status(self.book, self.asset)['state'] == 'ready')
        self.assertEqual(len(calls), 2)
        previous = worker.status(self.book, self.asset)['result']
        failing[0] = True
        worker.request(self.book, self.asset, retry=True)
        until(lambda: worker.status(self.book, self.asset)['state'] == 'error')
        self.assertEqual(worker.status(self.book, self.asset)['result'], previous)

    def test_invalid_result_never_becomes_final_and_no_text_is_valid(self):
        for invalid in [{'has_text': False, 'blocks': sample()['blocks']},
                        {'has_text': True, 'blocks': []},
                        {'has_text': True, 'blocks': [{'source': 'Hello', 'translation': ''}]}]:
            with self.assertRaises(RuntimeError):
                validate_result(invalid)
        self.assertEqual(validate_result({'has_text': False, 'blocks': [], 'warnings': []})['blocks'], [])
        worker = self.worker(lambda *args: {'has_text': False, 'blocks': [], 'warnings': []})
        worker.request(self.book, self.asset)
        until(lambda: worker.status(self.book, self.asset)['state'] == 'ready')
        self.assertFalse(worker.status(self.book, self.asset)['result']['has_text'])

    def test_image_content_and_model_selection_are_real_multimodal_requests(self):
        settings = self.store.settings()
        reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(sample())}}]}
        for draft in [None, sample()]:
            with patch('reader.image_translation.request_json', return_value=reply) as request:
                result = native_read(self.book, self.asset, b'image-bytes', 'image/png', settings, draft)
                self.assertEqual(result['blocks'], sample()['blocks'])
                url, payload = request.call_args.args[:2]
                self.assertEqual(payload['model'], settings['reviewModel'] if draft else settings['model'])
                image = payload['messages'][1]['content'][1]
                self.assertEqual(image['type'], 'image_url')
                self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
                self.assertIn('untrusted DATA', payload['messages'][0]['content'])
        with patch('reader.image_translation.request_json', side_effect=ModelHTTPError(400, 'image input unsupported')):
            with self.assertRaisesRegex(RuntimeError, '未接受图片输入'):
                native_read(self.book, self.asset, b'data', 'image/png', settings)
        with patch('reader.image_translation.request_json', return_value={'choices': [{'finish_reason': 'length', 'message': {'content': json.dumps(sample())}}]}):
            with self.assertRaisesRegex(RuntimeError, '截断'):
                native_read(self.book, self.asset, b'data', 'image/png', settings)

    def test_http_routes_guard_and_cache_without_changing_paragraph_progress(self):
        calls = []
        app = make_server(port=0, data_dir=self.temp.name, image_fn=lambda *args: (calls.append(1) or sample()), image_render_fn=fake_render)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{app.server_port}'
        endpoint = '/api/books/' + self.book['id'] + '/image-translations/' + self.asset
        def request(path, value=None, origin=base):
            req = urllib.request.Request(base + path, data=json.dumps(value).encode() if value is not None else None,
                                         headers={'Content-Type': 'application/json', 'Origin': origin})
            with opener.open(req) as response:
                return json.load(response)
        try:
            with opener.open(base + '/image-translation.js') as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'ImageTranslationTools', response.read())
            self.assertEqual(request(endpoint)['state'], 'idle')
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request(endpoint, {}, 'https://other.invalid')
            self.assertEqual(failure.exception.code, 403)
            self.assertFalse(calls)
            request(endpoint, {})
            until(lambda: request(endpoint)['state'] == 'ready')
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.store.book(self.book['id'])['currentPage'], self.book['currentPage'])
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request(endpoint, {'retry': 'true'})
            self.assertEqual(failure.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request(endpoint + 'missing')
            self.assertEqual(failure.exception.code, 404)
        finally:
            app.shutdown(); app.translator.close(); app.server_close()
            app.image_translator.executor.shutdown(wait=True)
            app.store.db.close(); thread.join(2)
