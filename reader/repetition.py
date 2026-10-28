"""Conservative, source-aware detection of long repeated model output.

Report the unit to retranslate; never delete text or deduplicate the novel.
"""
import re
import json
from difflib import SequenceMatcher

REPETITION_VERSION = 'source-aware-v1'
MINIMUM = 24
CJK = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]')
REPETITION_NOTE = ('\nTranslate ONLY each numbered source unit into its matching output slot. '
                   'The paragraph and neighboring units are reference, never extra content for that slot. '
                   'Do not place a full paragraph or an adjacent sentence into one slot and then repeat it in another. '
                   'Preserve repetitions actually present in the English source.')


def normalized(text):
    return re.sub(r'[^\w]', '', text).casefold()


def repeated_span(text, cjk=True):
    """Longest non-overlapping exact repetition, ignoring punctuation."""
    seen, best = {}, (0, 0, 0)
    for right in range(max(0, len(text) - MINIMUM + 1)):
        seed = text[right:right + MINIMUM]
        if cjk and len(CJK.findall(seed)) < 12:
            continue
        left = seen.setdefault(seed, right)
        if right - left < MINIMUM:
            continue
        length = MINIMUM
        while right + length < len(text) and left + length < right and text[left + length] == text[right + length]:
            length += 1
        if length > best[0]:
            best = length, left, right
    return best


def repetition_issue(sources, targets):
    """Return a bounded repair request only for substantial unsupported repeats.

    Short refrains, matching source repetitions, and identifiers are allowed.
    Source similarity deliberately errs toward preserving author repetition.
    """
    source = [normalized(value) for value in sources]
    target = [normalized(value) if isinstance(value, str) else '' for value in targets]
    for i, value in enumerate(target):
        length, left, _ = repeated_span(value)
        if length:
            original_repeat = repeated_span(source[i], cjk=False)[0]
            # A long repeated passage in the original can legitimately recur
            # in the translation, including within one sentence or quotation.
            if original_repeat >= MINIMUM and original_repeat / max(1, len(source[i])) >= min(.35, length / len(value) * .65):
                continue
            return {'unit': i, 'units': [i], 'sample': value[left:left + min(length, 60)], 'kind': 'within-unit'}
    eligible = [i for i, value in enumerate(target) if len(value) >= 14 and len(CJK.findall(value)) >= 12]
    for pos, i in enumerate(eligible):
        for j in eligible[pos + 1:]:
            a, b = target[i], target[j]
            # One translated source unit must not contain another unit's
            # complete translation. Also catch near copies with small edits.
            matcher = SequenceMatcher(None, a, b, autojunk=False)
            blocks = matcher.get_matching_blocks()
            longest_block = max(blocks, key=lambda block: block.size)
            longest = longest_block.size
            matched = sum(block.size for block in blocks)
            shorter = min(len(a), len(b))
            nonempty = [block for block in blocks if block.size]
            spread = 0
            if nonempty:
                first, last = nonempty[0], nonempty[-1]
                spread = last.a + last.size - first.a if len(a) >= len(b) else last.b + last.size - first.b
            exact_copy = (longest >= MINIMUM and longest >= shorter * .8 and
                          len(CJK.findall(a[longest_block.a:longest_block.a + longest])) >= 12)
            near_copy = (matched >= shorter * (.98 if shorter < MINIMUM else .94) and spread <= shorter * 1.6 and
                         sum(len(CJK.findall(a[block.a:block.a + block.size])) for block in blocks) >= 12)
            if not exact_copy and not near_copy:
                continue
            source_match = SequenceMatcher(None, source[i], source[j], autojunk=False)
            source_shorter = min(len(source[i]), len(source[j]))
            source_blocks = [block for block in source_match.get_matching_blocks() if block.size]
            source_spread = 0
            if source_blocks:
                first, last = source_blocks[0], source_blocks[-1]
                source_spread = (last.a + last.size - first.a if len(source[i]) >= len(source[j])
                                 else last.b + last.size - first.b)
            shared_source_clause = (source_shorter >= 16 and source_spread <= source_shorter * 1.6 and
                                    sum(block.size for block in source_blocks) >= source_shorter * .85)
            if (source_match.ratio() >= .6 or
                source_shorter >= 16 and source_match.find_longest_match().size >= source_shorter * .8 or shared_source_clause):
                continue
            # Copying the paragraph into its first slot usually makes that
            # slot much longer relative to its own source. If tied, repair the
            # later copy. Other, already-correct slots remain untouched.
            culprit = max((i, j), key=lambda k: (len(target[k]) / max(24, len(source[k])), k))
            # Similar-sized copies give no reliable indication of which slot
            # lost its own meaning. Rebuild both implicated slots rather than
            # deleting one or trusting the shorter source-length heuristic.
            repairs = [culprit] if max(len(a), len(b)) >= shorter * 1.6 else [i, j]
            return {'unit': culprit, 'units': [i, j], 'repairUnits': repairs, 'sample': target[culprit][:60], 'kind': 'across-units'}
    return None


def repetition_feedback(issue):
    return ('译文出现原文没有的长段重复，已拒绝缓存。只翻译当前编号片段，不把整段或相邻片段的译文放入本片段；'
            '参考上下文只供理解。原文自己重复的内容应保留。疑似重复：' + issue['sample'])


def repetition_shots(language, editing=False):
    if language not in {'简体中文', '繁體中文'}:
        return []
    source = ['He switched off the device.', 'They moved to the ambush site.']
    target = ['他关闭了装置。', '他们赶往伏击地点。'] if language == '简体中文' else ['他關閉了裝置。', '他們趕往伏擊地點。']
    example = {'source_paragraph': ' '.join(source), 'protected_names': {},
               'units': [{'id': i, 'text': text, 'dialogue': False} for i, text in enumerate(source)]}
    if editing:
        example['draft_units'] = [target[0] + target[1], target[1]]
        example['draft_translation'] = target[0] + target[1] + target[1]
    return [{'role': 'user', 'content': 'ICL example: one source unit per output. Remove only repetitions absent from the source.\n' + json.dumps(example, ensure_ascii=False)},
            {'role': 'assistant', 'content': json.dumps({'translations': target}, ensure_ascii=False)}]
