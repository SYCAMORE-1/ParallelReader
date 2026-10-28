import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from unittest.mock import patch
from reader.epub import parse_epub
from reader.export import BookExporter
from reader.image_translation import ImageTranslator
from reader.language import LanguageTools, word_settings
from reader.machine_translation import PlainText, protect_text
from reader.reading_sources import selection
from reader.storage import Store
from reader.translation import Translator, profile, translate
from server import make_server, validate_settings
from test_illustrations import illustrated_epub
from test_reader import queue_book, until


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(self.temp.name)
        self.base = self.store.settings() | {'baseUrl': 'https://book.example.invalid/v1', 'model': 'book-model', 'apiKey': 'book-test-key', 'reviewEnabled': False}

    def tearDown(self): self.store.db.close(); self.temp.cleanup()

    def test_only_two_services_and_no_cloud_credentials(self):
        for provider in ['llm', 'microsoft']:
            value = validate_settings({'translationProvider': provider}, self.base)
            self.assertEqual(value['translationProvider'], provider)
            for key in ['azureEndpoint', 'azureApiKey', 'azureRegion', 'hasAzureApiKey']: self.assertNotIn(key, value)
        with self.assertRaises(ValueError): validate_settings({'translationProvider': 'azure'}, self.base)

    def test_retired_azure_settings_migrate_without_deleting_saved_text(self):
        old = self.base | {'translationProvider': 'azure', 'azureEndpoint': 'https://retired.example.invalid', 'azureApiKey': 'retired-test-key', 'azureRegion': 'test'}
        with self.store.db: self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('model',?)", (json.dumps(old),))
        book = self.store.add(queue_book()); self.store.cache(book['id'], 0, 'legacy-cloud-profile', '旧版译文。', {'provider': 'azure'})
        value = self.store.settings()
        self.assertEqual(value['translationProvider'], 'microsoft'); self.assertNotIn('azureApiKey', value)
        self.assertNotIn('azureApiKey', self.store.db.execute("SELECT value FROM settings WHERE key='model'").fetchone()[0])
        self.assertEqual(self.store.translation(book['id'], 0, 'legacy-cloud-profile'), '旧版译文。')
        self.assertEqual(selection(self.store, book, value)['sources'][0]['model'], 'Azure Translate')

    def test_legacy_inherited_word_model_is_snapshotted_once(self):
        old = self.base | {'wordProvider': 'llm', 'wordBaseUrl': '', 'wordModel': ''}
        with self.store.db: self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('model',?)", (json.dumps(old),))
        migrated = self.store.settings()
        self.assertEqual(word_settings(migrated)['model'], 'book-model')
        self.store.save_settings(migrated | {'baseUrl': 'https://new-book.example.invalid/v1', 'model': 'new-book-model', 'apiKey': 'new-book-key'})
        word = word_settings(self.store.settings())
        self.assertEqual((word['baseUrl'], word['model'], word['apiKey']), (old['baseUrl'], old['model'], old['apiKey']))

    def test_word_and_book_services_are_independent_in_all_four_combinations(self):
        word_values = {'wordBaseUrl': 'https://words.example.invalid/v1', 'wordModel': 'small-word-model', 'wordApiKey': 'word-test-key'}
        for book_provider in ['llm', 'microsoft']:
            for word_provider in ['llm', 'microsoft']:
                value = validate_settings({'translationProvider': book_provider, 'wordProvider': word_provider, **word_values}, self.base)
                self.assertEqual(value['wordProvider'], word_provider)
                self.assertEqual(value['translationProvider'], book_provider)
                self.assertEqual(word_settings(value)['model'], 'small-word-model')
                self.assertEqual(profile(value), profile(value | {'wordProvider': 'llm' if word_provider == 'microsoft' else 'microsoft', 'wordModel': 'changed'}))
        with self.assertRaisesRegex(ValueError, '单独'): validate_settings({'wordProvider': 'llm'}, self.base)

    def test_azure_book_can_use_word_llm_and_llm_book_can_use_azure_words(self):
        book = self.store.add(queue_book()); book['paragraphs'][0]['text'] = 'The traveler waited.'
        answer = {'lemma': 'traveler', 'partOfSpeech': 'n.', 'basicMeaning': '旅行者', 'contextMeaning': '这里指等候的旅人。', 'note': ''}
        calls = []
        tool = LanguageTools(self.store, request_fn=lambda settings, *args: calls.append(settings) or answer)
        value = self.base | {'translationProvider': 'microsoft', 'wordProvider': 'llm', 'wordBaseUrl': 'https://words.example.invalid/v1', 'wordModel': 'word-only', 'wordApiKey': 'word-only-key'}
        self.assertEqual(tool.explain(book, 0, 'traveler', value)['provider'], 'llm')
        self.assertEqual(calls[0]['model'], 'word-only'); self.assertEqual(calls[0]['apiKey'], 'word-only-key')
        with patch('reader.microsoft_dictionary.explain', return_value=answer | {'provider': 'microsoft'}) as azure:
            self.assertEqual(tool.explain(book, 0, 'traveler', self.base | {'wordProvider': 'microsoft'})['provider'], 'microsoft')
            azure.assert_called_once()
        self.assertEqual(len(calls), 1)

    def test_machine_sentence_slots_and_protected_markup(self):
        book = queue_book(); book['paragraphs'][0]['text'] = 'Alex arrived. The door opened.'
        value = self.base | {'translationProvider': 'microsoft'}
        with patch('reader.microsoft_translation.CLIENT.translate', return_value=['<p>Alex来了。</p>', '<p>门开了。</p>']), patch('reader.translation.request_json', side_effect=AssertionError('No LLM expected')):
            result = translate(book, 0, value)
        self.assertEqual(result['text'], 'Alex来了。门开了。'); self.assertEqual(len(result['alignment']), 2)
        protected = protect_text('Alex & <tag> [redacted] XXX', ['Alex'])
        self.assertIn('<span class="notranslate">Alex</span>', protected); self.assertIn('&amp; &lt;tag&gt;', protected)
        parser = PlainText(); parser.feed(protected); self.assertEqual(''.join(parser.parts), 'Alex & <tag> [redacted] XXX')

    def test_azure_whole_book_ignores_images_and_exports_originals(self):
        self.store.save_settings(self.base | {'translationProvider': 'microsoft'})
        data = illustrated_epub(); book = self.store.add(parse_epub(data), source_bytes=data)
        forbidden = lambda *args: self.fail('Azure Translate must not call image models')
        images = ImageTranslator(self.store, forbidden, forbidden); self.store.image_service = images; self.store.image_progress = images.book_jobs.library
        worker = exporter = None
        try:
            with patch('reader.microsoft_translation.CLIENT.translate', side_effect=lambda settings, texts: ['<p>测试译文。</p>'] * len(texts)):
                worker = Translator(self.store, workers=2, image_translator=images)
                worker.bulk_action(book, 'start'); until(lambda: worker.bulk_status(book)['state'] == 'completed')
            task = worker.bulk_status(book); self.assertEqual(task['percent'], 100); self.assertEqual(task['model'], 'Azure Translate')
            self.assertEqual(task['total'], len(book['paragraphs'])); self.assertEqual(images.book_jobs.assets(book), [])
            exporter = BookExporter(self.store)
            with zipfile.ZipFile(exporter.export(book, self.store.settings())) as archive:
                self.assertTrue(any('/images/' in name for name in archive.namelist()))
                self.assertFalse(any('-translated.png' in name for name in archive.namelist()))
        finally:
            if worker:
                worker.close()
                for thread in worker.threads: thread.join(3)
            if exporter: exporter.close(); exporter.thread.join(3)
            images.close(); images.executor.shutdown(wait=True)

    def test_http_settings_and_connection_test_have_no_azure_key_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory, sample=Path(directory) / 'missing.epub')
            threading.Thread(target=app.serve_forever, daemon=True).start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = f'http://127.0.0.1:{app.server_port}'
            def api(path, value=None):
                request = urllib.request.Request(url + path, data=None if value is None else json.dumps(value).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(request) as response: return json.load(response)
            try:
                api('/api/settings', {'translationProvider': 'microsoft'})
                saved = api('/api/settings')
                self.assertFalse(any(key.startswith('azure') or key == 'hasAzureApiKey' for key in saved))
                with patch('reader.microsoft_translation.CLIENT.translate', return_value=['你好。']) as azure:
                    self.assertEqual(api('/api/model/test', {})['provider'], 'microsoft'); azure.assert_called_once()
            finally:
                app.translator.close(); app.shutdown(); app.server_close()
                for thread in app.translator.threads: thread.join(3)
                app.store.db.close()
