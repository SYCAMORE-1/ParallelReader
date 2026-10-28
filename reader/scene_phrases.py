"""Source action/idiom hints with civilian counterexamples."""
import json
import re

SCENE_VERSION = 'scene-meaning-v1'
SCENE_WORD = re.compile(r'\b(?:jump(?:ed|ing|s)?|deal(?:s|t)?)\b', re.I)
PART_OF_DEAL = re.compile(r'\bpart of (?:the|this) deal\b', re.I)
PUT_BODIES = re.compile(r'\bput(?:s|ting)?\s+(?:the\s+)?bodies\s+in(?:to)?\s+the\s+dirt\b', re.I)
CARRY_JUMP = re.compile(r'\bjump(?:ed|ing)?\s+(?:with\s+)?(?:(?:Russian[- ](?:sourced|made)|Russian|the|our|their|his|her|these|those|an?|loaded|suppressed)\s+){0,4}(?:AKMs?|AK-?47s?|HK[ -]?416s?|rifles?|weapons?|carbines?|guns?|equipment|kit|packs?)\b', re.I)
SCENE_NOTE = ('\n语境核对：先确认每句是谁对什么做了什么、处于计划还是执行阶段、所带装备及目的，再选择词义。'
              '不要因为初译顺口就沿用它。动词的宾语和固定短语优先于词典第一义，不能把携带装备改成缴获装备，'
              '不能把行动中的习惯说法自动解释为买卖或合同；真正谈交易、付款或约定时保留相应商业含义。')


def carry_jump(source):
    return bool(CARRY_JUMP.search(source)) and not re.search(r'\bjump(?:ed|ing)? the gun\b', source, re.I)


def phrase_mistranslation(source, target):
    if not isinstance(target, str) or not re.search(r'[\u3400-\u9fff]', target):
        return None
    if carry_jump(source) and not re.search(r'\b(?:captur\w*|seiz\w*|loot\w*|stole|stolen)\b', source, re.I):
        if re.search(r'缴获|繳獲|夺取|奪取|抢夺|搶奪', target):
            return 'jump 的直接宾语是随身武器／装备，这里指携带该装备跳伞，不能添加缴获、夺取或袭击敌人的动作。保留原文行动主体和装备归属。'
    if (PART_OF_DEAL.search(source) and re.search(r'\b(?:dark|black) humou?r\b', source, re.I) and re.search(r'交易', target)
            and not re.search(r'\b(?:purchase|payment|price|transaction|contract|bargain)\b', source, re.I)):
        return 'part of the deal 与黑色幽默连用，在这里是这份差事／这类行动少不了的一部分，不是经济交易。按人物对危险任务的口吻重译，不添加协议或买卖。'
    if (PUT_BODIES.search(source) and not re.search(r'\b(?:bur\w*|graves?|funerals?|shovels?)\b', source, re.I)
            and re.search(r'埋|掩葬|下葬', target)):
        return 'put bodies in the dirt 在这段战斗叙述中是击毙／干掉人，不是掩埋尸体。结合射击、弹药和现场痕迹理解动作，只重译当前片段，不添加埋尸。'
    if re.search(r'\bbrass\b', source, re.I) and re.search(r'\b\d+(?:\.\d+)?[ -]grains?\b', source, re.I) and re.search(r'\d+(?:\.\d+)?\s*(?:格令|格林)\s*(?:的)?[弹彈][壳殼]', target):
        return '原文 grain 形容弹药的弹头重量，brass 指这种弹药发射后留下的弹壳；不能说成多少格令重的弹壳。按“某品牌77格令弹药留下的弹壳”等自然关系表达，保留品牌、数字、产地及因果关系。'
    return None


def scene_shots(source, language, repair=False, editing=False):
    if language not in {'简体中文', '繁體中文'}:
        return []
    examples = []
    if carry_jump(source):
        examples += [('They would jump with their rifles.', '他们会携带步枪跳伞。', '他们会跳伞去缴获步枪。'),
                     ('Do not jump the gun.', '别操之过急。', '不要带着枪跳伞。'),
                     ('They jumped the armed guard.', '他们突然袭击了那名武装警卫。', '他们带着警卫跳伞。')]
    if PART_OF_DEAL.search(source):
        examples += [('The team joked about the dangerous mission; dark humor was part of the deal.', '队员们拿危险的任务开玩笑；干这行，少不了这种黑色幽默。', '队员们拿危险的任务开玩笑；黑色幽默是交易的一部分。'),
                     ('An advance payment was part of the deal.', '预付款是这笔交易的条件之一。', '预付款是行动的一部分。')]
    if PUT_BODIES.search(source):
        examples += [('Wherever they put bodies in the dirt, they left spent cases.', '他们在哪儿干掉人，就在哪儿留下打过的弹壳。', '他们在哪儿埋尸，就在哪儿留下打过的弹壳。'),
                     ('They buried the bodies in a grave.', '他们把尸体埋进墓穴。', '他们在墓穴里击毙了那些人。')]
    if re.search(r'\bbrass\b', source, re.I) and re.search(r'\bgrains?\b', source, re.I):
        examples += [('The brass came from 55-grain ammunition.', '这些弹壳来自55格令弹药。', '这些是55格令弹壳。')]
    if not examples:
        return []
    messages = []
    for original, target, stale in examples:
        if language == '繁體中文':
            target = target.translate(str.maketrans({'们': '們', '会': '會', '带': '帶', '枪': '槍', '伞': '傘', '别': '別', '队': '隊', '员': '員', '险': '險', '这': '這', '笔': '筆', '条': '條'}))
        if repair:
            value = 'Independent ICL example: preserve the actual action or idiom.\n<source>' + original + '</source>'
            answer = {'translation': target}
        else:
            value = {'source_paragraph': original, 'protected_names': {}, 'units': [{'id': 0, 'text': original, 'dialogue': False}]}
            if editing:
                value['draft_translation'] = stale
            value = 'Independent ICL example: choose by action, object and situation.\n' + json.dumps(value, ensure_ascii=False)
            answer = {'translations': [target]}
        messages.extend([{'role': 'user', 'content': value}, {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}])
    return messages
