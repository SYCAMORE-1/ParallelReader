import json
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from reader.language import LanguageTools
from reader.storage import Store
from reader.translation import profile
from reader.translation_rules import finish_translation, is_literal_reference, nickname_hints, person_names, protect_names, translation_plan
from test_reader import queue_book


class TranslationRulesTests(unittest.TestCase):
    def test_person_names_do_not_split_social_handles_into_adjacent_tokens(self):
        source = 'Morgan Lane thanked Jamie Fox. Follow @morgan_lane.'
        names = ['Morgan Lane', 'Jamie Fox', 'Morgan', 'Lane']
        masked, tokens = protect_names(source, names)
        self.assertIn('@morgan_lane', masked)
        self.assertEqual(set(tokens.values()), {'Morgan Lane', 'Jamie Fox'})
        plan = translation_plan(source, names)
        result = finish_translation(plan, ['__PERSON_000__向__PERSON_001__致谢。', '请关注@morgan_lane。'])
        self.assertEqual(result['text'], 'Morgan Lane向Jamie Fox致谢。请关注@morgan_lane。')
        self.assertEqual(result['protectedNames'], ['Jamie Fox', 'Morgan Lane'])

    def test_redacted_locations_are_preserved_without_inventing_text(self):
        for source in ['xxxxx xxxxxxx xxxxxxxxxxx xxxxx', 'xxx xxxx, xxxxxxxx', 'XXXXXXXXXX', '[XXX]', 'XX', 'xxxxx xx xxx xx xxxx.']:
            with self.subTest(source=source):
                self.assertTrue(is_literal_reference(source))
                result = finish_translation(translation_plan(source, []), [])
                self.assertEqual(result['text'], source)
                self.assertEqual(''.join(result['sourceSentences']), source)
                self.assertEqual(result['sourceSentences'], result['targetSentences'])

    def test_redactions_inside_prose_are_fixed_but_surrounding_words_are_translated(self):
        source = 'They served with XXXXXXXXXX advisors.'
        self.assertFalse(is_literal_reference(source))
        plan = translation_plan(source, [])
        self.assertEqual([p['source'] for p in plan['pieces'] if not p['fixed']], ['They served with ', ' advisors.'])
        result = finish_translation(plan, ['他们曾与', '顾问共事。'])
        self.assertEqual(result['text'], '他们曾与XXXXXXXXXX顾问共事。')
        self.assertEqual(len(result['alignment']), 1)
        self.assertEqual(result['sourceSentences'], [source])
        with self.assertRaisesRegex(RuntimeError, '原样复制'):
            finish_translation(plan, ['They served with ', ' advisors.'])
        with self.assertRaisesRegex(RuntimeError, '额外输出了遮盖符号'):
            finish_translation(plan, ['他们曾与XXXXXXXX', '顾问共事。'])

    def test_consecutive_redactions_keep_two_letter_blocks_and_spacing(self):
        source = 'He commanded the unit, xxxxx xx xxxx xx xxx.'
        plan = translation_plan(source, [])
        self.assertEqual([p['source'] for p in plan['pieces'] if not p['fixed']], ['He commanded the unit, '])
        self.assertEqual(finish_translation(plan, ['他指挥该部队，'])['text'], '他指挥该部队，xxxxx xx xxxx xx xxx.')

    def test_real_words_and_variables_are_not_redactions(self):
        for source in ['X-ray', 'Exxon', 'x x', 'The operator waited.', 'xxxxx is missing.']:
            with self.subTest(source=source):
                self.assertFalse(is_literal_reference(source))
        with self.assertRaisesRegex(RuntimeError, '原样复制'):
            finish_translation(translation_plan('The operator waited.', []), ['The operator waited.'])

    def test_inches_do_not_open_dialogue_but_numeric_dialogue_still_closes(self):
        source = '0.1 MIL equals 0.36" at 100 yards. Do not become a sniper.'
        plan = translation_plan(source, [])
        self.assertFalse(any(piece['speech'] for piece in plan['pieces']))
        result = finish_translation(plan, ['0.1密位在100码处等于0.36英寸。', '那就别当狙击手了。'])
        self.assertEqual(result['text'], '0.1密位在100码处等于0.36英寸。那就别当狙击手了。')
        result = finish_translation(translation_plan('"He is 20" she said.', []), ['他二十岁', '她说。'])
        self.assertEqual(result['text'], '“他二十岁”她说。')

    def test_spaced_nested_closing_quote_is_not_an_empty_translation_sentence(self):
        source = '“He said, ‘This is what happens to thieves.’ ”'
        plan = translation_plan(source, [])
        self.assertEqual(plan['source'], [source])
        result = finish_translation(plan, ['他说，小偷就是这个下场。'])
        self.assertEqual(result['text'], '“他说，小偷就是这个下场。”')
        self.assertEqual(len(result['alignment']), 1)
        self.assertEqual(''.join(result['sourceSentences']), source)
        inner = finish_translation(plan, ['他说，‘这就是小偷的下场。’'])
        self.assertEqual(inner['text'], '“他说，‘这就是小偷的下场。’”')
        title = finish_translation(translation_plan('“ ‘The Most Dangerous Game.’ ”', []), ['《最危险的游戏》。'])
        self.assertEqual(title['text'], '“《最危险的游戏》。”')

    def test_standalone_initials_are_not_protected_inside_country_abbreviations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            book = queue_book()
            book['paragraphs'][3]['text'] = 'S. Rainsford met W. J. Jeffery in the U.S. military.'
            response = {'choices': [{'message': {'content': json.dumps({'names': ['S. Rainsford', 'W. J. Jeffery']})}}]}
            try:
                names = person_names(book, 3, store.settings(), lambda *args: response, store)
                self.assertTrue({'S. Rainsford', 'W. J. Jeffery'} <= set(names))
                self.assertFalse({'S.', 'W.', 'J.'} & set(names))
                # Previously persisted chapter name lists must be safe too.
                with store.lock, store.db:
                    store.db.execute('UPDATE language_cache SET value=?', (json.dumps(names + ['S.', 'W.', 'J.']),))
                cleaned = person_names(book, 3, store.settings(), lambda *args: self.fail('reuse the cached name table'), store)
                masked, _ = protect_names(book['paragraphs'][3]['text'], cleaned)
                self.assertIn('U.S. military', masked)
                self.assertNotIn('S.', cleaned)
            finally:
                store.db.close()

    def test_direct_address_supplements_missing_person_nickname(self):
        source = '“Liz, what are you doing here?” Morgan asked.'
        self.assertIn('Liz', nickname_hints(source))
        self.assertNotIn('Hey', nickname_hints('“Hey, what are you doing?”'))
        self.assertNotIn('No', nickname_hints('“No, what are you doing?”'))

    def test_different_chapters_recognize_names_in_parallel_but_same_chapter_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            book = queue_book()
            for paragraph in book['paragraphs']:
                paragraph['chapter'] = 0 if paragraph['id'] < 5 else 1
                paragraph['text'] = 'Morgan waited.' if paragraph['chapter'] == 0 else 'Jamie arrived.'
            entered, release = threading.Barrier(2), threading.Event()
            calls, guard = [], threading.Lock()
            def request(url, payload, key):
                data = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])
                name = 'Morgan' if 'Morgan' in data['chapter_text'] else 'Jamie'
                with guard:
                    calls.append(name)
                entered.wait(2)  # A global model lock would time out here.
                release.wait(3)
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': [name]})}}]}
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    first = pool.submit(person_names, book, 0, store.settings(), request, store)
                    other = pool.submit(person_names, book, 5, store.settings(), request, store)
                    same = pool.submit(person_names, book, 1, store.settings(), request, store)
                    release.set()
                    self.assertEqual(first.result(4), ['Morgan'])
                    self.assertEqual(other.result(4), ['Jamie'])
                    self.assertEqual(same.result(4), ['Morgan'])
                self.assertCountEqual(calls, ['Morgan', 'Jamie'])
            finally:
                release.set()
                store.db.close()

    def test_source_explicit_family_nickname_is_protected_when_model_misses_it(self):
        source = '“The package is for Birch,” Jamie explained, using the nickname for his father-in-law.'
        self.assertEqual(nickname_hints(source), {'Birch', 'Jamie'})
        self.assertEqual(nickname_hints('The car had a nickname.'), set())
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book(); book['paragraphs'][3]['text'] = source
            request = lambda *args: {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"names":["Jamie"]}'}}]}
            self.assertIn('Birch', person_names(book, 3, store.settings(), request, store))
            store.db.close()
    def test_names_preserve_source_spelling_and_possessive_boundaries(self):
        masked, tokens = protect_names("Morgan met Jamie. MORGAN took Morgan’s bag; Greece is elsewhere.", ['Morgan', 'Jamie'])
        self.assertIn('Greece', masked)
        self.assertNotIn('Morgan', masked)
        self.assertEqual(set(tokens.values()), {'Morgan', 'Jamie', 'MORGAN'})
        self.assertEqual(masked.count('__PERSON_000__'), 2)

    def test_missing_or_duplicated_name_is_rejected(self):
        plan = translation_plan('Morgan waited.', ['Morgan'])
        for values in [['里斯等着。'], ['__PERSON_000____PERSON_000__等着。']]:
            with self.assertRaises(RuntimeError):
                finish_translation(plan, values)
        result = finish_translation(plan, ['__PERSON_000__等着。'])
        self.assertEqual(result['text'], 'Morgan等着。')
        self.assertEqual(finish_translation(plan, ['MORGAN等着。'])['text'], 'Morgan等着。')

    def test_dialogue_quotes_restored_even_if_model_omits_or_changes_them(self):
        for source in ['“It’s beautiful.”', '"It is beautiful."']:
            result = finish_translation(translation_plan(source, []), ['「真美。」'])
            self.assertEqual(result['text'], '“真美。”')
        result = finish_translation(translation_plan('“The view?”', []), ['景色。'])
        self.assertEqual(result['text'], '“景色？”')

    def test_copied_english_is_not_accepted_as_a_translation(self):
        with self.assertRaises(RuntimeError):
            finish_translation(translation_plan('“The view?”', []), ['The view？'])

    def test_only_exact_repeated_product_model_header_is_literal(self):
        from reader.translation_rules import is_literal_reference
        self.assertTrue(is_literal_reference('Aimpoint Micro: Aimpoint Micro T-2.'))
        for text in ['Aimpoint Micro: It weighs 3 ounces.', 'Aimpoint Micro: Aimpoint Micro is popular in 2026.',
                     'The view: The view was clear in 2026.', 'Chapter 2: Chapter 2']:
            self.assertFalse(is_literal_reference(text), text)

    def test_letter_spelling_is_fixed_without_exempting_hyphenated_prose(self):
        from reader.translation_rules import is_literal_reference
        for text in ['C-o-n-k,', 'F-B-I!', 'A-b-c', 'C - o - n - k,']:
            self.assertTrue(is_literal_reference(text), text)
        for text in ['mother-in-law', 'well-being', 'C-o-n-k is the spelling.', 'Who is C-o-n-k?', 'A-B']:
            self.assertFalse(is_literal_reference(text), text)
        source = '“C-o-n-k,” Alex said.'
        plan = translation_plan(source, ['Alex'])
        self.assertEqual(len([piece for piece in plan['pieces'] if not piece['fixed']]), 1)
        result = finish_translation(plan, ['__PERSON_000__说。'])
        self.assertEqual(result['text'], '“C-o-n-k,”Alex说。')
        self.assertEqual(''.join(result['sourceSentences']), source)

    def test_original_name_with_chinese_gloss_is_not_misclassified_as_copied_english(self):
        for source, target in [('Accuracy International:', 'Accuracy International（精密国际）：'),
                               ('Project Delta', 'Project Delta（三角洲计划）'),
                               ('Suka, blyat!', 'Suka, blyat（俄语脏话：婊子养的，操）！')]:
            with self.subTest(source=source):
                result = finish_translation(translation_plan(source, []), [target])
                self.assertEqual(result['text'], target)
                with self.assertRaises(RuntimeError):
                    finish_translation(translation_plan(source, []), [source])

    def test_names_spanning_sentence_punctuation_keep_one_global_token(self):
        source = 'M.A.R. D’Amacourt Vice-Président Comptes à L’Étranger et Devises'
        plan = translation_plan(source, ['M.A.R. D’Amacourt', 'M.A.R.', 'D’Amacourt'])
        self.assertEqual(''.join(plan['source']), source)
        self.assertEqual(len(plan['source']), 1)
        self.assertEqual(list(plan['tokens'].values()), ['M.A.R. D’Amacourt'])
        self.assertIn('__PERSON_000__', plan['pieces'][0]['source'])
        result = finish_translation(plan, ['__PERSON_000__副行长，负责海外账户与外汇'])
        self.assertIn('M.A.R. D’Amacourt', result['text'])

    def test_name_only_dialogue_is_preserved_without_model_translation(self):
        plan = translation_plan('“Carlos?”', ['Carlos'])
        self.assertTrue(all(piece['fixed'] for piece in plan['pieces']))
        self.assertEqual(finish_translation(plan, [])['text'], '“Carlos?”')

    def test_cached_false_names_are_filtered_and_particle_surnames_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            book['paragraphs'][3]['text'] = 'Philippe d’Anjou left. Again he stopped. “D’Anjou?” Villiers asked.'
            settings = store.settings()
            with unittest.mock.patch.object(store, 'language_result', return_value=['Philippe', 'Again', 'Villiers', 'General', 'St.']):
                names = person_names(book, 3, settings, lambda *args: self.fail('cached names should not request a model'), store)
            self.assertNotIn('Again', names)
            self.assertNotIn('General', names)
            self.assertNotIn('St.', names)
            self.assertIn('Philippe d’Anjou', names)
            self.assertIn('d’Anjou', names)
            plan = translation_plan(book['paragraphs'][3]['text'], names)
            self.assertNotIn('again', {name.lower() for name in plan['tokens'].values()})
            self.assertIn('D’Anjou', plan['tokens'].values())
            store.db.close()

    def test_mixed_dialogue_and_attribution_share_one_sentence_mapping(self):
        source = '“Wait,” Morgan said. Then Jamie left.'
        plan = translation_plan(source, ['Morgan', 'Jamie'])
        self.assertEqual(len([p for p in plan['pieces'] if not p['fixed']]), 3)
        result = finish_translation(plan, ['等一下。', '__PERSON_000__说。', '然后__PERSON_001__离开了。'])
        self.assertEqual(result['text'], '“等一下，”Morgan说。然后Jamie离开了。')
        self.assertEqual(result['alignment'], [{'source': [0], 'target': [0]}, {'source': [1], 'target': [1]}])
        self.assertEqual(''.join(result['sourceSentences']), source)

    def test_dialogue_across_sentence_boundaries_stays_balanced(self):
        source = '“Stop. Go now!” Morgan shouted.'
        plan = translation_plan(source, ['Morgan'])
        result = finish_translation(plan, ['停下。', '现在走！', '__PERSON_000__喊道。'])
        self.assertEqual(result['text'], '“停下。现在走！” Morgan喊道。')
        self.assertEqual(result['text'].count('“'), 1)
        self.assertEqual(result['text'].count('”'), 1)

    def test_structural_alignment_survives_restart_without_alignment_model(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            book = queue_book(); book['paragraphs'][3]['text'] = '“Wait,” Morgan said. Then he left.'
            detail = finish_translation(translation_plan(book['paragraphs'][3]['text'], ['Morgan']), ['等等，', '__PERSON_000__说。', '然后他走了。'])
            store.cache(book['id'], 3, profile(store.settings()), detail['text'], detail=detail)
            store.db.close()
            reopened = Store(directory)
            tools = LanguageTools(reopened, request_fn=lambda *args: self.fail('new translations must not need an alignment model'))
            view = tools.align(book, 3, detail['text'], reopened.settings())
            self.assertEqual(view['alignmentMethod'], 'translation-slots')
            self.assertEqual(view['alignment'], detail['alignment'])
            reopened.db.close()

    def test_chapter_person_recognition_is_validated_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            book['paragraphs'][3]['text'] = 'Morgan met Jamie in Moscow. Morgan spoke.'
            calls = []
            def request(*args):
                calls.append(args)
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': ['Morgan', 'Jamie']})}}]}
            first = person_names(book, 3, store.settings(), request, store)
            self.assertEqual(set(first), {'Morgan', 'Jamie'})
            self.assertEqual(person_names(book, 4, store.settings(), request, store), first)
            self.assertEqual(len(calls), 1)
            store.db.close()

    def test_long_chapter_names_cover_the_middle_and_can_exceed_64_in_total(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            expected = ['Writer' + chr(97 + i // 26) + chr(97 + i % 26) for i in range(90)]
            book['paragraphs'] = [{'id': i, 'chapter': 0, 'text': name + ' helped. ' + 'background words ' * 45} for i, name in enumerate(expected)]
            references = []
            def request(url, payload, key):
                reference = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])['chapter_text']
                references.append(reference)
                names = re.findall(r'\bWriter[a-z]{2}\b', reference)
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': names})}}]}
            try:
                names = person_names(book, 0, store.settings(), request, store)
                chapter = '\n'.join(p['text'] for p in book['paragraphs'])
                self.assertGreater(len(chapter), 40000)
                self.assertEqual(''.join(references), chapter)
                self.assertLessEqual(max(map(len, references)), 4000)
                self.assertEqual(set(names), set(expected))
                self.assertEqual(person_names(book, 1, store.settings(), lambda *a: self.fail('whole chapter cache must be reused'), store), names)
            finally:
                store.db.close()

    def test_dense_name_list_at_schema_limit_is_split_instead_of_cached_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            expected = ['Given' + chr(97 + i // 16) + ' Family' + chr(97 + i % 16) for i in range(120)]
            book['paragraphs'] = [{'id': 0, 'chapter': 0, 'text': ', '.join(expected) + '.'}]
            lengths = []
            def request(url, payload, key):
                reference = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])['chapter_text']
                names = re.findall(r'\bGiven[a-z] Family[a-z]\b', reference); lengths.append(len(names))
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': names[:64]})}}]}
            try:
                self.assertTrue(set(expected) <= set(person_names(book, 0, store.settings(), request, store)))
                self.assertGreater(lengths[0], 64)
                self.assertTrue(any(n < 64 for n in lengths))
            finally:
                store.db.close()

    def test_name_batches_resume_after_outage_without_repeating_successful_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            book['paragraphs'] = [{'id': 0, 'chapter': 0, 'text': 'Alpha Warren helped. ' + 'quiet words ' * 220},
                                  {'id': 1, 'chapter': 0, 'text': 'Bella Stone helped. ' + 'quiet words ' * 220}]
            first_calls, resumed_calls = [], []
            def request(url, payload, key):
                reference = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])['chapter_text']; first_calls.append(reference)
                if 'Bella' in reference:
                    raise RuntimeError('模型暂时离线')
                return {'choices': [{'message': {'content': '{"names":["Alpha Warren"]}'}}]}
            def resume(url, payload, key):
                reference = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])['chapter_text']; resumed_calls.append(reference)
                self.assertNotIn('Alpha', reference)
                return {'choices': [{'message': {'content': '{"names":["Bella Stone"]}'}}]}
            try:
                with self.assertRaisesRegex(RuntimeError, '离线'):
                    person_names(book, 0, store.settings(), request, store)
                store.db.close()
                store = Store(directory)
                names = person_names(book, 1, store.settings(), resume, store)
                self.assertTrue({'Alpha Warren', 'Bella Stone'} <= set(names))
                self.assertEqual((len(first_calls), len(resumed_calls)), (2, 1))
            finally:
                store.db.close()

    def test_truncated_name_reply_is_not_saved_as_a_complete_name_table(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            book['paragraphs'] = [{'id': 0, 'chapter': 0, 'text': 'Morgan waited.'}]
            try:
                with self.assertRaisesRegex(RuntimeError, '人名识别输出不完整'):
                    person_names(book, 0, store.settings(), lambda *a: {'choices': [{'finish_reason': 'length', 'message': {'content': '{"names":["Morgan"]}'}}]}, store)
                self.assertEqual(store.db.execute('select count(*) from language_cache').fetchone()[0], 0)
            finally:
                store.db.close()

    def test_name_protection_excludes_titles_so_they_can_be_translated_and_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); book = queue_book()
            source = 'Deputy Director Parker spoke to Dr. Port and Colonel Taylor.'
            book['paragraphs'][3]['text'] = source
            def request(*args):
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'names': ['Deputy Director Parker', 'Dr. Port', 'Colonel Taylor']})}}]}
            names = person_names(book, 3, store.settings(), request, store)
            self.assertEqual(set(names), {'Parker', 'Port', 'Taylor'})
            plan = translation_plan(source, names)
            self.assertIn('Deputy Director __PERSON_000__', plan['masked'])
            self.assertIn('Dr. __PERSON_001__', plan['masked'])
            text = '__PERSON_000__副局长与__PERSON_001__医生和__PERSON_002__上校交谈。'
            self.assertEqual(finish_translation(plan, [text])['text'], 'Parker副局长与Port医生和Taylor上校交谈。')
            self.assertEqual(person_names(book, 3, store.settings(), lambda *args: self.fail('cached recognition must be reused'), store), names)
            store.db.close()


if __name__ == '__main__':
    unittest.main()
