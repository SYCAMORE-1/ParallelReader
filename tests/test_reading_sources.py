import json
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path
from reader.storage import Store
from reader.translation import Translator, profile
from reader.reading_sources import choose, selection, progress
from reader.language import LanguageTools
from reader.export import BookExporter
from test_reader import queue_book, until
from server import make_server


class ReadingSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(self.temp.name)
        self.book = self.store.add(queue_book()); self.workers = []
        self.llm = self.store.settings() | {'translationProvider': 'llm', 'baseUrl': 'https://llm.example.invalid/v1', 'model': 'fixture-model', 'reviewEnabled': False}
        self.store.save_settings(self.llm); self.old = profile(self.llm)
        self.microsoft = self.llm | {'translationProvider': 'microsoft'}

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            for thread in worker.threads: thread.join(3)
        self.store.db.close(); self.temp.cleanup()

    def cache(self, pid, fingerprint=None):
        source = self.book['paragraphs'][pid]['text']; target = f'已保存第{pid}段。'
        detail = {'text': target, 'sourceSentences': [source], 'targetSentences': [target],
                  'alignment': [{'source': [0], 'target': [0]}], 'alignmentMethod': 'translation-slots'}
        self.store.cache(self.book['id'], pid, fingerprint or self.old, target, detail)

    def worker(self, fn):
        worker = Translator(self.store, workers=1, translate_fn=fn); self.workers.append(worker); return worker

    def test_no_saved_text_needs_no_question(self):
        value = selection(self.store, self.book, self.microsoft)
        self.assertFalse(value['selectionRequired']); self.assertEqual(value['sources'], [])

    def test_switch_waits_for_choice_and_reports_saved_provider(self):
        self.cache(0); self.store.save_settings(self.microsoft)
        value = selection(self.store, self.book, self.microsoft)
        self.assertTrue(value['selectionRequired'])
        self.assertEqual(value['sources'][0]['provider'], 'llm'); self.assertEqual(value['sources'][0]['completed'], 1)
        worker = self.worker(lambda *args: self.fail('Must wait before translating'))
        worker.window(self.book, 0)
        self.assertEqual(worker.desired, {}); self.assertTrue(worker.status(self.book, 0)['needsTranslationChoice'])

    def test_selected_saved_text_is_reused_and_only_missing_paragraph_is_generated(self):
        for pid in range(9): self.cache(pid)
        self.store.save_settings(self.microsoft); current = profile(self.microsoft)
        choose(self.store, self.book, self.microsoft, self.old, current)
        calls = []
        worker = self.worker(lambda book, pid, settings: calls.append(pid) or '新服务补齐。')
        worker.window(self.book, 8)
        until(lambda: self.store.translation(self.book['id'], 9, current))
        self.assertEqual(calls, [9])
        value = worker.status(self.book, 8)
        self.assertEqual(value['readingProfile'], self.old)
        self.assertEqual(value['items'][8]['text'], '已保存第8段。')
        self.assertEqual(value['items'][8]['translationProfile'], self.old)
        self.assertEqual(value['items'][9]['translationProfile'], current)
        self.assertIsNone(self.store.translation(self.book['id'], 8, current))
        self.assertEqual(progress(self.store, self.book, self.microsoft)['percent'], 100)
        self.assertEqual(worker.bulk_status(self.book)['completed'], 1, 'New background work keeps its own provenance')
        mapped = LanguageTools(self.store).view(self.book, 8, value['items'][8]['text'], self.microsoft, value['items'][8]['translationProfile'])
        self.assertEqual(mapped['alignment'], [{'source': [0], 'target': [0]}])

    def test_declining_saved_text_uses_current_service_and_preserves_old_text(self):
        self.cache(0); self.store.save_settings(self.microsoft); current = profile(self.microsoft)
        choose(self.store, self.book, self.microsoft, current, current)
        worker = self.worker(lambda *args: '当前服务译文。')
        worker.window(self.book, 0, selected=0, enabled=False)
        until(lambda: self.store.translation(self.book['id'], 0, current))
        self.assertEqual(worker.status(self.book, 0)['items'][0]['text'], '当前服务译文。')
        self.assertEqual(self.store.translation(self.book['id'], 0, self.old), '已保存第0段。')

    def test_current_cache_loads_without_question_but_other_versions_remain_selectable(self):
        self.cache(0); self.store.save_settings(self.microsoft); self.cache(0, profile(self.microsoft))
        value = selection(self.store, self.book, self.microsoft)
        self.assertFalse(value['selectionRequired']); self.assertEqual(len(value['sources']), 2)
        self.assertEqual(value['selectedProfile'], profile(self.microsoft))

    def test_same_llm_initial_translation_can_continue_to_proofreading(self):
        self.cache(0)
        reviewed = self.llm | {'reviewEnabled': True, 'reviewBaseUrl': 'https://review.example.invalid/v1', 'reviewModel': 'test-reviewer'}
        self.store.save_settings(reviewed)
        self.assertFalse(selection(self.store, self.book, reviewed)['selectionRequired'])
        self.assertEqual(selection(self.store, self.book, reviewed)['selectedProfile'], profile(reviewed))

    def test_choice_survives_restart_and_is_per_book_and_configuration(self):
        self.cache(0); self.store.save_settings(self.microsoft)
        choose(self.store, self.book, self.microsoft, self.old, profile(self.microsoft))
        restored = Store(self.temp.name)
        try: self.assertEqual(selection(restored, self.book, self.microsoft)['selectedProfile'], self.old)
        finally: restored.db.close()
        other = {**self.book, 'id': 'different-book'}; self.store.add(other)
        self.assertFalse(selection(self.store, other, self.microsoft)['selectionRequired'])
        self.assertEqual(selection(self.store, self.book, self.llm)['selectedProfile'], self.old)
        with self.assertRaisesRegex(ValueError, '配置已变化'): choose(self.store, self.book, self.microsoft, self.old, self.old)
        with self.assertRaisesRegex(ValueError, '不存在'): choose(self.store, self.book, self.microsoft, 'missing', profile(self.microsoft))

    def test_legacy_azure_metadata_is_inferred_without_copying_or_leaking_settings(self):
        self.store.cache(self.book['id'], 0, 'legacy-azure', '旧译文。', {'provider': 'azure'})
        state = selection(self.store, self.book, self.llm)
        source = state['sources'][0]
        self.assertEqual(source['provider'], 'azure'); self.assertIsNone(source['targetLanguage'])
        self.assertNotIn('baseUrl', json.dumps(state)); self.assertNotIn('ApiKey', json.dumps(state))

    def test_export_uses_selected_saved_version_and_keeps_generation_profile_separate(self):
        for pid in range(10): self.cache(pid)
        self.store.save_settings(self.microsoft)
        choose(self.store, self.book, self.microsoft, self.old, profile(self.microsoft))
        exporter = BookExporter(self.store)
        try:
            path = exporter.export(self.book, self.microsoft, reading=True)
            with zipfile.ZipFile(path) as archive:
                content = ''.join(archive.read(name).decode() for name in archive.namelist() if name.endswith('.xhtml'))
                self.assertIn('已保存第0段。', content)
            with self.assertRaisesRegex(ValueError, '尚未完成'): exporter.export(self.book, self.microsoft)
        finally: exporter.close(); exporter.thread.join(3)

    def test_http_inventory_choice_status_and_sentence_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory, sample=Path(directory) / 'missing.epub', translate_fn=lambda *args: self.fail('No new requests expected'))
            threading.Thread(target=app.serve_forever, daemon=True).start()
            book = app.store.add(queue_book()); app.store.save_settings(self.llm)
            source = book['paragraphs'][0]['text']
            detail = {'text': '本地译文。', 'sourceSentences': [source], 'targetSentences': ['本地译文。'], 'alignment': [{'source': [0], 'target': [0]}], 'alignmentMethod': 'translation-slots'}
            app.store.cache(book['id'], 0, self.old, detail['text'], detail)
            app.store.save_settings(self.microsoft); app.translator.settings_changed()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{app.server_port}/api/books/{book["id"]}'
            def call(path, value=None):
                request = urllib.request.Request(base + path, data=None if value is None else json.dumps(value).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(request) as response: return json.load(response)
            try:
                self.assertTrue(call('/translation-sources')['selectionRequired'])
                self.assertTrue(call('/window', {'page': 0})['needsTranslationChoice'])
                call('/translation-sources', {'sourceProfile': self.old, 'configurationProfile': profile(self.microsoft)})
                status = call('/status?page=0')
                self.assertEqual(status['items']['0']['text'], '本地译文。')
                self.assertEqual(status['items']['0']['alignment'], detail['alignment'])
                self.assertEqual(call('/align', {'paragraph': 0})['alignment'], detail['alignment'])
            finally:
                app.translator.close(); app.shutdown(); app.server_close()
                for thread in app.translator.threads: thread.join(3)
                app.store.db.close()
