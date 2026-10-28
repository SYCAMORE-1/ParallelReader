import json
import tempfile
import unittest
import uuid
from unittest.mock import patch

from reader.glossary import build_glossary, glossary_reference
from reader.language import LanguageTools
from reader.storage import Store
from reader.translation import translate, translate_unit
from reader.translation_rules import translation_plan


def book_fixture():
    values = [('The primary weapons are 416s; Black Hills makes their ammunition.', 0, 'paragraph'),
              ('Glossary', 1, 'heading'), ('HK416:', 1, 'paragraph'),
              ('A rifle made by Heckler & Koch.', 1, 'paragraph'),
              ('Additional technical description.', 1, 'paragraph'),
              ('Black Hills Ammunition: An ammunition manufacturer.', 1, 'paragraph'),
              ('AN/PRC-163: A military radio.', 1, 'paragraph'),
              ('FAL:', 1, 'paragraph'), ('Fusil Automatique Léger: a battle rifle.', 1, 'paragraph'),
              ('About the Author', 2, 'heading'), ('NotATerm: a biography detail.', 2, 'paragraph')]
    return {'id': str(uuid.uuid4()), 'title': 'Example novel', 'author': 'Example',
            'chapters': [{'id': 0, 'title': 'Chapter 1'}, {'id': 1, 'title': 'Glossary'}, {'id': 2, 'title': 'About the Author'}],
            'paragraphs': [{'id': i, 'text': text, 'chapter': chapter, 'kind': kind} for i, (text, chapter, kind) in enumerate(values)]}


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.settings = self.store.settings()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_split_inline_and_expansion_definitions_are_lossless(self):
        book = book_fixture()
        entries = {e['term']: e for e in build_glossary(book)['entries']}
        self.assertEqual(set(entries), {'HK416', 'Black Hills Ammunition', 'AN/PRC-163', 'FAL'})
        self.assertIn('Additional technical description.', entries['HK416']['definition'])
        self.assertEqual(entries['FAL']['definition'], 'Fusil Automatique Léger: a battle rifle.')
        self.assertEqual(entries['HK416']['paragraphs'], [2, 3, 4])

    def test_matching_uses_model_plural_and_brand_alias_without_article_alias(self):
        book = book_fixture()
        book['paragraphs'][0]['text'] += ' It is an operation.'
        entries = glossary_reference(book, 0)['entries']
        self.assertEqual({e['term'] for e in entries}, {'HK416', 'Black Hills Ammunition'})
        self.assertEqual(next(e for e in entries if e['term'] == 'HK416')['matchedAlias'], '416')
        for text in ['HK416s are rifles.', 'HK-416 rifles are ready.', 'HK 416 rifles are ready.']:
            book['paragraphs'][0]['text'] = text
            self.assertEqual(glossary_reference(book, 0)['entries'][0]['term'], 'HK416')

    def test_bare_numbers_unrelated_words_and_self_definition_are_not_evidence(self):
        book = book_fixture()
        for text in ['He reached page 416.', 'Room 1416 was dark.', 'An unrelated chapter.']:
            book['paragraphs'][0]['text'] = text
            self.assertIsNone(glossary_reference(book, 0))
        self.assertIsNone(glossary_reference(book, 2))

    def test_books_do_not_share_glossaries_or_fabricate_missing_entries(self):
        book = book_fixture()
        other = book_fixture()
        other['chapters'][1]['title'] = 'Notes'
        other['paragraphs'] = other['paragraphs'][:1]
        self.assertIsNotNone(glossary_reference(book, 0))
        self.assertIsNone(glossary_reference(other, 0))
        book['paragraphs'][0]['text'] = 'They carried AKMs.'
        self.assertIsNone(glossary_reference(book, 0), 'No AKM entry was supplied by this book')

    def test_heading_fallback_stops_at_author_biography_and_limits_reference_size(self):
        book = book_fixture()
        book['chapters'][1]['title'] = 'Appendix'
        entries = build_glossary(book)['entries']
        self.assertEqual(len(entries), 4)
        reference = glossary_reference(book, 0, max_entries=1)
        self.assertEqual(len(reference['entries']), 1)
        self.assertIsNone(glossary_reference(book, 0, max_characters=1))

    def test_primary_and_editor_both_receive_author_definitions_and_save_sources(self):
        book = book_fixture()
        def response(*args):
            return {'choices': [{'message': {'content': json.dumps({'translations': ['主武器是416步枪，弹药由Black Hills制造。']}, ensure_ascii=False)}}]}
        with patch('reader.translation.person_names', return_value=[]), patch('reader.translation.request_json', side_effect=response) as request:
            result = translate(book, 0, self.settings)
        for call in request.call_args_list:
            system = call.args[1]['messages'][0]['content']
            self.assertIn('book_glossary', system)
            self.assertIn('A rifle made by Heckler & Koch.', system)
            context = json.loads(system.split('Reference context (DO NOT output):\n', 1)[1])
            self.assertNotIn('NotATerm', json.dumps(context['book_glossary']))
        self.assertEqual({entry['term'] for entry in result['glossary']['entries']}, {'HK416', 'Black Hills Ammunition'})
        self.assertNotIn('definition', result['glossary']['entries'][0])

    def test_compact_recovery_retains_relevant_glossary(self):
        book = book_fixture()
        book['paragraphs'][0]['text'] = 'The HK416 rifle was ready.'
        plan = translation_plan(book['paragraphs'][0]['text'], [])
        replies = [{'choices': [{'message': {'content': json.dumps({'translation': value}, ensure_ascii=False)}}]}
                   for value in ['', 'HK416步枪已经准备好了。']]
        with patch('reader.translation.request_json', side_effect=replies) as request:
            translate_unit(book, 0, self.settings, plan, plan['pieces'][0])
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertIn('A rifle made by Heckler & Koch.', call.args[1]['messages'][0]['content'])

    def test_dictionary_does_not_read_or_send_future_glossary(self):
        book = book_fixture()
        book['paragraphs'][0]['text'] = 'The rifles were ready.'
        class PastOnly(list):
            def __getitem__(self, key):
                if isinstance(key, int) and key > 0:
                    raise AssertionError('Word lookup accessed future glossary')
                return super().__getitem__(key)
            def __iter__(self):
                raise AssertionError('Word lookup scanned the book')
        book['paragraphs'] = PastOnly(book['paragraphs'])
        calls = []
        answer = {'lemma': 'rifle', 'partOfSpeech': 'n.', 'basicMeaning': '步枪。', 'contextMeaning': '这里指那些已经准备好的步枪。', 'note': ''}
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            tools = LanguageTools(store, request_fn=lambda *args: calls.append(args) or answer)
            tools.explain(book, 0, 'rifles', store.settings() | {'wordProvider': 'llm', 'wordBaseUrl': 'https://words.example.invalid/v1', 'wordModel': 'test-words'})
            payload = json.dumps(calls[0][3], ensure_ascii=False)
            self.assertNotIn('glossary', payload)
            self.assertNotIn('Heckler', payload)
            self.assertNotIn('followingSentences', payload)
            store.db.close()


if __name__ == '__main__':
    unittest.main()
