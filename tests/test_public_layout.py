import importlib.util
import ipaddress
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reader.epub import parse_epub
from reader.language import word_settings
from reader.library_transfer import copy_library
from reader.storage import Store
from reader.translation import profile, request_json
from server import validate_settings
from test_illustrations import illustrated_epub
from tools.audit_public import audit_public


class PublicLayoutTests(unittest.TestCase):
    def test_private_package_outputs_are_excluded_from_public_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'private-transfer'; private.mkdir()
            (private / 'original.epub').write_bytes(b'private-data')
            (private / 'library-manifest.json').write_text('{}')
            self.assertTrue(audit_public(root)['ok'])
    def test_new_library_has_no_endpoint_or_model_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                settings = store.settings()
                for key in ['baseUrl', 'model', 'reviewBaseUrl', 'reviewModel', 'wordBaseUrl', 'wordModel', 'apiKey', 'reviewApiKey', 'wordApiKey']:
                    self.assertEqual(settings[key], '')
                self.assertEqual(store.library(), [])
            finally: store.db.close()
        with self.assertRaisesRegex(RuntimeError, '配置模型'):
            request_json('/chat/completions', {})

    def test_copying_only_data_restores_books_images_translations_and_bookmark(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Store(Path(directory) / 'original' / '.data')
            try:
                data = illustrated_epub()
                book = source.add(parse_epub(data), source_bytes=data)
                settings = source.settings() | {'baseUrl': 'https://translation.example.invalid/v1', 'model': 'text-model',
                    'reviewBaseUrl': 'https://review.example.invalid/v1', 'reviewModel': 'editor', 'apiKey': 'test-key', 'wordApiKey': 'word-test-key'}
                source.save_settings(settings)
                source.cache(book['id'], 0, profile(settings), '人工构造的测试译文。')
                source.progress(book['id'], 1); source.set_bookmark(book['id'], 2)
                source.save_bulk_job(book['id'], profile(settings), 'text-model', 'running')
                destination = Path(directory) / 'new-program' / '.data'
                copy_library(source.directory, destination)
                restored = Store(destination)
                try:
                    self.assertEqual(len(restored.library()), 1)
                    self.assertEqual(profile(restored.settings()), profile(settings))
                    self.assertEqual(restored.translation(book['id'], 0, profile(restored.settings())), '人工构造的测试译文。')
                    self.assertEqual(restored.book(book['id'])['currentPage'], 1)
                    self.assertEqual(restored.bookmark(book['id'])['page'], 2)
                    asset = book['illustrations'][0]['asset']
                    self.assertEqual(restored.image(book, asset), source.image(book, asset))
                    self.assertEqual(restored.settings()['wordApiKey'], '')
                    self.assertEqual(restored.bulk_jobs()[0]['state'], 'paused')
                finally: restored.db.close()
                self.assertEqual(source.bulk_jobs()[0]['state'], 'running')
            finally: source.db.close()

    def test_word_configuration_uses_its_own_key_and_never_inherits_book_model(self):
        base = {'baseUrl': 'https://translation.example.invalid/v1', 'model': 'text-model', 'apiKey': 'primary-test',
                'targetLanguage': '简体中文', 'reviewEnabled': False, 'reviewBaseUrl': '', 'reviewModel': '', 'reviewApiKey': ''}
        with self.assertRaises(ValueError): word_settings(base)
        values = {'wordBaseUrl': 'https://words.example.invalid/v1', 'wordModel': 'word-model', 'wordApiKey': 'word-test'}
        result = validate_settings(values, base)
        selected = word_settings(result)
        self.assertEqual((selected['baseUrl'], selected['model'], selected['apiKey']), tuple(values.values()))
        self.assertEqual(validate_settings({'clearWordApiKey': True}, result)['wordApiKey'], '')
        for invalid in [{'wordBaseUrl': 'https://words.example.invalid/v1'}, {'wordModel': 'words'}, {'wordBaseUrl': 'file:///private', 'wordModel': 'words'}]:
            with self.assertRaises(ValueError): validate_settings(invalid, base)

    def test_public_audit_rejects_books_and_deployment_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'example.epub').write_bytes(b'private book')
            with self.assertRaisesRegex(RuntimeError, 'private library'): audit_public(root)
            (root / 'example.epub').unlink()
            (root / 'settings.py').write_text('endpoint=' + str(ipaddress.IPv4Address(0x0A000001)), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'network address'): audit_public(root)
        self.assertTrue(audit_public()['ok'])

    def test_public_builder_rejects_including_private_library(self):
        from tools.build_release import build
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'release'
            with self.assertRaisesRegex(ValueError, '私人书库'): build(target, include_data=True)
            self.assertFalse(target.exists())
