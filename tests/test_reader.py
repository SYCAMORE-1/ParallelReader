import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from reader.epub import parse_epub
from reader.storage import Store
from reader.translation import Translator, messages, profile, read_model_field, translate, translation_error
from server import make_server


def epub_fixture():
    output = io.BytesIO()
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
        "OPS/book.opf": '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>A &amp; B</dc:title><dc:creator>Test Author</dc:creator><dc:language>en</dc:language></metadata><manifest><item id="one" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/></spine></package>',
        "OPS/nav.xhtml": '<html><body><nav><a href="chapter.xhtml">Chapter 1</a></nav></body></html>',
        "OPS/chapter.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ignore me</title><script>evil()</script></head><body><h1>Chapter One</h1><p>First <em>real</em> paragraph &amp; a&nbsp;space.</p><p>Second paragraph.<br/>New line.</p><script>bad()</script><div hidden="hidden">Hidden words</div></body></html>',
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def queue_book():
    return {"id": "queue-test", "title": "A Novel", "author": "An Author", "language": "en", "cover": None, "startPage": 0,
            "chapters": [{"id": 0, "title": "Chapter One", "startPage": 0, "endPage": 9}],
            "pages": [{"id": i, "chapter": 0, "paragraphs": [i]} for i in range(10)],
            "paragraphs": [{"id": i, "chapter": 0, "kind": "paragraph", "text": f"Paragraph {i}."} for i in range(10)]}


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("Timed out")


