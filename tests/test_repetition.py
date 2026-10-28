import unittest

from reader.repetition import repetition_issue
from reader.translation_rules import RuleViolation, finish_translation, translation_plan


class RepetitionTests(unittest.TestCase):
    def setUp(self):
        self.english = ['He switched off the device and led his team to the ambush site. ',
                        'The machine needed twenty minutes to recover enough power for another shot.']
        self.chinese = ['他关闭了战术电磁脉冲装置，带领七人小队前往山上的伏击地点。',
                        '装置需要二十分钟才能恢复并储存足够的能量，以便再次发射。']

    def test_whole_paragraph_in_first_slot_repairs_first_slot(self):
        plan = translation_plan(''.join(self.english), [])
        with self.assertRaises(RuleViolation) as raised:
            finish_translation(plan, [''.join(self.chinese), self.chinese[1]])
        self.assertEqual(raised.exception.unit_index, 0)
        self.assertIn('重复', str(raised.exception))

    def test_one_unit_repeating_its_translation_is_rejected(self):
        with self.assertRaises(RuleViolation) as raised:
            finish_translation(translation_plan(self.english[0], []), [self.chinese[0] * 3])
        self.assertEqual(raised.exception.unit_index, 0)

    def test_identical_long_target_for_unrelated_sources_repairs_short_source(self):
        issue = repetition_issue(['The detective pointed to the marks.', 'Our theory is that the driver was shot before the car swerved off the road.'],
                                 ['我们目前的推测是司机中弹后猛打方向盘，导致车辆翻覆并冲下路基。'] * 2)
        self.assertEqual(issue['unit'], 0)

    def test_small_word_changes_do_not_hide_a_near_duplicate(self):
        a = '我们目前的推测是，司机在朝这个方向行驶时中弹，车速加上猛打方向盘导致车辆翻覆，冲下了路基。'
        issue = repetition_issue(['The detective pointed to the marks.', 'Our theory is that the driver was shot before the car swerved off the road.'],
                                 [a, a.replace('中弹', '中枪')])
        self.assertIsNotNone(issue)

    def test_long_refrain_repeated_in_original_is_preserved(self):
        sentence = self.english[0]
        result = finish_translation(translation_plan(sentence + sentence, []), [self.chinese[0], self.chinese[0]])
        self.assertEqual(result['text'], self.chinese[0] * 2)
        repeated_source = sentence.rstrip('. ') + '; ' + sentence.lower()
        result = finish_translation(translation_plan(repeated_source, []), [self.chinese[0] * 2])
        self.assertEqual(result['text'], self.chinese[0] * 2)

    def test_short_refrains_and_technical_identifiers_are_allowed(self):
        self.assertIsNone(repetition_issue(['No, no, no!', 'Again and again.'], ['不，不，不！', '一遍又一遍。']))
        self.assertIsNone(repetition_issue(['A separator.'], ['X' * 500]))

    def test_short_action_repeated_with_small_changes_rebuilds_both_slots(self):
        issue = repetition_issue(['Based on the wound, it was a rifle round.', 'The detective pointed toward the black marks.'],
                                 ['侦探朝路面上新鲜的黑痕指了指。', '侦探朝路面上那些新鲜的黑色痕迹指了指。'])
        self.assertEqual(issue['repairUnits'], [0, 1])

    def test_original_can_repeat_a_clause_inside_a_longer_sentence(self):
        self.assertIsNone(repetition_issue([self.english[0], self.english[0].rstrip('. ') + ', while the others followed.'],
                                            [self.chinese[0], self.chinese[0] + '其他人紧随其后。']))

    def test_common_characters_scattered_through_long_text_are_not_a_copy(self):
        short = '侦探朝路面上新鲜的黑痕指了指'
        long = ''.join(char + ''.join(chr(0x6000 + index * 40 + j) for j in range(30)) for index, char in enumerate(short))
        self.assertIsNone(repetition_issue(['An unrelated detailed historical account.', 'The detective pointed at the marks.'], [long, short]))

    def test_glossary_entries_can_share_their_original_definition(self):
        sources = ["Special operations variant of the Army’s Chinook helicopter, usually flown by members of the 160th SOAR. This twin-rotor aircraft carries troops and cargo.",
                   "Special operations variant of the Army’s Black Hawk helicopter, usually flown by members of the 160th SOAR."]
        targets = ['陆军支奴干直升机的特种作战改型，通常由第160特种作战航空团成员驾驶。这种双旋翼直升机载人载货。',
                   '陆军黑鹰直升机的特种作战改型，通常由第160特种作战航空团成员驾驶。']
        self.assertIsNone(repetition_issue(sources, targets))

    def test_ordinary_translation_and_alignment_are_unchanged(self):
        result = finish_translation(translation_plan(''.join(self.english), []), self.chinese)
        self.assertEqual(result['targetSentences'], self.chinese)
        self.assertEqual(result['alignment'], [{'source': [0], 'target': [0]}, {'source': [1], 'target': [1]}])


if __name__ == '__main__':
    unittest.main()
