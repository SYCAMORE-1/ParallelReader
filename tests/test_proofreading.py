import json
import re
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from reader.storage import Store
from reader.translation import Translator, ModelHTTPError, draft_settings, profile, translate, translation_error
from reader.translation_rules import phonetic_code_gloss
from server import make_server, validate_settings
from test_reader import queue_book, until


def reply(values):
    return {'choices': [{'message': {'content': json.dumps({'translations': values}, ensure_ascii=False)}}]}


class ProofreadingTests(unittest.TestCase):
    def test_name_repair_does_not_block_later_cross_slot_repetition_repair(self):
        self.book['paragraphs'][4]['text'] = ('What if Morgan were not elected president? '
                                             'These were variables with which the country must contend. '
                                             'Could they not see what was coming?')
        targets = ['要是__PERSON_000__未能当选总统呢？', '这些都是变数，是这个国家必须面对的变数。', '难道他们看不出即将发生什么吗？']
        unit = lambda text: {'choices': [{'message': {'content': json.dumps({'translation': text}, ensure_ascii=False)}}]}
        outputs = [reply(targets), reply(['要是他没能当选总统呢？', targets[0], targets[1]]),
                   unit(targets[0]), unit(targets[1]), unit(targets[1]), unit(targets[2])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(result['targetSentences'], ['要是Morgan未能当选总统呢？', *targets[1:]])
        self.assertEqual(result['text'].count('这些都是变数'), 1)

    def test_repeated_unit_repair_is_checked_against_neighbor_before_acceptance(self):
        self.book['paragraphs'][4]['text'] = ('He switched off the device and led his team to the ambush site. '
                                             'The machine needed twenty minutes to recover enough power for another shot.')
        targets = ['他关闭了战术电磁脉冲装置，带领七人小队前往山上的伏击地点。',
                   '装置需要二十分钟才能恢复并储存足够的能量，以便再次发射。']
        copied = ''.join(targets)
        unit = lambda text: {'choices': [{'message': {'content': json.dumps({'translation': text}, ensure_ascii=False)}}]}
        outputs = [reply(targets), reply([copied, targets[1]]), unit(copied), unit(targets[0])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['targetSentences'], targets)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args.args[1]['response_format']['type'], 'json_object')
        self.assertIn('other_units_do_not_translate_or_repeat', request.call_args.args[1]['messages'][0]['content'])
        self.assertTrue(all(call.args[1]['model'] == self.settings['reviewModel'] for call in request.call_args_list[1:]))

    def test_persistent_neighbor_copy_stays_bounded_and_does_not_replace_draft(self):
        self.book['paragraphs'][4]['text'] = ('He switched off the device and led his team to the ambush site. '
                                             'The machine needed twenty minutes to recover enough power for another shot.')
        targets = ['他关闭了战术电磁脉冲装置，带领七人小队前往山上的伏击地点。',
                   '装置需要二十分钟才能恢复并储存足够的能量，以便再次发射。']
        copied = ''.join(targets)
        invalid = {'choices': [{'message': {'content': json.dumps({'translation': copied}, ensure_ascii=False)}}]}
        with patch('reader.translation.request_json', side_effect=[reply(targets), reply([copied, targets[1]]), invalid, invalid]) as request:
            with self.assertRaisesRegex(RuntimeError, '重复'):
                translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(self.store.draft(self.book['id'], 4, profile(draft_settings(self.settings)))['text'], ''.join(targets))
        self.assertIsNone(self.store.translation(self.book['id'], 4, profile(self.settings)))

    def test_haho_initial_and_review_both_reject_halo_expansion(self):
        self.book['paragraphs'][4]['text'] = 'They made a HAHO jump. The team waited.'
        wrong, correct = '他们进行了高跳低开（HAHO）跳伞。', '他们进行了高跳高开（HAHO）跳伞。'
        outputs = [reply([wrong, '小队在等候。']),
                   {'choices': [{'message': {'content': json.dumps({'translation': correct}, ensure_ascii=False)}}]},
                   reply([wrong, '小队耐心等候。']),
                   {'choices': [{'message': {'content': json.dumps({'translation': correct}, ensure_ascii=False)}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(result['targetSentences'], [correct, '小队耐心等候。'])
        self.assertEqual(request.call_args_list[1].args[1]['model'], self.settings['model'])
        self.assertEqual(request.call_args_list[3].args[1]['model'], self.settings['reviewModel'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.book = queue_book()
        self.book['paragraphs'][4]['text'] = 'Director Morgan waited. "The view?"'
        self.store.add(self.book)
        self.settings = self.store.settings() | {'baseUrl': 'https://translation.example.invalid/v1', 'model': 'translation-model',
                         'reviewBaseUrl': 'https://review.example.invalid/v1', 'reviewModel': 'review-model',
                         'apiKey': 'primary-secret', 'reviewApiKey': 'review-secret'}
        self.names = patch('reader.translation.person_names', return_value=['Morgan'])
        self.names.start()

    def tearDown(self):
        self.names.stop()
        self.store.db.close()
        self.temp.cleanup()

    def test_redacted_source_bypasses_both_models_and_stale_hallucinated_draft(self):
        source = 'xxxxx xxxxxxx xxxxxxxxxxx xxxxx'
        self.book['paragraphs'][4]['text'] = source
        primary_profile = profile(draft_settings(self.settings))
        self.store.cache_draft(self.book['id'], 4, primary_profile, {'text': '不应存在的人名'})
        with patch('reader.translation.request_json') as request, patch('reader.translation.person_names') as names:
            for review in [False, True]:
                result = translate(self.book, 4, self.settings | {'reviewEnabled': review}, self.store)
                self.assertEqual(result['text'], source)
                self.assertEqual(result['targetSentences'], [source])
            request.assert_not_called()
            names.assert_not_called()

    def test_reviewer_cannot_copy_old_redaction_into_a_translatable_slot(self):
        self.book['paragraphs'][4]['text'] = 'They served with XXXXXXXXXX advisors.'
        self.store.cache_draft(self.book['id'], 4, profile(draft_settings(self.settings)), {'text': '他们曾与XXXXXXXX顾问共事。'})
        outputs = [reply(['他们曾与XXXXXXXX', '顾问共事。']), {'choices': [{'message': {'content': '{"translation":"他们曾与"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['text'], '他们曾与XXXXXXXXXX顾问共事。')
        self.assertEqual(request.call_count, 2)

    def test_primary_then_reviewer_preserves_names_dialogue_and_alignment(self):
        outputs = [reply(['__PERSON_000__局长等着。', '景色怎么样？']), reply(['__PERSON_000__局长等着。', '景色？'])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        calls = request.call_args_list
        self.assertEqual([call.args[1]['model'] for call in calls], ['translation-model', 'review-model'])
        self.assertEqual([call.args[2] for call in calls], ['primary-secret', 'review-secret'])
        self.assertTrue(calls[1].args[0].startswith(self.settings['reviewBaseUrl']))
        self.assertEqual(result['text'], 'Morgan局长等着。“景色？”')
        self.assertEqual(len(result['alignment']), 2)
        self.assertEqual(result['proofreading']['model'], self.settings['reviewModel'])
        prompt = calls[1].args[1]['messages']
        self.assertIn('Paragraph 3.', prompt[0]['content'])
        self.assertIn('Paragraph 5.', prompt[0]['content'])
        self.assertIn('景色怎么样', prompt[-1]['content'])
        draft = self.store.draft(self.book['id'], 4, profile(draft_settings(self.settings)))
        self.assertIn('景色怎么样', draft['text'])
        self.assertIsNone(self.store.translation(self.book['id'], 4, profile(self.settings)))

    def test_reviewer_outage_retains_draft_across_restart_without_primary_rerun(self):
        with patch('reader.translation.request_json', side_effect=[reply(['__PERSON_000__局长等着。', '景色？']), RuntimeError('无法连接模型')]):
            with self.assertRaisesRegex(RuntimeError, '初译已保留'):
                translate(self.book, 4, self.settings, self.store)
        self.store.db.close()
        self.store = Store(self.temp.name)
        with patch('reader.translation.request_json', return_value=reply(['__PERSON_000__局长等着。', '景色？'])) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1]['model'], self.settings['reviewModel'])
        self.assertIn('Morgan', result['text'])

    def test_legacy_profile_and_saved_translation_reused_as_draft_only(self):
        legacy = profile(draft_settings(self.settings))
        self.assertNotEqual(profile(self.settings), legacy)
        self.store.cache(self.book['id'], 4, legacy, 'Morgan局长等着。“景色怎么样？”')
        with patch('reader.translation.request_json', return_value=reply(['__PERSON_000__局长等着。', '景色？'])) as request:
            translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(self.store.translation(self.book['id'], 4, profile(self.settings)))
        self.assertIn('怎么样', self.store.translation(self.book['id'], 4, legacy))
        self.assertNotEqual(profile(self.settings | {'reviewModel': 'another-reviewer'}), profile(self.settings))

    def test_invalid_review_does_not_silently_fall_back_to_initial_translation(self):
        invalid_unit = {'choices': [{'message': {'content': '{"translation":"他等着。"}'}}]}
        invalid_slots = {'choices': [{'message': {'content': '{"segments":["__PERSON_000__","等着。"]}'}}]}
        outputs = [reply(['__PERSON_000__局长等着。', '景色？']), reply(['他等着。', '景色？']), invalid_unit, invalid_unit, invalid_slots, invalid_slots]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            with self.assertRaisesRegex(RuntimeError, '校对未完成') as failure:
                translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(translation_error(str(failure.exception))['errorCode'], 'proofreading')
        self.assertIsNone(self.store.translation(self.book['id'], 4, profile(self.settings)))
        self.assertIsNotNone(self.store.draft(self.book['id'], 4, profile(draft_settings(self.settings))))

    def test_program_inserts_name_when_model_repeatedly_replaces_it_with_pronoun(self):
        self.book['paragraphs'][4]['text'] = 'It happened before Morgan was born.'
        lost = {'choices': [{'message': {'content': '{"translation":"事情发生在他出生之前。"}'}}]}
        bad_gap = {'choices': [{'message': {'content': '{"segments":["事情发生在他","出生之前。"]}'}}]}
        outputs = [reply(['事情发生在他出生之前。']), lost, lost, bad_gap, reply(['事情发生在__PERSON_000__出生之前。'])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['text'], '事情发生在Morgan出生之前。')
        self.assertEqual(len(result['alignment']), 1)
        self.assertEqual(request.call_args_list[3].args[1]['response_format']['json_schema']['name'], 'name_slot_translation')
        self.assertEqual(request.call_count, 5)
        self.assertNotIn('他Morgan', result['text'])

    def test_repeated_product_label_and_model_are_fixed_while_description_is_reviewed(self):
        self.book['paragraphs'][4]['text'] = 'Aimpoint Micro: Aimpoint Micro T-2. It weighs three ounces.'
        outputs = [reply(['它重三盎司。']), reply(['它的重量只有三盎司。'])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['targetSentences'][0], 'Aimpoint Micro: Aimpoint Micro T-2. ')
        self.assertEqual(result['targetSentences'][1], '它的重量只有三盎司。')
        self.assertEqual(len(result['alignment']), 2)

    def test_spelled_dialogue_does_not_trigger_futile_unit_retries(self):
        self.book['paragraphs'][4]['text'] = '“C-o-n-k,” Morgan said.'
        outputs = [reply(['__PERSON_000__说。']), reply(['__PERSON_000__接话道。'])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['text'], '“C-o-n-k,”Morgan接话道。')
        self.assertEqual(result['protectedNames'], ['Morgan'])

    def test_schema_server_error_retries_compact_json_without_skipping_review(self):
        outputs = [reply(['__PERSON_000__局长等着。', '景色？']), reply(['他等着。', '景色？']),
                   ModelHTTPError(500, '模型接口 HTTP 500'), {'choices': [{'message': {'content': '{"translation":"__PERSON_000__局长在等候。"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args.args[1]['response_format']['type'], 'json_object')
        self.assertEqual(request.call_args.args[1]['model'], self.settings['reviewModel'])
        self.assertIn('Morgan局长在等候', result['text'])

    def test_military_operator_sense_must_agree_with_reviewed_translation(self):
        self.book['paragraphs'][4]['text'] = 'Two special operations operators entered the room.'
        outputs = [reply(['两名特种作战操作员走进房间。']),
                   {'choices': [{'message': {'content': '{"operator_senses":["combat"],"translations":["两名操作员走进房间。"]}'}}]},
                   {'choices': [{'message': {'content': '{"translation":"两名作战队员走进房间。"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['text'], '两名作战队员走进房间。')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.args[1]['model'], self.settings['reviewModel'])
        self.assertEqual(result['terminology']['operators'], [{'unit': 0, 'sense': 'combat'}])

    def test_dog_handler_uses_previous_context_and_repairs_intelligence_meaning(self):
        self.book['paragraphs'][3]['text'] = 'A second dog charged at him.'
        self.book['paragraphs'][4]['text'] = 'Where is his handler?'
        reviewed = {'choices': [{'message': {'content': json.dumps({'handler_senses': ['animal'], 'translations': ['他的联络人在哪里？']}, ensure_ascii=False)}}]}
        repaired = {'choices': [{'message': {'content': '{"translation":"它的训导员在哪儿？"}'}}]}
        with patch('reader.translation.request_json', side_effect=[reply(['他的联络人在哪里？']), reviewed, repaired]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['text'], '它的训导员在哪儿？')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.args[1]['model'], self.settings['reviewModel'])
        self.assertEqual(result['terminology']['handlers'], [{'unit': 0, 'sense': 'animal'}])
        self.assertIn('A second dog charged at him.', request.call_args_list[1].args[1]['messages'][0]['content'])

    def test_spy_handler_is_not_changed_to_dog_trainer_by_background_dog(self):
        self.book['paragraphs'][3]['text'] = 'A dog barked outside the safe house.'
        self.book['paragraphs'][4]['text'] = 'The spy called his handler.'
        value = '那名间谍给他的联络官打了电话。'
        reviewed = {'choices': [{'message': {'content': json.dumps({'handler_senses': ['intelligence'], 'translations': [value]}, ensure_ascii=False)}}]}
        with patch('reader.translation.request_json', side_effect=[reply([value]), reviewed]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(result['text'], value)
        self.assertEqual(request.call_count, 2)

    def test_goods_and_unclear_handler_do_not_get_an_invented_dog(self):
        for source, sense, value in [('The baggage handler lifted the suitcase.', 'equipment_or_goods', '行李装卸工提起了箱子。'),
                                     ('Where is his handler?', 'mixed_or_unclear', '他的负责人在哪儿？')]:
            with self.subTest(sense=sense):
                self.book['paragraphs'][4]['text'] = source
                reviewed = {'choices': [{'message': {'content': json.dumps({'handler_senses': [sense], 'translations': [value]}, ensure_ascii=False)}}]}
                with patch('reader.translation.request_json', side_effect=[reply([value]), reviewed]) as request:
                    result = translate(self.book, 4, self.settings)
                self.assertEqual(result['text'], value)
                self.assertEqual(request.call_count, 2)

    def test_operator_and_handler_are_both_checked_in_one_unit(self):
        self.book['paragraphs'][4]['text'] = 'The special operations operator spoke to the dog’s handler.'
        value = '特战队员与犬只的训导员交谈。'
        reviewed = {'choices': [{'message': {'content': json.dumps({'operator_senses': ['combat'], 'handler_senses': ['animal'], 'translations': [value]}, ensure_ascii=False)}}]}
        with patch('reader.translation.request_json', side_effect=[reply([value]), reviewed]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        required = request.call_args.args[1]['response_format']['json_schema']['schema']['required']
        self.assertEqual(set(required), {'operator_senses', 'handler_senses', 'translations'})
        self.assertEqual(result['terminology']['operators'][0]['sense'], 'combat')
        self.assertEqual(result['terminology']['handlers'][0]['sense'], 'animal')

    def test_unfixed_animal_handler_is_rejected_and_initial_draft_retained(self):
        self.book['paragraphs'][4]['text'] = 'The dog waited for his handler.'
        bad = '这条狗在等他的联络人。'
        reviewed = {'choices': [{'message': {'content': json.dumps({'handler_senses': ['animal'], 'translations': [bad]}, ensure_ascii=False)}}]}
        failed_repair = {'choices': [{'message': {'content': json.dumps({'translation': bad}, ensure_ascii=False)}}]}
        with patch('reader.translation.request_json', side_effect=[reply([bad]), reviewed, failed_repair]), self.assertRaises(RuntimeError):
            translate(self.book, 4, self.settings, self.store)
        self.assertEqual(self.store.draft(self.book['id'], 4, profile(draft_settings(self.settings)))['text'], bad)
        self.assertIsNone(self.store.translation(self.book['id'], 4, profile(self.settings)))

    def test_equipment_operator_is_not_forced_into_a_combat_role(self):
        self.book['paragraphs'][4]['text'] = 'The machine operator stopped the machine.'
        reviewed = {'choices': [{'message': {'content': '{"operator_senses":["equipment"],"translations":["操作员停下了机器。"]}'}}]}
        with patch('reader.translation.request_json', side_effect=[reply(['操作员停下了机器。']), reviewed]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['text'], '操作员停下了机器。')

    def test_operator_error_cannot_move_across_inline_quote_fragments(self):
        self.book['paragraphs'][4]['text'] = 'The operators called it a “skull-crusher” for its fit.'
        reviewed = {'choices': [{'message': {'content': '{"operator_senses":["combat"],"translations":["他们称它为","碎颅器","操作员们觉得它夹得太紧。"]}'}}]}
        outputs = [reply(['队员们称它为', '碎颅器', '，因为它夹得太紧。']), reviewed,
                   {'choices': [{'message': {'content': '{"translation":"队员们称它为"}'}}]},
                   {'choices': [{'message': {'content': '{"translation":"，因为它夹得太紧。"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(result['text'], '队员们称它为“碎颅器”，因为它夹得太紧。')

    def test_distinct_operator_senses_in_one_quoted_sentence_remain_distinct(self):
        self.book['paragraphs'][4]['text'] = 'The operators met a “radar operator” on duty.'
        values = ['队员们见到了一位', '雷达操作员', '，他正在值班。']
        reviewed = {'choices': [{'message': {'content': json.dumps({'operator_senses': ['combat', 'equipment'], 'translations': values}, ensure_ascii=False)}}]}
        with patch('reader.translation.request_json', side_effect=[reply(values), reviewed]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 2)
        self.assertIn('雷达操作员', result['text'])

    def test_ambiguous_operator_does_not_invent_a_special_forces_identity(self):
        self.book['paragraphs'][4]['text'] = 'The operator arrived.'
        reviewed = {'choices': [{'message': {'content': '{"operator_senses":["mixed_or_unclear"],"translations":["操作员到了。"]}'}}]}
        with patch('reader.translation.request_json', side_effect=[reply(['操作员到了。']), reviewed]):
            result = translate(self.book, 4, self.settings, self.store)
        self.assertNotIn('特战', result['text'])

    def test_only_invalid_review_unit_is_repaired_by_reviewer_not_primary(self):
        self.book['paragraphs'][4]['text'] = 'Morgan waited. “Suka, blyat!”'
        outputs = [reply(['__PERSON_000__等着。', '该死的！']), reply(['__PERSON_000__耐心等候。', 'Suka, blyat!']),
                   {'choices': [{'message': {'content': '{"translation":"婊子养的，操！"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 3)
        repair = request.call_args
        self.assertTrue(repair.args[0].startswith(self.settings['reviewBaseUrl']))
        self.assertEqual(repair.args[1]['model'], self.settings['reviewModel'])
        self.assertEqual(repair.args[2], 'review-secret')
        self.assertIn('Suka, blyat!', repair.args[1]['messages'][-1]['content'])
        self.assertNotIn('Morgan waited.', repair.args[1]['messages'][-1]['content'])
        self.assertEqual(result['text'], 'Morgan耐心等候。“婊子养的，操！”')
        self.assertEqual(len(result['alignment']), 2)

    def test_repeated_review_repairs_only_the_slot_containing_the_paragraph(self):
        self.book['paragraphs'][4]['text'] = 'He switched off the device and led his team to the ambush site. The machine needed twenty minutes to recover enough power for another shot.'
        values = ['他关闭了战术电磁脉冲装置，带领七人小队前往山上的伏击地点。',
                  '装置需要二十分钟才能恢复并储存足够的能量，以便再次发射。']
        outputs = [reply(values), reply([''.join(values), values[1]]),
                   {'choices': [{'message': {'content': json.dumps({'translation': values[0]}, ensure_ascii=False)}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result['targetSentences'], values)
        self.assertEqual(request.call_args.args[1]['model'], self.settings['reviewModel'])
        self.assertIn('重复', request.call_args.args[1]['messages'][0]['content'])

    def test_repeated_initial_translation_is_repaired_before_draft_is_saved(self):
        self.book['paragraphs'][4]['text'] = 'He switched off the device and led his team to the ambush site.'
        value = '他关闭了战术电磁脉冲装置，带领七人小队前往山上的伏击地点。'
        outputs = [reply([value * 2]),
                   {'choices': [{'message': {'content': json.dumps({'translation': value}, ensure_ascii=False)}}]}, reply([value])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[1].args[1]['model'], self.settings['model'])
        self.assertEqual(result['text'], value)
        self.assertEqual(self.store.draft(self.book['id'], 4, profile(draft_settings(self.settings)))['text'], value)

    def test_duplicate_names_are_constrained_in_repair_schema(self):
        outputs = [reply(['__PERSON_000__局长等着。', '景色？']), reply(['他等着。', '景色？']),
                   {'choices': [{'message': {'content': '{"translation":"__PERSON_000__局长耐心等候。"}'}}]}]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        payload = request.call_args.args[1]
        pattern = payload['response_format']['json_schema']['schema']['properties']['translation']['pattern']
        self.assertIsNotNone(re.fullmatch(pattern, '__PERSON_000__局长等候。'))
        self.assertIsNone(re.fullmatch(pattern, '除了__PERSON_000__还是__PERSON_000__。'))
        self.assertEqual(result['text'].count('Morgan'), 1)

    def test_phonetic_code_sequence_recovers_without_repeating_futile_requests(self):
        self.book['paragraphs'][4]['text'] = 'Alpha, Bravo, Cain, Delta.'
        untranslated = reply(['Alpha, Bravo, __PERSON_000__ ... Delta.'])
        with patch('reader.translation.person_names', return_value=['Cain']), patch('reader.translation.request_json', side_effect=[untranslated, untranslated]) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([call.args[1]['model'] for call in request.call_args_list], [self.settings['model'], self.settings['reviewModel']])
        self.assertIn('Alpha（阿尔法）', result['text'])
        self.assertIn('Bravo（布拉沃）', result['text'])
        self.assertEqual(result['text'].count('Cain'), 1)
        self.assertEqual(result['protectedNames'], ['Cain'])

    def test_code_gloss_never_accepts_prose_or_generic_word_lists(self):
        for source in ['The Alpha team met Bravo.', 'Hotel, Golf.', 'The view?', 'Alpha and Bravo.']:
            self.assertIsNone(phonetic_code_gloss(source, {}, '简体中文'))

    def test_natural_rephrasing_can_split_chinese_clauses_without_losing_alignment(self):
        source = 'Her father’s carpenter was an immigrant from Belarus; the odor of burnt leaf and sweat was one she remembered. She kept walking.'
        self.book['paragraphs'][4]['text'] = source
        initial = reply(['她父亲的木匠是来自白俄罗斯的移民；烧焦烟叶和汗水的味道是她记忆中的。', '她继续走。'])
        revised = reply(['父亲雇的木匠是白俄罗斯移民。那股焦烟叶混着汗水的气味，她还记得。', '她继续往前走。'])
        with patch('reader.translation.request_json', side_effect=[initial, revised]):
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(''.join(result['sourceSentences']), source)
        self.assertEqual(len(result['targetSentences']), 2)
        self.assertEqual(result['targetSentences'][0].count('。'), 2)
        self.assertEqual(result['alignment'], [{'source': [0], 'target': [0]}, {'source': [1], 'target': [1]}])

    def test_missing_review_units_retry_with_validation_feedback(self):
        outputs = [reply(['__PERSON_000__局长等着。', '景色？']), reply(['__PERSON_000__局长等着。']), reply(['__PERSON_000__局长等着。', '景色？'])]
        with patch('reader.translation.request_json', side_effect=outputs) as request:
            result = translate(self.book, 4, self.settings, self.store)
        self.assertEqual(len(result['targetSentences']), 2)
        self.assertIn('failed validation', request.call_args.args[1]['messages'][-1]['content'])

    def test_bulk_progress_counts_only_final_results_and_reuses_legacy_drafts(self):
        legacy = profile(draft_settings(self.settings))
        self.store.cache(self.book['id'], 4, legacy, '旧译文')
        self.store.save_settings(self.settings)
        worker = Translator(self.store, workers=1, translate_fn=lambda *_: {'text': '已校对', 'proofreading': {'model': self.settings['reviewModel']}})
        try:
            status = worker.bulk_status(self.book)
            self.assertEqual((status['completed'], status['drafted']), (0, 1))
            worker.window(self.book, 4, enabled=False, selected=4)
            until(lambda: self.store.translation(self.book['id'], 4, profile(self.settings)) == '已校对')
            self.assertEqual(worker.bulk_status(self.book)['completed'], 1)
            self.assertEqual(self.store.translation(self.book['id'], 4, legacy), '旧译文')
        finally:
            worker.close()
            for thread in worker.threads:
                thread.join(2)

    def test_review_configuration_preserves_keys_and_validates_types_urls(self):
        settings = validate_settings({'reviewEnabled': False, 'reviewApiKey': ''}, self.settings)
        self.assertFalse(settings['reviewEnabled'])
        self.assertEqual(settings['reviewApiKey'], 'review-secret')
        self.assertEqual(validate_settings({'clearReviewApiKey': True}, self.settings)['reviewApiKey'], '')
        for value in [{'reviewEnabled': 'false'}, {'reviewBaseUrl': 'file:///secret'}, {'reviewModel': ''}]:
            with self.assertRaises(ValueError):
                validate_settings(value, self.settings)

    def test_api_masks_both_keys_and_tests_both_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(port=0, data_dir=directory)
            server.store.save_settings(self.settings)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(base + '/api/settings') as response:
                    data = json.load(response)
                self.assertEqual(data['apiKey'], '')
                self.assertEqual(data['reviewApiKey'], '')
                self.assertTrue(data['hasReviewApiKey'])
                outputs = [{'data': [{'id': self.settings['model']}]}, {'data': [{'id': self.settings['reviewModel']}]}]
                with patch('server.request_json', side_effect=outputs) as request:
                    req = urllib.request.Request(base + '/api/model/test', data=b'{}', headers={'Content-Type': 'application/json'})
                    with opener.open(req) as response:
                        self.assertTrue(json.load(response)['ok'])
                self.assertEqual(request.call_count, 2)
            finally:
                server.shutdown()
                server.translator.close()
                server.server_close()
                thread.join(2)
                for worker in server.translator.threads:
                    worker.join(2)
                server.store.db.close()


if __name__ == '__main__':
    unittest.main()
