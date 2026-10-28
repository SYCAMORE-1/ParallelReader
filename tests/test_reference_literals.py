import unittest
from unittest.mock import patch

from reader.reproof import review_existing
from reader.translation import translate
from reader.translation_rules import finish_translation, is_literal_reference, translation_plan
from test_reader import queue_book


SOURCE = ('“Nuy Dap Ranh,” said Webb, barely above a whisper. At the sound of the Oriental words, '
          'Alex’s eyes snapped back up at David. “That’s the key, isn’t it?” continued Webb. '
          '“Nuy Dap Ranh—Snake Lady.”')


class ReferenceLiteralTests(unittest.TestCase):
    def test_bulleted_domains_and_social_handles_keep_exact_source_spelling(self):
        for source in ['• 100deadlyskills.com.', '/TouchstoneBooks @TouchstoneBooks @TouchstoneBooks',
                       '1. example.org.', '• www.example.com.', 'https://example.com/resource?key=value']:
            with self.subTest(source=source):
                self.assertTrue(is_literal_reference(source))
                result = finish_translation(translation_plan(source, []), [])
                self.assertEqual(result['text'], source)
                self.assertEqual(''.join(result['sourceSentences']), source)
                self.assertEqual(result['sourceSentences'], result['targetSentences'])

    def test_resource_lines_bypass_both_models_and_reproof_without_retry(self):
        settings = {'reviewEnabled': True, 'reviewModel': 'review', 'baseUrl': 'http://unused', 'model': 'primary',
                    'targetLanguage': '简体中文', 'reviewBaseUrl': 'http://unused'}
        book = queue_book()
        for source in ['• 100deadlyskills.com.', '/TouchstoneBooks @TouchstoneBooks @TouchstoneBooks']:
            book['paragraphs'][0]['text'] = source
            with patch('reader.translation.request_json') as request:
                self.assertEqual(translate(book, 0, settings)['text'], source)
                result = review_existing(book, 0, settings, {'text': '旧的错误结果', 'detail': None}, None, None)
                self.assertEqual(result['text'], source)
                request.assert_not_called()

    def test_prose_with_urls_or_handles_still_requires_translation(self):
        for source in ['• Visit 100deadlyskills.com.', 'Follow @TouchstoneBooks for news.',
                       'Visit /TouchstoneBooks for more.', '• The operator waited.', 'The view?']:
            self.assertFalse(is_literal_reference(source), source)
            plan = translation_plan(source, [])
            with self.assertRaisesRegex(RuntimeError, '原样复制'):
                finish_translation(plan, [piece['source'] for piece in plan['pieces'] if not piece['fixed']])

    def test_explicit_foreign_gloss_fixes_only_repeated_foreign_words(self):
        plan = translation_plan(SOURCE, ['Webb', 'Alex', 'David'])
        fixed = [piece['source'] for piece in plan['pieces'] if piece['fixed']]
        self.assertEqual(fixed.count('Nuy Dap Ranh'), 2)
        values = ['，__PERSON_000__低声说道，声音几乎成了耳语。',
                  '听到这句东方语言，__PERSON_001__猛地抬眼望向__PERSON_002__。',
                  '这就是关键，对吧？', '__PERSON_000__接着说。', '——蛇女。']
        self.assertEqual(len([piece for piece in plan['pieces'] if not piece['fixed']]), len(values))
        result = finish_translation(plan, values)
        self.assertEqual(result['text'].count('Nuy Dap Ranh'), 2)
        self.assertTrue(result['text'].startswith('“Nuy Dap Ranh，”Webb'))
        self.assertIn('蛇女', result['text'])
        self.assertEqual(result['protectedNames'], ['Alex', 'David', 'Webb'])
        self.assertEqual(''.join(result['sourceSentences']), SOURCE)
        self.assertEqual(len(result['sourceSentences']), len(result['targetSentences']))
        self.assertEqual(result['text'].count('“'), 3)
        self.assertEqual(result['text'].count('”'), 3)
        with self.assertRaisesRegex(RuntimeError, '原样复制'):
            finish_translation(plan, [piece['source'] for piece in plan['pieces'] if not piece['fixed']])

    def test_selection_uses_full_paragraph_evidence_for_foreign_words(self):
        selected = SOURCE.split(' At the sound')[0]
        plan = translation_plan(selected, ['Webb'], reference=SOURCE)
        self.assertEqual([p['source'] for p in plan['pieces'] if not p['fixed']], [' said __PERSON_000__, barely above a whisper.'])
        result = finish_translation(plan, ['__PERSON_000__几乎是在耳语。'])
        self.assertIn('Nuy Dap Ranh', result['text'])
        self.assertEqual(''.join(result['sourceSentences']), selected)

    def test_ordinary_dialogue_and_unconfirmed_foreign_phrases_are_not_fixed(self):
        for source in [SOURCE.replace('Oriental words', 'familiar greeting'),
                       'The foreign words surprised him. “Nuy Dap Ranh—Snake Lady.”',
                       '“Come Here,” he said. “Come Here—Walk This Way.”']:
            plan = translation_plan(source, [])
            self.assertFalse(any(p['fixed'] and p['source'] in {'Nuy Dap Ranh', 'Come Here'} for p in plan['pieces']))
