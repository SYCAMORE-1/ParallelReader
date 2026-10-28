"""Contextual sense guidance, never global word replacement."""
import json
import re
from reader.military_terms import parachute_shots
from reader.scene_phrases import scene_shots

TERMINOLOGY_VERSION = 'context-senses-v1'
OPERATOR = re.compile(r'\boperators?\b', re.I)
OPERATOR_SENSES = ['combat', 'communications', 'equipment', 'business', 'mixed_or_unclear']
HANDLER = re.compile(r'\bhandlers?\b', re.I)
HANDLER_VERSION = 'handler-referents-v1'
HANDLER_SENSES = ['animal', 'intelligence', 'equipment_or_goods', 'mixed_or_unclear']
RULES = [
    (r'put(?:s|ting)?\s+(?:the\s+)?bodies\s+in(?:to)?\s+the\s+dirt', 'put bodies in the dirt 在战斗语境通常是干掉／击毙人，不是埋尸；若明确描述墓穴、铁锹、葬礼或掩埋，再按实际埋葬动作处理。还要核对后续弹药、现场痕迹与动机，不能把隐喻译成另一件事。'),
    (r'jump(?:s|ed|ing)?', 'jump 先看宾语与行动：跳伞叙述中 jump（with）rifles／AKMs 等装备表示携带该装备跳伞，不能凭空补成跳伞后缴获武器，不能改成敌人持有这些装备。jump the gun 是操之过急，jump a person 也可指突然袭击人；必须按当前句与周边行动区分。'),
    (r'deal(?:s|t)?', 'deal 不能总译成交易。part of the deal 在描述工作、危险任务或黑色幽默时，往往指干这行／这类行动少不了的部分、这份差事的常态；真正谈买卖、合同或付款时才用交易／约定。deal with 可是处理／应对。'),
    (r'HAHO|HALO', '跳伞缩略语必须区分：HAHO = High Altitude High Opening，高跳高开／高空高开；HALO = High Altitude Low Opening，高跳低开／高空低开。两个词绝不能互换，翻译时可保留英文缩写并加正确中文释义。这里的 high/low 指开伞高度，不能把 HAHO 也译成高跳低开。HALO 若明确指电子游戏或普通 halo 光环，使用该语境的含义，不强套跳伞术语。'),
    (r'operators?', 'operator：参与突击、特种行动、战斗或战术训练的人，可按身份与口吻译作队员、作战人员、特战队员或行动人员；不能译作操作员。只有明确是特种部队才用特战，不臆造隶属。电话语境是接线员，无线电岗位是通信员；机器设备岗位才是操作员／操作手；商业经营者是运营商／经营者。operator error 通常指操作失误，军事背景也不例外。'),
    (r'operatives?', 'operative 指执行秘密任务的人时是特工／行动人员；不要按形容词“有效的”或普通操作人员处理。'),
    (r'handlers?', 'handler 必须先确认其负责的对象，再选中文词义。警犬／军犬的 handler 是训导员、训犬员或领犬员；动物也可用 he/his，Where is his handler? 中的 his 可指刚出现的犬只，不一定指主句中的男性人物。情报人员的 handler 才是联络官、负责人或上线；设备或货物岗位是操作、装卸人员。查看当前句与最近上下文中的动作、角色和指代链，不能只凭谍战类型套用联络人，也不能仅因背景有狗就把间谍的联络官改成训犬员。'),
    (r'assets?', 'asset 若指可提供情报的人，按证据译作线人／情报来源等；财务语境是资产，行动中可调用的装备／支援力量则按用途表达。rotary-wing assets 可指直升机或旋翼机支援力量，ground assets 可指地面兵力或支援力量，不机械译成旋翼机资产／地面资产，也不把所有 asset 都译成人。'),
    (r'brass|grains?', 'brass 在射击现场通常指打出的弹壳，不是黄铜原料；grain 是弹头重量的格令单位，不是口径，也不把弹头重量说成弹壳重量。先分清武器、整发弹药、弹头和打出的弹壳，再组织中文。'),
    (r'intelligence', 'intelligence 在搜集、报告、机构或军事分析中通常是情报；描述认知能力时才是智力。'),
    (r'magazines?', 'magazine 在枪械、弹药语境中是弹匣，不是杂志；阅读或出版语境仍是杂志。'),
    (r'rounds?', 'round 在弹药与射击中按句意作弹药或“发”；巡逻、访问、比赛或讨论则可能是一轮／一圈，不要机械译成圆形。'),
    (r'engagements?', 'engagement 在战斗中是交战／接战，订婚、约定或工作安排则用相应日常词义。'),
    (r'elements?', 'element 指参与行动的一支分队时可译作小组／分队；化学或构成要素仍是元素／要素。'),
    (r'cover', 'cover 按动作与对象区分掩护、掩护身份、遮盖或书刊封面；under cover 等需结合完整短语。'),
    (r'cells?', 'cell 可指组织的小组、牢房、细胞或手机，必须由该句和相邻语境判断。'),
    (r'contacts?', 'contact 在战术呼叫中可能是发现敌人／接敌，情报或社交中可能是联络人，物理动作也可指接触。'),
    (r'stations?', 'station 可指情报站、驻地、电台或车站；不要仅因是情报小说就把每个 station 都译成情报站。'),
    (r'company|companies', 'company 可以是公司、军队的连、陪伴，或特定情报机构的代称；同一句中也要看明确所指。'),
    (r'extract(?:ion|ed|ing)?|exfil(?:tration)?', 'extract／exfil 在行动语境多指撤出、撤离或接应带走；资料、液体等对象则可指提取／抽取。'),
    (r'compromised?', 'compromised 对掩护身份、情报或行动可指暴露／泄露／失去安全性；谈判语境才可能是妥协。'),
    (r'theaters?', 'theater 在军事地域中可指战区；文娱场所仍是剧院。'),
]


