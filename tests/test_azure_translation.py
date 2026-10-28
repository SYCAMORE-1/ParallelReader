"""Azure protocol and app integration against a local synthetic service."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from reader import azure_translation as azure
from reader.epub import parse_epub
from reader.export import BookExporter
from reader.image_translation import ImageTranslator, image_profile
from reader.language import LanguageTools
from reader.library_transfer import copy_library
from reader.providers import review_enabled
from reader.storage import Store
from reader.translation import Translator, profile, translate
from server import make_server, validate_settings
from test_illustrations import illustrated_epub
from test_reader import until


class AzureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.requests, self.failure = [], 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                values = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, dict(self.headers), values))
                if owner.failure:
                    self.send_response(owner.failure); self.end_headers()
                    self.wfile.write(b'sensitive upstream diagnostic must not be shown')
                    return
                result = [{'translations': [{'to': 'zh-Hans', 'text': '<p>测试译文。</p>'}]} for _ in values]
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(result).encode())
        self.service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.service.serve_forever, daemon=True); self.thread.start()
        self.settings = self.store.settings() | {'translationProvider': 'azure',
            'azureEndpoint': f'http://127.0.0.1:{self.service.server_port}',
            'azureApiKey': 'synthetic-test-key', 'azureRegion': 'test-region'}
        self.store.save_settings(self.settings)
        data = illustrated_epub(); self.book = self.store.add(parse_epub(data), source_bytes=data)
        self.workers = []; self.images = []; self.exporters = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            for thread in worker.threads: thread.join(3)
        for exporter in self.exporters: exporter.close(); exporter.thread.join(3)
        for images in self.images: images.close(); images.executor.shutdown(wait=True)
        self.service.shutdown(); self.service.server_close(); self.thread.join(3)
        self.store.db.close(); self.temp.cleanup()

    def test_endpoint_global_custom_and_validation(self):
        for endpoint, expected in [('https://azure.example.invalid', '/translate?'),
            ('https://fixture.cognitiveservices.azure.com/', '/translator/text/v3.0/translate?'),
            ('https://azure.example.invalid/translator/text/v3.0/translate', '/translator/text/v3.0/translate?')]:
            url = azure.endpoint_url(endpoint, 'zh-Hans')
            self.assertIn(expected, url); self.assertEqual(url.count('/translate?'), 1)
            self.assertIn('api-version=3.0', url); self.assertIn('textType=html', url)
        for invalid in ['', 'file:///secret', 'https://user:password@example.invalid', 'https://example.invalid?key=secret']:
            with self.assertRaises(ValueError): azure.endpoint_url(invalid, 'zh-Hans')

    def test_settings_support_empty_llm_and_saved_key(self):
        settings = validate_settings(self.settings, self.store.settings())
        self.assertEqual(settings['baseUrl'], ''); self.assertFalse(review_enabled(settings))
        self.assertEqual(validate_settings({'azureApiKey': ''}, settings)['azureApiKey'], 'synthetic-test-key')
        with self.assertRaises(ValueError): validate_settings({'clearAzureApiKey': True}, settings)
        with self.assertRaises(ValueError): validate_settings({'azureRegion': 'bad region'}, settings)
        with self.assertRaises(ValueError): validate_settings({'translationProvider': 'unknown'}, settings)
        self.assertEqual(validate_settings({'azureRegion': ''}, settings)['azureRegion'], '')

    def test_protocol_headers_batch_alignment_and_no_llm(self):
        self.book['paragraphs'][0]['text'] = 'A traveler arrived. The door opened.'
        with patch('reader.translation.request_json', side_effect=AssertionError('LLM must not be called')):
            detail = translate(self.book, 0, self.settings, self.store)
        route, headers, body = self.requests[-1]
        headers = {key.lower(): value for key, value in headers.items()}
        self.assertIn('to=zh-Hans', route); self.assertEqual(len(body), 2)
        self.assertEqual(headers['ocp-apim-subscription-key'], 'synthetic-test-key')
        self.assertEqual(headers['ocp-apim-subscription-region'], 'test-region')
        self.assertEqual(''.join(detail['sourceSentences']), self.book['paragraphs'][0]['text'])
        self.assertEqual(detail['text'], '测试译文。测试译文。')
        self.assertEqual(detail['alignment'], [{'source': [0], 'target': [0]}, {'source': [1], 'target': [1]}])
        self.assertNotIn('<p>', detail['text'])

    def test_literal_names_redactions_and_html_are_protected(self):
        value = azure.protect_text('Alex & <tag> [redacted] XXX xxxx', ['Alex'])
        self.assertIn('<span class="notranslate">Alex</span>', value)
        self.assertIn('<span class="notranslate">[redacted]</span>', value)
        self.assertIn('&amp; &lt;tag&gt;', value)
        self.book['paragraphs'][0]['text'] = '[XXXX]'
        detail = translate(self.book, 0, self.settings, self.store)
        self.assertEqual(detail['text'], '[XXXX]'); self.assertEqual(self.requests, [])

    def test_http_errors_are_actionable_and_do_not_expose_body_or_key(self):
        for code, message in [(400, '请求无效'), (401, '身份验证'), (403, '被拒绝'), (429, '配额'), (503, '503')]:
            self.failure = code
            with self.assertRaises(RuntimeError) as error: azure.request_translations(self.settings, ['hello'])
            self.assertIn(message, str(error.exception))
            self.assertNotIn('synthetic-test-key', str(error.exception))
            self.assertNotIn('sensitive', str(error.exception))

    def test_invalid_responses_and_connection_errors(self):
        for value in [{}, [], [{'translations': None}], [{'translations': []}],
            [{'translations': [{'to': 'ja', 'text': 'wrong language'}]}],
            [{'translations': [{'to': 'zh-Hans', 'text': ''}]}]]:
            response = io.BytesIO(json.dumps(value).encode())
            with patch('urllib.request.urlopen', return_value=response):
                with self.assertRaises(RuntimeError): azure.request_translations(self.settings, ['hello'])
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('private diagnostic')):
            with self.assertRaisesRegex(RuntimeError, '无法连接 Azure'): azure.request_translations(self.settings, ['hello'])

    def test_profiles_separate_providers_and_ignore_key_rotation(self):
        llm = self.settings | {'translationProvider': 'llm'}
        self.assertNotEqual(profile(llm), profile(self.settings))
        self.assertEqual(profile(self.settings), profile(self.settings | {'azureApiKey': 'rotated'}))
        self.assertNotEqual(profile(self.settings), profile(self.settings | {'targetLanguage': '日本語'}))
        self.store.cache(self.book['id'], 0, profile(llm), '旧的大模型译文。')
        self.assertIsNone(self.store.translation(self.book['id'], 0, profile(self.settings)))
        self.assertEqual(self.store.translation(self.book['id'], 0, profile(llm)), '旧的大模型译文。')

    def test_whole_book_text_only_retry_cache_and_export_original_images(self):
        def forbidden(*args): raise AssertionError('Image model must not be called')
        images = ImageTranslator(self.store, forbidden, forbidden); self.images.append(images)
        self.store.image_service = images; self.store.image_progress = images.book_jobs.library
        worker = Translator(self.store, workers=2, image_translator=images); self.workers.append(worker)
        asset = self.book['illustrations'][0]['asset']
        self.assertEqual(images.request(self.book, asset)['state'], 'disabled')
        self.assertEqual(images.book_jobs.assets(self.book), [])
        self.failure = 401; worker.bulk_action(self.book, 'start')
        until(lambda: worker.bulk_status(self.book)['state'] in {'paused', 'failed'})
        self.assertEqual(self.store.cached_paragraphs(self.book['id'], profile(self.settings)), set())
        self.failure = 0; worker.bulk_action(self.book, 'retry')
        until(lambda: worker.bulk_status(self.book)['state'] == 'completed')
        status = worker.bulk_status(self.book)
        self.assertEqual(status['percent'], 100); self.assertEqual(status['total'], len(self.book['paragraphs']))
        self.assertFalse(status['reviewEnabled']); self.assertEqual(status['model'], 'Azure Translator')
        count = len(self.requests); worker.bulk_action(self.book, 'start')
        self.assertEqual(len(self.requests), count)
        exporter = BookExporter(self.store); self.exporters.append(exporter)
        for kind in ['bilingual', 'translation']:
            path = exporter.export(self.book, self.settings, kind)
            with zipfile.ZipFile(path) as archive:
                self.assertFalse(any('-translated.png' in name for name in archive.namelist()))
                self.assertTrue(any('/images/' in name for name in archive.namelist()))
        self.store.save_settings(self.settings | {'translationProvider': 'llm'})
        self.assertTrue(images.book_jobs.assets(self.book))
        self.assertNotEqual(image_profile(self.settings), image_profile(self.store.settings()))

    def test_azure_reproof_and_word_lookup_never_call_llm(self):
        worker = Translator(self.store, workers=1); self.workers.append(worker)
        with patch('reader.translation.request_json', side_effect=AssertionError('LLM must not be called')):
            with self.assertRaisesRegex(ValueError, 'Azure 模式'): worker.reproof.request(self.book, {'scope': 'book'})
            import re
            word = re.search('[A-Za-z]+', self.book['paragraphs'][0]['text'])[0]
            answer = {'lemma': word, 'partOfSpeech': 'n.', 'basicMeaning': '测试', 'contextMeaning': '语境测试', 'provider': 'microsoft'}
            with patch('reader.microsoft_dictionary.explain', return_value=answer) as lookup:
                self.assertEqual(LanguageTools(self.store).explain(self.book, 0, word, self.settings)['provider'], 'microsoft')
                lookup.assert_called_once()

    def test_private_transfer_strips_azure_key_and_keeps_source(self):
        destination = Path(self.temp.name) / 'transfer' / '.data'
        copy_library(self.store.directory, destination)
        restored = Store(destination)
        try: self.assertEqual(restored.settings()['azureApiKey'], '')
        finally: restored.db.close()
        self.assertNotIn(b'synthetic-test-key', (destination / 'reader.sqlite3').read_bytes())
        self.assertEqual(self.store.settings()['azureApiKey'], 'synthetic-test-key')

    def test_http_settings_mask_key_and_test_service(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory, sample=Path(directory) / 'missing.epub')
            threading.Thread(target=app.serve_forever, daemon=True).start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{app.server_port}'
            def call(route, data=None):
                request = urllib.request.Request(base + route, data=None if data is None else json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(request) as response: return json.load(response)
            try:
                self.assertTrue(call('/api/settings', self.settings)['ok'])
                saved = call('/api/settings')
                self.assertEqual(saved['azureApiKey'], ''); self.assertTrue(saved['hasAzureApiKey'])
                self.assertEqual(call('/api/model/test', {'azureApiKey': ''})['provider'], 'azure')
            finally:
                app.translator.close(); app.shutdown(); app.server_close()
                for thread in app.translator.threads: thread.join(3)
                app.store.image_service.close(); app.store.image_service.executor.shutdown(wait=True)
                app.store.db.close()
