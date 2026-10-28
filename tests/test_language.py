import tempfile
import unittest
from unittest.mock import patch

from reader.language import LanguageTools, word_settings, short_part_of_speech, split_sentences
from reader.text import sentence_window
from reader.storage import Store
from test_reader import queue_book


class LanguageTests(unittest.TestCase):
    def test_time_and_dotted_initials_do_not_create_stray_source_slots(self):
        text = 'They arrived after nine a.m. this morning. The briefing is at four p.m. today.'
        self.assertEqual(split_sentences(text), ['They arrived after nine a.m. this morning. ', 'The briefing is at four p.m. today.'])
        text = 'The initials “T.S.R. III” were engraved beneath the image. Phil turned it over.'
        self.assertEqual(split_sentences(text), ['The initials “T.S.R. III” were engraved beneath the image. ', 'Phil turned it over.'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = queue_book()
        self.book['paragraphs'][3]['text'] = 'Dr. Jones waited. He had arrived. Then he left.'
        self.settings = self.store.settings() | {'baseUrl': 'https://translation.example.invalid/v1', 'model': 'translation-model',
                    'wordBaseUrl': 'https://words.example.invalid/v1', 'wordModel': 'word-model', 'wordApiKey': ''}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_sentence_boundaries_preserve_abbreviations_dialogue_and_spacing(self):
        text = 'Dr. Jones arrived. “Stop!” he shouted. Then he left.'
        parts = split_sentences(text)
        self.assertEqual(parts, ['Dr. Jones arrived. ', '“Stop!” he shouted. ', 'Then he left.'])
        self.assertEqual(''.join(parts), text)
        chinese = '他来了。“停下！”她喊道。后来，他离开了。'
        self.assertEqual(''.join(split_sentences(chinese)), chinese)
        self.assertEqual(len(split_sentences(chinese)), 4)
        spaced = '“First.” Then he said, “Second.’ ”'
        self.assertEqual(''.join(split_sentences(spaced)), spaced)
        self.assertTrue(all(part.strip().strip('”’') for part in split_sentences(spaced)))
        decades = 'They arrived during the 1990s. Often armed, they used threats.'
        self.assertEqual(split_sentences(decades), ['They arrived during the 1990s. ', 'Often armed, they used threats.'])

    def test_semantic_many_to_one_alignment_and_reuse(self):
        calls = []
        groups = [{'source': [0, 1], 'target': [0]}, {'source': [2], 'target': [1]}]
        tools = LanguageTools(self.store, request_fn=lambda *args: calls.append(args) or {'groups': groups})
        text = '琼斯医生终于到了，正在等候。后来他走了。'
        result = tools.align(self.book, 3, text, self.settings)
        self.assertEqual(result['alignment'], groups)
        self.assertEqual(len(result['sourceSentences']), 3)
        self.assertEqual(len(result['targetSentences']), 2)
        tools.align(self.book, 3, text, self.settings)
        self.assertEqual(len(calls), 1)
        self.assertEqual(tools.view(self.book, 3, text, self.settings)['alignment'], groups)

    def test_incomplete_alignment_is_not_cached(self):
        tools = LanguageTools(self.store, request_fn=lambda *args: {'groups': [{'source': [0], 'target': [0]}]})
        text = '医生来了。他走了。'
        with self.assertRaises(RuntimeError):
            tools.align(self.book, 3, text, self.settings)
        self.assertIsNone(tools.view(self.book, 3, text, self.settings)['alignment'])

    def test_single_sentence_group_needs_no_inference(self):
        tools = LanguageTools(self.store, request_fn=lambda *args: self.fail('unnecessary model call'))
        result = tools.align(self.book, 3, '琼斯医生抵达后等了等便离开了。', self.settings)
        self.assertEqual(result['alignment'], [{'source': [0, 1, 2], 'target': [0]}])

    def test_coarse_many_to_many_groups_are_rejected(self):
        tools = LanguageTools(self.store, request_fn=lambda *args: {'groups': [{'source': [0, 1, 2], 'target': [0, 1]}]})
        with self.assertRaises(RuntimeError):
            tools.align(self.book, 3, '医生来了。他走了。', self.settings)

    def test_word_is_contextual_validated_and_cached_by_model(self):
        calls = []
        answer = {'lemma': 'arrive', 'partOfSpeech': '动词', 'basicMeaning': '到达、抵达。', 'contextMeaning': '这里表示此前已经到达。', 'note': 'arrived 是过去分词，与 had 构成过去完成时。'}
        tools = LanguageTools(self.store, request_fn=lambda *args: calls.append(args) or answer)
        result = tools.explain(self.book, 3, 'arrived', self.settings)
        self.assertEqual(result['lemma'], 'arrive')
        self.assertEqual(result['basicMeaning'], '到达、抵达。')
        self.assertEqual(result['contextMeaning'], '这里表示此前已经到达。')
        self.assertEqual(calls[0][3]['sentence'], 'He had arrived. ')
        tools.explain(self.book, 3, 'ARRIVED', self.settings)
        self.assertEqual(len(calls), 1)
        tools.explain(self.book, 3, 'arrived', self.settings | {'model': 'different-model'})
        self.assertEqual(len(calls), 1, 'Changing the paragraph model does not invalidate independent word explanations')
        tools.explain(self.book, 3, 'arrived', self.settings | {'wordModel': 'different-word-model'})
        self.assertEqual(len(calls), 2)
        for word in ['not present', '<script>', 'unrelated', None]:
            with self.assertRaises(ValueError):
                tools.explain(self.book, 3, word, self.settings)
        self.assertEqual(len(calls), 2)

    def test_context_contains_exactly_ten_sentences_on_each_side(self):
        self.book['paragraphs'] = [{'id': i, 'chapter': 0, 'kind': 'paragraph', 'text': f'Sentence {i}.'} for i in range(25)]
        view = sentence_window(self.book, 12, 0, 10)
        self.assertEqual(view['sentence'], 'Sentence 12.')
        self.assertEqual(view['previousSentences'], [f'Sentence {i}.' for i in range(2, 12)])
        self.assertEqual(view['followingSentences'], [f'Sentence {i}.' for i in range(13, 23)])
        edge = sentence_window(self.book, 0, 0, 10)
        self.assertEqual(edge['previousSentences'], [])
        self.assertEqual(len(edge['followingSentences']), 10)

    def test_lookup_uses_clicked_occurrence_and_separate_context_cache(self):
        text = 'Her car stopped. Then her sister arrived.'
        self.book['paragraphs'][3]['text'] = text
        calls = []
        def request(*args):
            calls.append(args)
            return {'lemma': 'her', 'partOfSpeech': '代词', 'basicMeaning': '她；她的。', 'contextMeaning': args[3]['sentence'], 'note': '', 'referentType': 'person'}
        tools = LanguageTools(self.store, request_fn=request)
        first = tools.explain(self.book, 3, 'Her', self.settings, offset=0)
        second_offset = text.index('her', 1)
        second = tools.explain(self.book, 3, 'her', self.settings, offset=second_offset)
        self.assertNotEqual(first['contextMeaning'], second['contextMeaning'])
        tools.explain(self.book, 3, 'HER', self.settings, offset=second_offset)
        self.assertEqual(len(calls), 2)
        for offset in [1, -1, len(text), True]:
            with self.assertRaises(ValueError):
                tools.explain(self.book, 3, 'her', self.settings, offset=offset)

    def test_lookup_keeps_basic_and_contextual_meaning_separate_and_rejects_empty(self):
        answer = {'lemma': 'arrive', 'partOfSpeech': '动词', 'basicMeaning': '', 'contextMeaning': '已经到达。', 'note': ''}
        tools = LanguageTools(self.store, request_fn=lambda *args: answer)
        with self.assertRaises(RuntimeError):
            tools.explain(self.book, 3, 'arrived', self.settings)

    def test_personified_object_meaning_does_not_invent_ownership_or_relationships(self):
        self.book['paragraphs'][3]['text'] = 'Don’t you recognize her?'
        answer = {'lemma': 'her', 'partOfSpeech': '代词', 'basicMeaning': '她；她的。', 'contextMeaning': '这辆车是某人物送给另一个人的礼物。', 'note': '重复的拟人解释', 'referentType': 'vehicle'}
        tools = LanguageTools(self.store, request_fn=lambda *args: answer)
        result = tools.explain(self.book, 3, 'her', self.settings)
        self.assertIn('这辆车', result['contextMeaning'])
        self.assertIn('拟人', result['contextMeaning'])
        self.assertNotIn('礼物', result['contextMeaning'])
        self.assertEqual(result['note'], '')
        answer['referentType'] = 'invalid'
        with self.assertRaises(RuntimeError):
            tools.explain(self.book, 3, 'her', self.settings | {'wordModel': 'other'})

    def test_word_lookup_never_sends_future_sentences_or_book_plot_hints(self):
        self.book['paragraphs'] = [{'id': i, 'chapter': 0, 'kind': 'paragraph', 'text': f'Sentence {i}.'} for i in range(25)]
        self.book['title'] = 'A future identity is revealed'
        calls = []
        answer = {'lemma': 'sentence', 'partOfSpeech': 'n.', 'basicMeaning': '句子。', 'contextMeaning': '这里指这一句话。', 'note': ''}
        tools = LanguageTools(self.store, request_fn=lambda *args: calls.append(args) or answer)
        result = tools.explain(self.book, 12, 'Sentence', self.settings | {'apiKey': 'paragraph-secret'})
        self.assertEqual(calls[0][3]['sentence'], 'Sentence 12.')
        self.assertEqual(calls[0][3]['previousSentences'], [f'Sentence {i}.' for i in range(2, 12)])
        self.assertNotIn('followingSentences', calls[0][3])
        self.assertNotIn('bookTitle', calls[0][3])
        self.assertNotIn('chapter', calls[0][3])
        self.assertEqual(result['contextWindow'], {'before': 10, 'after': 0})
        self.assertEqual(result['model'], 'word-model')
        self.assertEqual(calls[0][0]['baseUrl'], 'https://words.example.invalid/v1')
        self.assertEqual(calls[0][0]['apiKey'], '')
        self.assertIn('禁止剧透', calls[0][3]['task'])
        self.assertEqual(calls[0][4]['properties']['partOfSpeech']['enum'][0], 'n.')

    def test_past_only_window_does_not_read_future_paragraphs(self):
        class PastOnly(list):
            def __getitem__(self, index):
                if index > 3:
                    raise AssertionError('future paragraph accessed')
                return super().__getitem__(index)
        self.book['paragraphs'] = PastOnly(self.book['paragraphs'])
        result = sentence_window(self.book, 3, self.book['paragraphs'][3]['text'].index('arrived'), following_radius=0)
        self.assertEqual(result['sentence'], 'He had arrived. ')
        self.assertEqual(result['followingSentences'], [])
        self.assertEqual(result['previousSentences'][-1], 'Dr. Jones waited. ')

    def test_operator_explanation_uses_context_without_future_or_stale_term_cache(self):
        self.book['paragraphs'] = [{'id': i, 'chapter': 0, 'kind': 'paragraph', 'text': f'Sentence {i}.'} for i in range(25)]
        self.book['paragraphs'][12]['text'] = 'The operator entered the room.'
        self.book['title'] = 'A future identity is revealed'
        calls = []
        answer = {'lemma': 'operator', 'partOfSpeech': 'n.', 'basicMeaning': '操作员；经营者。',
                  'contextMeaning': '这里指执行行动的队员。', 'note': ''}
        tools = LanguageTools(self.store, request_fn=lambda *args: calls.append(args) or answer)
        with patch('reader.language.TERMINOLOGY_VERSION', 'previous-guidance'):
            tools.explain(self.book, 12, 'operator', self.settings)
        result = tools.explain(self.book, 12, 'operator', self.settings)
        tools.explain(self.book, 12, 'operator', self.settings)
        self.assertEqual(len(calls), 2, 'Updated terminology must bypass old word explanations and cache the new one')
        self.assertIn('不能套用词典第一义', calls[-1][2])
        self.assertEqual(calls[-1][3]['previousSentences'], [f'Sentence {i}.' for i in range(2, 12)])
        self.assertNotIn('followingSentences', calls[-1][3])
        self.assertNotIn('bookTitle', calls[-1][3])
        self.assertNotIn('chapter', calls[-1][3])
        self.assertEqual(result['basicMeaning'], answer['basicMeaning'])
        self.assertEqual(result['model'], 'word-model')

    def test_handler_word_uses_past_context_and_bypasses_old_handler_cache(self):
        self.book['paragraphs'][2]['text'] = 'A second dog charged at him.'
        self.book['paragraphs'][3]['text'] = 'Where is his handler?'
        calls = []
        answer = {'lemma': 'handler', 'partOfSpeech': 'n.', 'basicMeaning': '负责管理或操作的人。',
                  'contextMeaning': '这里指负责带领这条狗的训导员。', 'note': ''}
        tools = LanguageTools(self.store, request_fn=lambda *args: calls.append(args) or answer)
        with patch('reader.language.HANDLER_VERSION', 'previous-handler-guidance'):
            tools.explain(self.book, 3, 'handler', self.settings)
        result = tools.explain(self.book, 3, 'handler', self.settings)
        tools.explain(self.book, 3, 'handler', self.settings)
        self.assertEqual(len(calls), 2)
        self.assertIn('A second dog charged at him.', calls[-1][3]['previousSentences'])
        self.assertNotIn('followingSentences', calls[-1][3])
        self.assertNotIn('bookTitle', calls[-1][3])
        self.assertEqual(result['basicMeaning'], answer['basicMeaning'])
        self.assertEqual(result['model'], 'word-model')

    def test_word_pos_is_always_a_short_english_label(self):
        for value, expected in [('动词', 'v.'), ('ADJ', 'adj.'), ('adjective', 'adj.'), ('代词', 'pron.'), ('noun', 'n.'), ('prop. n.', 'prop. n.')]:
            self.assertEqual(short_part_of_speech(value), expected)
        with self.assertRaises(RuntimeError):
            short_part_of_speech('不确定的长解释')

    def test_context_includes_same_paragraph_sentences_and_unicode_offsets(self):
        text = '🚙 Her car stopped. Her sister waved. Then she left.'
        self.book['paragraphs'][3]['text'] = text
        selected = sentence_window(self.book, 3, text.index('Her', 3), radius=10)
        self.assertEqual(selected['sentence'], 'Her sister waved. ')
        self.assertEqual(selected['previousSentences'][-1], '🚙 Her car stopped. ')
        self.assertEqual(selected['followingSentences'][0], 'Then she left.')


if __name__ == '__main__':
    unittest.main()