def terminology_note(source, language):
    if language not in {'简体中文', '繁體中文'}:
        return ''
    chosen = [note for pattern, note in RULES if re.search(r'\b(?:' + pattern + r')\b', source, re.I)][:6]
    if not chosen:
        return ''
    return ('\n\n语境词义检查：先看人物正在做什么、岗位、动作、装备与上下文，再确定词义。'
            '不能套用词典第一义，也不能仅凭书名或类型强行军事化；同一个词在不同语境可以有不同译法，'
            '同一语义要尽量一致。下面是辨义参考，不是待译正文，不要添加原文未提到的身份或事实：\n' + '\n'.join(chosen))


def terminology_shots(source, language, repair=False, editing=False):
    if language not in {'简体中文', '繁體中文'}:
        return []
    handler_examples = handler_shots(source, language, repair, editing) + parachute_shots(source, language, repair, editing) + scene_shots(source, language, repair, editing)
    if OPERATOR.search(source):
        sources = ['The special operations team sent two operators.', 'The telephone operator connected the call.',
                   'The machine operator stopped the machine.', 'The missile missed because of operator error.']
        targets = (['特戰小組派出兩名隊員。', '接線員接通了電話。', '操作員停下了機器。', '導彈因操作失誤未能命中。'] if language == '繁體中文' else
                   ['特战小组派出两名队员。', '接线员接通了电话。', '操作员停下了机器。', '导弹因操作失误未能命中。'])
        stale = ['特种作战小组派出了两名操作员。', '电话操作员连接了呼叫。', '操作员停下了机器。', '导弹因操作失误而未命中。']
        senses = ['combat', 'communications', 'equipment', 'equipment']
    elif re.search(r'\bmagazines?\b', source, re.I):
        sources = ['The rifle had an empty magazine.', 'She read a magazine on the train.']
        targets = ['步槍的彈匣空了。', '她在火車上看雜誌。'] if language == '繁體中文' else ['步枪的弹匣空了。', '她在火车上看杂志。']
        stale, senses = ['步枪有一本空杂志。', '她在火车上看杂志。'], None
    else:
        return handler_examples
    if repair:
        messages = []
        for original, target in zip(sources[:3], targets[:3]):
            messages.extend([{'role': 'user', 'content': 'ICL example only: choose the contextual word sense.\n<source>' + original + '</source>'},
                             {'role': 'assistant', 'content': json.dumps({'translation': target}, ensure_ascii=False)}])
        return messages + handler_examples
    value = {'paragraph': ' '.join(sources), 'protected_names': {},
             'units': [{'id': i, 'text': text, 'dialogue': False} for i, text in enumerate(sources)]}
    answer = {'translations': targets}
    if editing:
        value['draft_translation'] = ''.join(stale)
        if senses:
            value['operator_unit_ids'] = list(range(len(sources)))
            answer = {'operator_senses': senses, **answer}
    return [{'role': 'user', 'content': 'ICL examples only: correct the word sense using local context, including civilian counterexamples.\n' + json.dumps(value, ensure_ascii=False)},
            {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}] + handler_examples


