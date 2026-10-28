import unittest

from reader.scene_phrases import carry_jump, phrase_mistranslation
from reader.translation_rules import RuleViolation, finish_translation, translation_plan


class ScenePhraseTests(unittest.TestCase):
    def test_jump_with_weapon_does_not_invent_capture(self):
        source = 'Three of us are going to jump Russian-sourced AKMs.'
        self.assertTrue(carry_jump(source))
        with self.assertRaises(RuleViolation):
            finish_translation(translation_plan(source, []), ['我们三个人要跳伞去缴获俄制AKM。'])
        result = finish_translation(translation_plan(source, []), ['我们三人将携带俄制AKM跳伞。'])
        self.assertEqual(result['text'], '我们三人将携带俄制AKM跳伞。')

    def test_jump_the_gun_and_jump_a_person_keep_their_own_meaning(self):
        for source, target in [('Do not jump the gun.', '别操之过急。'),
                               ('They jumped the armed guard.', '他们突然袭击了那名武装警卫。'),
                               ('They jumped with captured rifles.', '他们携带缴获的步枪跳伞。')]:
            self.assertIsNone(phrase_mistranslation(source, target))

    def test_dark_humor_in_a_job_is_not_automatically_a_business_transaction(self):
        source = 'He smiled, knowing dark humor was part of the deal.'
        self.assertIsNotNone(phrase_mistranslation(source, '他笑了笑，知道黑色幽默是交易的一部分。'))
        self.assertIsNone(phrase_mistranslation(source, '他笑了笑，知道干这行少不了这种黑色幽默。'))

    def test_actual_business_deal_is_not_changed_to_an_operation(self):
        self.assertIsNone(phrase_mistranslation('An advance payment was part of the deal.', '预付款是这笔交易的条件之一。'))
        self.assertIsNone(phrase_mistranslation('He had to deal with the problem.', '他必须处理这个问题。'))
        self.assertIsNone(phrase_mistranslation('Dark humor was part of the deal in the contract.', '黑色幽默是合同中这笔交易的一部分。'))

    def test_combat_metaphor_does_not_invent_burial(self):
        source = 'Wherever we put bodies in the dirt, we must leave the right brass.'
        self.assertIsNotNone(phrase_mistranslation(source, '不管我们把尸体埋在哪儿，都得留下合适的弹壳。'))
        self.assertIsNone(phrase_mistranslation(source, '无论在哪儿干掉人，我们都得留下合适的弹壳。'))
        self.assertIsNone(phrase_mistranslation('They put the bodies in the dirt beside the open graves.', '他们将尸体埋在挖开的墓坑旁。'))

    def test_bullet_weight_is_not_assigned_to_spent_case(self):
        source = 'The only brass was from 77 grain ammunition.'
        self.assertIsNotNone(phrase_mistranslation(source, '现场只有77格令弹壳。'))
        self.assertIsNone(phrase_mistranslation(source, '现场只有77格令弹药留下的弹壳。'))


if __name__ == '__main__':
    unittest.main()