class EpubTests(unittest.TestCase):
    def test_text_spine_toc_entities_and_no_active_content(self):
        book = parse_epub(epub_fixture())
        self.assertEqual(book["title"], "A & B")
        self.assertEqual(book["chapters"][0]["title"], "Chapter 1")
        self.assertEqual([p["text"] for p in book["paragraphs"]], ["Chapter One", "First real paragraph & a space.", "Second paragraph. New line."])
        self.assertEqual(book["warnings"], [])

    def test_bad_file_and_missing_container(self):
        with self.assertRaises(ValueError):
            parse_epub(b"not an epub")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../unsafe", "no extraction")
        with self.assertRaises(ValueError):
            parse_epub(output.getvalue())


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.save_settings(self.store.settings() | {'reviewEnabled': False})
        self.book = queue_book()
        self.store.add(self.book)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_prompt_separates_current_text_and_two_sided_context(self):
        prompt = messages(self.book, 4, self.store.settings())
        self.assertEqual(json.loads(prompt[-1]["content"].split('\n', 1)[1])["paragraph"], "Paragraph 4.")
        self.assertNotIn("Paragraph 3.", prompt[-1]["content"])
        self.assertIn("Paragraph 2.", prompt[0]["content"])
        self.assertIn("Paragraph 6.", prompt[0]["content"])
        self.assertIn("A Novel", prompt[0]["content"])
        self.assertIn("Chapter One", prompt[0]["content"])
        self.assertIn("Paragraph 9.", prompt[0]["content"])
        self.assertNotIn("Paragraph 4.", prompt[0]["content"])

    def test_reading_errors_explain_problem_without_cache_implementation_terms(self):
        error = translation_error('模型改写或遗漏了受保护的人名，已拒绝缓存，请重试')
        self.assertEqual(error['errorCode'], 'name_preservation')
        self.assertIn('人物原名', error['error'])
        self.assertNotIn('缓存', error['error'])
        self.assertEqual(translation_error('模型没有返回有效译文，请重试')['errorCode'], 'incomplete_translation')

    def test_structured_output_and_truncation_detection(self):
        with patch('reader.translation.person_names', return_value=[]), patch('reader.translation.request_json', return_value={"choices": [{"finish_reason": "stop", "message": {"content": '{"translations":["译文"]}'}}]}) as request:
            self.assertEqual(translate(self.book, 4, self.store.settings())['text'], "译文")
            self.assertFalse(request.call_args.args[1]["chat_template_kwargs"]["enable_thinking"])
        with patch('reader.translation.person_names', return_value=[]), patch('reader.translation.request_json', return_value={"choices": [{"finish_reason": "length"}]}):
            with self.assertRaises(RuntimeError):
                translate(self.book, 4, self.store.settings())

    def test_truncated_paragraph_recovers_by_translating_individual_units(self):
        self.book['paragraphs'][4]['text'] = 'Morgan left. He was tired.'
        primary = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"translations":["  \n\n'}}]}
        outputs = [primary, {'choices': [{'message': {'content': '{"translation":"__PERSON_000__离开了。"}'}}]},
                   {'choices': [{'message': {'content': '{"translation":"他累了。"}'}}]}]
        with patch('reader.translation.person_names', return_value=['Morgan']), patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.store.settings())
        self.assertEqual(result['text'], 'Morgan离开了。他累了。')
        self.assertEqual(request.call_count, 3)
        self.assertTrue(all(call.args[1]['response_format']['json_schema']['name'] == 'repair_translation_unit' for call in request.call_args_list[1:]))
        self.assertEqual(len(result['alignment']), 2)

    def test_json_commentary_is_ignored_but_truncated_values_are_not_guessed(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '```json\n{"translations":["译文"]}\n```\nNote: an explanation, not a translation.'}}]}
        self.assertEqual(read_model_field(response, 'translations'), ['译文'])
        with self.assertRaises(RuntimeError):
            read_model_field({'choices': [{'finish_reason': 'length', 'message': {'content': '{"translations":["未完成'}}]}, 'translations')

    def test_web_handles_and_model_identifiers_remain_usable_without_model_requests(self):
        sources = ['www.SimonandSchuster.com/Jack-Carr', 'SimonandSchuster.com', '@EmilyBestler @EmilyBestler @EmilyBestler',
                   'AN/PAS-13G(v)L3/LWTS:', 'Mk 46 MOD 1:', 'Zodiac Mk 2 GR:']
        with patch('reader.translation.person_names', side_effect=AssertionError('literal reference must not request NER')), patch('reader.translation.request_json', side_effect=AssertionError('literal reference must not request translation')):
            for source in sources:
                with self.subTest(source=source):
                    self.book['paragraphs'][4]['text'] = source
                    result = translate(self.book, 4, self.store.settings())
                    self.assertEqual(result['text'], source)
                    self.assertEqual(''.join(result['sourceSentences']), source)

    def test_empty_unit_recovers_without_retranslating_valid_units(self):
        self.book['paragraphs'][4]['text'] = 'He left. He was tired.'
        outputs = [{'choices': [{'message': {'content': '{"translations":["他走了。", ""]}'}}]},
                   {'choices': [{'message': {'content': '{"translation":"他累了。"}'}}]}]
        with patch('reader.translation.person_names', return_value=[]), patch('reader.translation.request_json', side_effect=outputs) as request:
            self.assertEqual(translate(self.book, 4, self.store.settings())['text'], '他走了。他累了。')
            self.assertEqual(request.call_count, 2)

    def test_repeated_unit_output_uses_compact_prompt_with_same_context_and_validation(self):
        self.book['paragraphs'][4]['text'] = 'Morgan waited.'
        outputs = [{'choices': [{'message': {'content': '{"translations":["他等着。"]}'}}]},
                   {'choices': [{'finish_reason': 'length', 'message': {'content': '{"translation":"Morgan Morgan Morgan'}}]},
                   {'choices': [{'message': {'content': '{"translation":"__PERSON_000__等着。"}'}}]}]
        with patch('reader.translation.person_names', return_value=['Morgan']), patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.store.settings())
            self.assertEqual(result['text'], 'Morgan等着。')
            compact = request.call_args.args[1]
            self.assertEqual(compact['response_format']['type'], 'json_object')
            self.assertIn('Paragraph 3.', compact['messages'][0]['content'])
            self.assertNotIn('Paragraph 3.', compact['messages'][-1]['content'])
            self.assertIn('__PERSON_000__', compact['messages'][-1]['content'])

    def test_compact_recovery_still_rejects_lost_names(self):
        self.book['paragraphs'][4]['text'] = 'Morgan waited.'
        response = {'choices': [{'message': {'content': '{"translations":["他等着。"],"translation":"他等着。"}'}}]}
        with patch('reader.translation.person_names', return_value=['Morgan']), patch('reader.translation.request_json', return_value=response):
            with self.assertRaises(RuntimeError):
                translate(self.book, 4, self.store.settings())

    def test_stale_jobs_cancel_and_current_page_first(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def fake(book, pid, settings):
            calls.append(pid)
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return f"translation {pid}"
        worker = Translator(self.store, workers=1, translate_fn=fake)
        try:
            worker.window(self.book, 2)
            self.assertTrue(started.wait(2))
            self.assertEqual(calls, [2])
            worker.window(self.book, 7)
            release.set()
            until(lambda: len(calls) == 6)
            self.assertEqual(calls[:2], [2, 7])
            self.assertEqual(set(calls[1:]), {5, 6, 7, 8, 9})
            until(lambda: not worker.active)
            worker.window(self.book, 7)
            time.sleep(.05)
            self.assertEqual(len(calls), 6, "cached pages must not call the model again")
            self.assertTrue(all(x["state"] == "ready" for x in worker.status(self.book, 7)["items"].values()))
        finally:
            release.set()
            worker.close()
            for thread in worker.threads:
                thread.join(2)

    def test_only_a_name_losing_unit_is_repaired_with_grammar_constraint(self):
        self.book['paragraphs'][4]['text'] = 'Morgan left. He was tired.'
        initial = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'translations': ['他离开了。', '他累了。']})}}]}
        repaired = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'translation': '__PERSON_000__离开了。'})}}]}
        with patch('reader.translation.person_names', return_value=['Morgan']), patch('reader.translation.request_json', side_effect=[initial, repaired]) as request:
            result = translate(self.book, 4, self.store.settings())
            self.assertEqual(result['text'], 'Morgan离开了。他累了。')
            self.assertEqual(request.call_count, 2)
            repair_payload = request.call_args.args[1]
            self.assertIn('__PERSON_000__', repair_payload['response_format']['json_schema']['schema']['properties']['translation']['pattern'])
            self.assertEqual(len(result['alignment']), 2)

    def test_selected_priority_pause_and_persistent_profile_cache(self):
        calls = []
        worker = Translator(self.store, workers=1, translate_fn=lambda b, p, s: calls.append(p) or "cached")
        try:
            worker.window(self.book, 3, enabled=False)
            time.sleep(.04)
            self.assertEqual(calls, [])
            worker.window(self.book, 3, selected=3, enabled=False)
            until(lambda: self.store.translation(self.book["id"], 3, profile(self.store.settings())))
            self.assertEqual(calls, [3])
            second = Store(self.temp.name)
            self.assertEqual(second.translation(self.book["id"], 3, profile(second.settings())), "cached")
            settings = second.settings() | {"targetLanguage": "日本語"}
            self.assertIsNone(second.translation(self.book["id"], 3, profile(settings)))
            second.db.close()
        finally:
            worker.close()
            for thread in worker.threads:
                thread.join(2)

    def test_failure_is_visible_and_explicit_retry_can_recover(self):
        calls = []
        def failing_then_ok(book, pid, settings):
            calls.append(pid)
            if len(calls) <= 2:
                raise RuntimeError("model offline")
            return "recovered"
        worker = Translator(self.store, workers=1, translate_fn=failing_then_ok)
        try:
            worker.window(self.book, 3, selected=3, enabled=False)
            until(lambda: worker.status(self.book, 3)['items'][3]['state'] == 'error')
            self.assertIsNone(self.store.translation(self.book['id'], 3, profile(self.store.settings())))
            worker.window(self.book, 3, selected=3, retry=True, enabled=False)
            until(lambda: self.store.translation(self.book['id'], 3, profile(self.store.settings())) == 'recovered')
            self.assertEqual(calls, [3, 3, 3])
        finally:
            worker.close()
            for thread in worker.threads:
                thread.join(2)

    def test_worker_persists_translation_and_mapping_together(self):
        detail = {'text': '第三段。', 'sourceSentences': ['Paragraph 3.'], 'targetSentences': ['第三段。'], 'alignment': [{'source': [0], 'target': [0]}], 'alignmentMethod': 'translation-slots'}
        worker = Translator(self.store, workers=1, translate_fn=lambda *args: detail)
        try:
            worker.window(self.book, 3, selected=3, enabled=False)
            fingerprint = profile(self.store.settings())
            until(lambda: self.store.translation(self.book['id'], 3, fingerprint))
            self.assertEqual(self.store.translation_detail(self.book['id'], 3, fingerprint), detail)
        finally:
            worker.close()
            for thread in worker.threads:
                thread.join(2)


