import tempfile
import unittest
from unittest.mock import patch
from reader.language import LanguageTools
from reader.microsoft_dictionary import context_markup
from reader.microsoft_translation import utf16_length
from reader.storage import Store
from test_reader import queue_book


class MicrosoftDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(self.temp.name)
        self.settings = self.store.settings(); self.book = queue_book()
        self.book['paragraphs'][3]['text'] = 'The traveler sat on the bank. The next sentence contains a spoiler.'
        self.book['paragraphs'][2]['text'] = 'He followed the river.'
        self.offset = self.book['paragraphs'][3]['text'].index('bank')

    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def test_default_is_keyless_independent_of_text_provider_and_cached(self):
        self.assertEqual(self.settings['wordProvider'], 'microsoft')
        dictionary = [{'normalizedSource': 'bank', 'translations': [{'displayTarget': '银行', 'posTag': 'NOUN'}, {'displayTarget': '存储', 'posTag': 'VERB'}]}]
        calls = []
        def one(text, language):
            calls.append(text)
            self.assertNotIn('spoiler', text); self.assertNotIn('Paragraph 4', text)
            self.assertIn('He followed the river.', text)
            return '<p>他沿河而行。</p><p id="pr-current">他坐在<b id="pr-word">河岸</b>上。</p>'
        with patch('reader.microsoft_dictionary.CLIENT.dictionary', return_value=dictionary) as lookup, patch('reader.microsoft_dictionary.CLIENT.one', side_effect=one), patch('reader.translation.request_json', side_effect=AssertionError('Must not use an LLM')):
            tools = LanguageTools(self.store)
            for provider in ['llm', 'azure', 'microsoft']:
                value = tools.explain(self.book, 3, 'bank', self.settings | {'translationProvider': provider}, self.offset)
                self.assertEqual(value['basicMeaning'], '银行'); self.assertEqual(value['contextMeaning'], '河岸')
                self.assertEqual(value['partOfSpeech'], 'n. / v.'); self.assertEqual(value['provider'], 'microsoft')
                self.assertEqual(value['contextWindow']['after'], 0)
            lookup.assert_called_once(); self.assertEqual(len(calls), 1)

    def test_exact_selected_word_and_previous_ten_only(self):
        self.book['paragraphs'] = [{'id': i, 'chapter': 0, 'kind': 'paragraph', 'text': f'Past {i}.'} for i in range(20)]
        self.book['paragraphs'][12]['text'] = 'A bank stood near the bank. Future secret follows.'
        offset = self.book['paragraphs'][12]['text'].rindex('bank')
        markup, current, count, trimmed = context_markup(self.book, 12, offset, 'bank')
        self.assertEqual(count, 10); self.assertFalse(trimmed)
        self.assertIn('Past 2.', markup); self.assertNotIn('Past 1.', markup)
        self.assertNotIn('Future secret', markup); self.assertNotIn('Past 13.', markup)
        self.assertIn('A bank stood near the <b id="pr-word">bank</b>', current)

    def test_long_context_is_bounded_and_does_not_drop_selected_word(self):
        self.book['paragraphs'][3]['text'] = 'An & old story ' * 200 + 'bank' + ' beside the river' * 200 + '.'
        offset = self.book['paragraphs'][3]['text'].index('bank')
        markup, current, count, trimmed = context_markup(self.book, 3, offset, 'bank')
        self.assertLessEqual(utf16_length(markup), 1000); self.assertTrue(trimmed)
        self.assertIn('<b id="pr-word">bank</b>', markup)
        self.assertIn('&amp;', current)

    def test_missing_dictionary_or_markup_falls_back_to_labeled_sentence_translation(self):
        with patch('reader.microsoft_dictionary.CLIENT.dictionary', side_effect=RuntimeError('No dictionary for pair')), patch('reader.microsoft_dictionary.CLIENT.one', side_effect=['银行', '他坐在河岸上。', '他坐在河岸上。']):
            value = LanguageTools(self.store).explain(self.book, 3, 'bank', self.settings, self.offset)
            self.assertEqual(value['partOfSpeech'], ''); self.assertEqual(value['basicMeaning'], '银行')
            self.assertEqual(value['contextKind'], 'sentence'); self.assertEqual(value['contextMeaning'], '他坐在河岸上。')

    def test_invalid_selection_never_calls_service(self):
        with patch('reader.microsoft_dictionary.CLIENT.dictionary') as lookup:
            for word, offset in [('missing', None), ('bank', 0), ('bank!', None)]:
                with self.assertRaises(ValueError): LanguageTools(self.store).explain(self.book, 3, word, self.settings, offset)
            lookup.assert_not_called()
