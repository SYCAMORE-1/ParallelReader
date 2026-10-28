"""Fixed acronym meanings with local checks, never blind text replacement."""
import json
import re

PARACHUTE_VERSION = 'parachute-acronyms-v1'
PARACHUTE = re.compile(r'\b(?:HAHO|HALO)\b')
JUMP_CONTEXT = re.compile(r'\b(?:jump\w*|parachut\w*|free[ -]?fall|wings|high[ -]?altitude|(?:high|low)[ -]?opening)\b', re.I)
EXPANSIONS = {
    'HAHO': ('高跳高开', 'High Altitude High Opening', r'高(?:跳高开|空高开|空(?:跳伞|跳出)[，、,\s]*高空开伞)'),
    'HALO': ('高跳低开', 'High Altitude Low Opening', r'高(?:跳低开|空低开|空(?:跳伞|跳出)[，、,\s]*低空开伞)'),
}


def parachute_shots(source, language, repair=False, editing=False):
    if not PARACHUTE.search(source) or language not in {'简体中文', '繁體中文'}:
        return []
    sources = ['They made a HAHO jump.', 'They made a HALO jump.', 'They played the video game HALO.']
    targets = (['他們進行了高跳高開（HAHO）跳傘。', '他們進行了高跳低開（HALO）跳傘。', '他們玩了《光環》這款遊戲。'] if language == '繁體中文' else
               ['他们进行了高跳高开（HAHO）跳伞。', '他们进行了高跳低开（HALO）跳伞。', '他们玩了《光环》这款游戏。'])
    if repair:
        messages = []
        for source, target in zip(sources, targets):
            messages.extend([{'role': 'user', 'content': 'ICL example only: distinguish fixed parachuting acronyms from game titles.\n<source>' + source + '</source>'},
                             {'role': 'assistant', 'content': json.dumps({'translation': target}, ensure_ascii=False)}])
        return messages
    value = {'source_paragraph': ' '.join(sources), 'protected_names': {},
             'units': [{'id': i, 'text': text, 'dialogue': False} for i, text in enumerate(sources)]}
    if editing:
        value['draft_units'] = ['他们进行了高跳低开（HAHO）跳伞。', '他们进行了高跳高开（HALO）跳伞。', '他们玩了高跳低开游戏。']
    return [{'role': 'user', 'content': 'ICL example only: HAHO is high opening, HALO is low opening in parachuting; a game title keeps its own meaning.\n' + json.dumps(value, ensure_ascii=False)},
            {'role': 'assistant', 'content': json.dumps({'translations': targets}, ensure_ascii=False)}]


def parachute_mistranslation(source, target):
    if not isinstance(target, str) or not PARACHUTE.search(source):
        return None
    target = target.translate(str.maketrans({'開': '开', '傘': '伞'}))
    for acronym, (canonical, expansion, correct) in EXPANSIONS.items():
        if not re.search(r'\b' + acronym + r'\b', source):
            continue
        # HALO is also a game name and halo is an ordinary English word.
        # Enforce its parachuting meaning only with local airborne evidence.
        if acronym == 'HALO':
            game_context = re.search(r'\b(?:video[ -]?game|game|franchise|console|Master Chief)\b', source, re.I)
            if game_context or not JUMP_CONTEXT.search(source):
                continue
        other = 'HALO' if acronym == 'HAHO' else 'HAHO'
        wrong = EXPANSIONS[other][2]
        feedback = f'固定术语检查：{acronym} 是 {expansion}，应译为{canonical}（{acronym}），不能与 {other} 的{EXPANSIONS[other][0]}混淆。只重译当前片段，保留人名与其他信息。'
        # Explicitly reversed labels are wrong even when BOTH techniques are
        # mentioned together in the same source unit.
        if (re.search(r'\b' + acronym + r'\b\s*(?:[（(]\s*)?(?:即|是|意为|表示)?\s*' + wrong, target) or
                re.search(wrong + r'\s*[（(]\s*' + acronym + r'\b', target)):
            return feedback
        source_has_other = bool(re.search(r'\b' + other + r'\b', source) or
                                re.search(r'(?:high[ -]?altitude[ ,/-]*)?' + ('low' if other == 'HALO' else 'high') + r'[ -]?opening', source, re.I))
        if not source_has_other:
            for match in re.finditer(wrong, target):
                if not re.search(r'(?:不是|并非|而非|有别于|不能译为)\s*$', target[max(0, match.start() - 8):match.start()]):
                    return feedback
            if re.search(r'\b' + other + r'\b', target):
                return feedback
    return None
