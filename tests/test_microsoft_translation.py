"""Synthetic tests for the keyless Microsoft web translation adapter."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from reader.machine_translation import PlainText, protect_text
from reader.microsoft_translation import MicrosoftTranslator, Redirects, chunks, parse_session, allowed_url, utf16_length
from reader.providers import images_enabled, review_enabled
from reader.storage import Store
from reader.translation import Translator, profile, translate
from server import validate_settings
from test_reader import queue_book, until

PAGE = 'IG:"ABCD1234" data-iid="translator.1000" params_AbusePreventionHelper = [12345,"synthetic-session-token",3600000];'


def result(text='测试译文。'):
    return json.dumps([{'translations': [{'to': 'zh-Hans', 'text': text}]}])


class MicrosoftTests(unittest.TestCase):
    def test_session_uses_regional_host_and_expiry(self):
        value = parse_session(PAGE, 'https://cn.bing.com/translator', 100)
        self.assertEqual(value['url'], 'https://cn.bing.com/ttranslatev3')
        self.assertEqual(value['expires'], 3640)
        self.assertEqual(value['iid'], 'translator.1000')
        for invalid in ['https://bing.com.evil.invalid/', 'http://www.bing.com/', 'https://user:pass@bing.com/', 'https://example.invalid/']:
            self.assertFalse(allowed_url(invalid))
            with self.assertRaises(RuntimeError): parse_session(PAGE, invalid, 0)
        with self.assertRaisesRegex(RuntimeError, '网页验证'): parse_session('<html>Verification required</html>', 'https://www.bing.com/translator', 0)

    def test_redirect_preserves_post_and_rejects_external_host(self):
        redirect = Redirects()
        request = urllib.request.Request('https://www.bing.com/ttranslatev3', data=b'text=hello', method='POST')
        value = redirect.redirect_request(request, None, 302, '', {}, 'https://cn.bing.com/ttranslatev3')
        self.assertEqual(value.method, 'POST'); self.assertEqual(value.data, b'text=hello')
        with self.assertRaises(RuntimeError): redirect.redirect_request(request, None, 302, '', {}, 'https://example.invalid/')

    def test_chunks_preserve_text_markup_and_utf16_limit(self):
        source = ('Alex & his friend opened a door. ' * 80) + ('图😀&' * 650)
        parts = chunks(protect_text(source, ['Alex']))
        self.assertGreater(len(parts), 4)
        recovered = []
        for part in parts:
            self.assertLessEqual(utf16_length(part), 1000)
            self.assertTrue(part.startswith('<p>') and part.endswith('</p>'))
            self.assertEqual(part.count('<span '), part.count('</span>'))
            parser = PlainText(); parser.feed(part); parser.close(); recovered.extend(parser.parts)
        self.assertEqual(''.join(recovered), source)
        self.assertTrue(any('notranslate' in part for part in parts))

    def test_protocol_and_cached_session_no_azure_key(self):
        client = MicrosoftTranslator(); calls = []
        def fake(request):
            calls.append(request)
            return (PAGE, 'https://cn.bing.com/translator') if request.data is None else (result(), request.full_url)
        with patch.object(client, 'fetch', side_effect=fake):
            for _ in range(2): self.assertEqual(client.translate({'targetLanguage': '简体中文'}, ['<p>Hello.</p>']), ['测试译文。'])
        self.assertEqual(len(calls), 3)
        data = urllib.parse.parse_qs(calls[1].data.decode())
        self.assertEqual(data['fromLang'], ['auto-detect']); self.assertEqual(data['to'], ['zh-Hans'])
        self.assertIn('cn.bing.com/ttranslatev3', calls[1].full_url)
        self.assertNotIn('Ocp-apim-subscription-key', calls[1].headers)

    def test_expired_session_refreshes_once_and_invalid_data_is_rejected(self):
        client = MicrosoftTranslator(); posts = []
        def fake(request):
            if request.data is None: return PAGE, 'https://www.bing.com/translator'
            posts.append(request)
            return (json.dumps({'statusCode': 205}) if len(posts) == 1 else result()), request.full_url
        with patch.object(client, 'fetch', side_effect=fake): self.assertEqual(client.one('<p>Hello.</p>', 'zh-Hans'), '测试译文。')
        self.assertEqual(len(posts), 2)
        for invalid in ['<html>Not JSON</html>', '{}', '[]', json.dumps({'statusCode': 429}), json.dumps({'statusCode': 205}),
                        json.dumps([{'translations': [{'to': 'ja', 'text': 'wrong language'}]}])]:
            with patch.object(client, 'fetch', return_value=(invalid, 'https://www.bing.com/ttranslatev3')):
                client.session = parse_session(PAGE, 'https://www.bing.com/translator', 10**20)
                with self.assertRaises(RuntimeError): client.one('<p>Hello.</p>', 'zh-Hans')

    def test_concurrent_calls_share_authentication_and_limit_requests(self):
        client = MicrosoftTranslator(); calls = []; lock = threading.Lock()
        def fake(request):
            with lock: calls.append(request)
            return (PAGE, 'https://www.bing.com/translator') if request.data is None else (result(), request.full_url)
        with patch.object(client, 'fetch', side_effect=fake), ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: client.one('<p>Hello.</p>', 'zh-Hans'), range(12)))
        self.assertEqual(values, ['测试译文。'] * 12)
        self.assertEqual(sum(request.data is None for request in calls), 1)

    def test_network_and_http_errors_do_not_expose_session(self):
        client = MicrosoftTranslator()
        for error in [urllib.error.URLError('secret-token'), urllib.error.HTTPError('https://www.bing.com', 429, 'secret-token', {}, io.BytesIO(b'private diagnostic'))]:
            with patch.object(client.opener, 'open', side_effect=error):
                with self.assertRaises(RuntimeError) as raised: client.fetch(urllib.request.Request('https://www.bing.com/translator'))
                self.assertNotIn('secret-token', str(raised.exception))

    def test_no_configuration_profiles_capabilities_and_whole_book_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            settings = validate_settings({'translationProvider': 'microsoft'}, store.settings())
            store.save_settings(settings); book = store.add(queue_book())
            self.assertEqual(settings['baseUrl'], ''); self.assertNotIn('azureApiKey', settings)
            self.assertFalse(images_enabled(settings)); self.assertFalse(review_enabled(settings))
            self.assertNotEqual(profile(settings), profile(settings | {'translationProvider': 'llm'}))
            self.assertEqual(profile(settings), profile(settings | {'wordApiKey': 'irrelevant', 'model': 'irrelevant'}))
            with patch('reader.microsoft_translation.CLIENT.translate', side_effect=lambda settings, texts: ['<p>测试译文。</p>'] * len(texts)) as web, patch('reader.translation.request_json', side_effect=AssertionError('No LLM calls')):
                worker = Translator(store, workers=2)
                try:
                    worker.bulk_action(book, 'start')
                    until(lambda: worker.bulk_status(book)['state'] == 'completed')
                    value = worker.bulk_status(book)
                    self.assertEqual(value['model'], 'Azure Translate'); self.assertEqual(value['percent'], 100)
                    self.assertEqual(web.call_count, 10)
                    detail = store.translation_detail(book['id'], 0, profile(settings))
                    self.assertEqual(detail['provider'], 'microsoft'); self.assertEqual(detail['alignment'], [{'source': [0], 'target': [0]}])
                    worker.bulk_action(book, 'start'); self.assertEqual(web.call_count, 10)
                    with self.assertRaisesRegex(ValueError, 'Azure Translate'): worker.reproof.request(book, {'scope': 'book'})
                finally:
                    worker.close()
                    for thread in worker.threads: thread.join(3)
                    store.db.close()
