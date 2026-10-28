import unittest

from reader.military_terms import parachute_mistranslation
from reader.translation_rules import RuleViolation, finish_translation, translation_plan


class ParachuteTermTests(unittest.TestCase):
    def test_haho_and_halo_cannot_be_reversed(self):
        for source, wrong in [('They made a HAHO jump.', '他们进行了高跳低开（HAHO）跳伞。'),
                              ('They made a HALO jump.', '他们进行了高空高开（HALO）跳伞。'),
                              ('They HAHO in from the aircraft.', '他们从飞机上高跳低开。')]:
            with self.subTest(source=source), self.assertRaises(RuleViolation):
                finish_translation(translation_plan(source, []), [wrong])

    def test_correct_simplified_traditional_and_acronym_only_are_allowed(self):
        for source, target in [('They made a HAHO jump.', '他们进行了高跳高开（HAHO）跳伞。'),
                               ('They made a HALO jump.', '他們進行了高跳低開（HALO）跳傘。'),
                               ('They made a HAHO jump.', '他们采用HAHO方式跳伞。'),
                               ('They made a HAHO jump.', '他们从高空跳出，高空开伞。'),
                               ('They made a HAHO jump.', 'They performed a high altitude high opening jump.')]:
            with self.subTest(target=target):
                self.assertEqual(finish_translation(translation_plan(source, []), [target])['text'], target)

    def test_both_techniques_in_one_source_keep_their_own_labels(self):
        source = 'HAHO means high altitude high opening; HALO means high altitude low opening.'
        self.assertIsNone(parachute_mistranslation(source, 'HAHO是高跳高开；HALO是高跳低开。'))
        self.assertIsNotNone(parachute_mistranslation(source, '高跳低开（HAHO）；高跳高开（HALO）。'))

    def test_game_and_ordinary_halo_keep_their_non_parachuting_meaning(self):
        for source, target in [('They played HALO.', '他们玩了《光环》。'),
                               ('They played HALO on a console before the parachute jump.', '跳伞前，他们用主机玩了《光环》。'),
                               ('A halo surrounded the light.', '灯光周围有一圈光环。')]:
            self.assertIsNone(parachute_mistranslation(source, target))

    def test_explicit_contrast_is_not_mistaken_for_wrong_expansion(self):
        self.assertIsNone(parachute_mistranslation('They chose HAHO instead of high-altitude low-opening.',
                                                  '他们选择高跳高开，而不是高跳低开。'))


if __name__ == '__main__':
    unittest.main()