class HttpTests(unittest.TestCase):
    def test_explicit_preview_host_keeps_same_origin_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory, allowed_hosts=['192.0.2.10'])
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            authority = f'192.0.2.10:{app.server_port}'
            origin = 'http://' + authority
            url = f'http://127.0.0.1:{app.server_port}'
            try:
                with opener.open(urllib.request.Request(url + '/api/health', headers={'Host': authority})) as response:
                    self.assertTrue(json.load(response)['ok'])
                configuration = {'baseUrl': 'https://translation.example.invalid/v1', 'model': 'fixture-model', 'reviewEnabled': False}
                request = urllib.request.Request(url + '/api/settings', data=json.dumps(configuration).encode(), headers={'Content-Type': 'application/json', 'Host': authority, 'Origin': origin})
                with opener.open(request) as response:
                    self.assertTrue(json.load(response)['ok'])
                for destination, headers in [
                    (url, {'Host': f'attacker.test:{app.server_port}'}),
                    (url, {'Host': authority, 'Origin': 'https://attacker.test'}),
                ]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        opener.open(urllib.request.Request(destination + '/api/health', headers=headers))
                    self.assertEqual(error.exception.code, 403)
            finally:
                app.translator.close()
                app.shutdown()
                app.server_close()
                for worker in app.translator.threads:
                    worker.join(2)
                app.store.db.close()

    def test_import_progress_validation_and_origin_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_server(port=0, data_dir=directory, sample=Path(directory) / "missing.epub", translate_fn=lambda b, p, s: "译文")
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{app.server_port}"
            def post(route, value, origin=None, raw=False):
                req = urllib.request.Request(url + route, data=value if raw else json.dumps(value).encode(), headers={"Content-Type": "application/epub+zip" if raw else "application/json", **({"Origin": origin} if origin else {})})
                with urllib.request.urlopen(req) as response:
                    return json.load(response)
            try:
                book = post('/api/import', epub_fixture(), raw=True)
                self.assertEqual(len(book["paragraphs"]), 3)
                post('/api/books/' + book['id'] + '/window', {"page": 0})
                until(lambda: len(app.store.translations(book['id'], [0, 1, 2], profile(app.store.settings()))) == 3)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/settings', {}, origin="https://attacker.test")
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/books/' + book['id'] + '/progress', {"page": 999})
                self.assertEqual(error.exception.code, 400)
                # Reimport is idempotent and cannot destroy reading progress/cache.
                self.assertEqual(post('/api/import', epub_fixture(), raw=True)['id'], book['id'])
                with urllib.request.urlopen(url + '/api/settings') as response:
                    self.assertEqual(json.load(response)['apiKey'], '')
            finally:
                app.translator.close()
                app.shutdown()
                app.server_close()
                for worker in app.translator.threads:
                    worker.join(2)
                app.store.db.close()


if __name__ == '__main__':
    unittest.main()