def handler_shots(source, language, repair=False, editing=False):
    if not HANDLER.search(source):
        return []
    examples = [
        (['A second dog charged at him.'], 'Where is his handler?',
         '它的训导员在哪儿？', '它的訓導員在哪兒？', '他的联络人在哪里？', 'animal'),
        (['A dog barked outside the safe house.'], 'The spy called his handler.',
         '那名间谍给他的联络官打了电话。', '那名間諜給他的聯絡官打了電話。', '那名间谍给他的训犬员打了电话。', 'intelligence'),
        ([], 'The baggage handler lifted the suitcase.',
         '行李装卸工提起了箱子。', '行李裝卸工提起了箱子。', '行李联络人提起了箱子。', 'equipment_or_goods'),
    ]
    messages = []
    for previous, original, simplified, traditional, stale, sense in examples:
        target = traditional if language == '繁體中文' else simplified
        reference = {'previous_context': previous}
        if repair:
            value = 'ICL example only; reference context is not translated:\n' + json.dumps(reference, ensure_ascii=False) + '\n<source>' + original + '</source>'
            answer = {'translation': target}
        else:
            value = {'reference_context': reference, 'source_paragraph': original, 'protected_names': {},
                     'units': [{'id': 0, 'text': original, 'dialogue': False}]}
            answer = {'translations': [target]}
            if editing:
                value.update(draft_translation=stale, handler_unit_ids=[0])
                answer = {'handler_senses': [sense], **answer}
            value = 'Independent ICL example: resolve the handler referent from this local evidence.\n' + json.dumps(value, ensure_ascii=False)
        messages.extend([{'role': 'user', 'content': value}, {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}])
    return messages


def handler_units(units, language):
    return [unit['id'] for unit in units if HANDLER.search(unit['text'])] if language in {'简体中文', '繁體中文'} else []


def handler_mistranslation(values, ids, senses):
    for index, sense in zip(ids, senses):
        if sense != 'animal' or not isinstance(values[index], str):
            continue
        value = values[index]
        if re.search(r'联络|聯絡|联系[人员]|聯繫[人員]|上线|上線|处理[人者员]|處理[人者員]', value) and not re.search(r'训导|訓導|训犬|訓犬|驯|馴|领犬|領犬|饲养|飼養', value):
            return index, 'handler 的负责对象已根据当前句与上下文判定为动物：犬只的 handler 应译作训导员／训犬员／领犬员，不是人物的联络人或上线。his 可以指犬只，不能据此认定为男性人物。只修复当前片段，保留全部原意和人名，不添加情节。'
    return None


def operator_units(units, language):
    return [unit['id'] for unit in units if OPERATOR.search(unit['text'])] if language in {'简体中文', '繁體中文'} else []


def operator_mistranslation(values, ids, senses, pieces=None, repaired=()):
    alternatives = {'combat': '作战或行动队员（只有明确特种部队身份才用特战队员）',
                    'communications': '接线员或通信员', 'business': '运营商或经营者'}
    for index, sense in zip(ids, senses):
        if sense not in alternatives:
            continue
        candidates = [index]
        if pieces:
            sentence = pieces[index]['sentence']
            same_sentence = [other_sense for other, other_sense in zip(ids, senses) if pieces[other]['sentence'] == sentence]
            # Inline quotes split one sentence into several units. A model may
            # move the mistranslated noun to a neighboring unit. Only extend
            # the check when all operator occurrences have the SAME sense;
            # a sentence may also contain a legitimate machine operator.
            if all(other_sense == sense for other_sense in same_sentence):
                candidates = [i for i, piece in enumerate(pieces) if piece['sentence'] == sentence]
        for wrong in candidates:
            if isinstance(values[wrong], str) and re.search(r'操作[员員]|操作者', values[wrong]):
                feedback = f'语境词义检查：operator 此处已判定为 {sense}，应按上下文表达为{alternatives[sense]}，不能译成操作员；保留其他信息与所有人名。只翻译当前 source 片段，不把信息移到同句其他片段。'
                # First recover the source noun where it belongs, then remove
                # the incorrectly moved noun from its neighboring fragment.
                target = index if wrong != index and index not in repaired else wrong
                if target != index:
                    feedback += '本片段不含 operator，不得重复同句其他片段的人员称谓。'
                return target, feedback
    return None
