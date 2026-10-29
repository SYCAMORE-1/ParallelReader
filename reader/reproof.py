"""Durable, explicit chapter/sentence review without discarding existing text."""
import copy
import time
import uuid

from reader.translation_rules import paragraph_plan, person_names, translation_plan


def selected_slots(book, pid, detail, selection):
    source = book['paragraphs'][pid]['text']
    if not detail or ''.join(detail.get('sourceSentences', [])) != source or ''.join(detail.get('targetSentences', [])).strip() != detail.get('text'):
        raise ValueError('当前译文尚无完整句子对应，请先重新校对本章')
    parts, targets = detail['sourceSentences'], detail['targetSentences']
    if len(parts) != len(targets) or detail.get('alignment') != [{'source': [i], 'target': [i]} for i in range(len(parts))]:
        raise ValueError('当前译文尚无精确句子对应，请先重新校对本章')
    start, end = selection.get('start'), selection.get('end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
        raise ValueError('请选择有效的英文句子')
    cursor, slots = 0, []
    for i, part in enumerate(parts):
        following = cursor + len(part)
        if cursor < end and following > start:
            if cursor < start or following > end:
                raise ValueError('选句边界已变化，请重新选择英文句子')
            slots.append(i)
        cursor = following
    if not slots:
        raise ValueError('请选择有效的英文句子')
    return slots


def review_existing(book, pid, settings, snapshot, selection, store):
    # Import lazily: Translator owns the queue but shares its existing workers.
    from reader.translation import proofread, draft_settings, profile, request_json, REVIEW_VERSION, STYLE_VERSION, TERMINOLOGY_VERSION
    full_source = book['paragraphs'][pid]['text']
    fixed = all(piece['fixed'] for piece in paragraph_plan(book, pid, [])['pieces'])
    names = [] if fixed else person_names(book, pid, draft_settings(settings), request_json, store)
    detail = snapshot['detail']
    if selection is None:
        plan = paragraph_plan(book, pid, names)
        result = proofread(book, pid, settings, plan, {'text': snapshot['text']})
        result['proofreading'] = {'model': settings['reviewModel'], 'version': REVIEW_VERSION, 'styleVersion': STYLE_VERSION, 'terminologyVersion': TERMINOLOGY_VERSION, 'draftProfile': profile(draft_settings(settings))}
        return result
    indices = selected_slots(book, pid, detail, selection)
    plan = translation_plan(full_source[selection['start']:selection['end']], names, reference=full_source)
    for piece in plan['pieces']:
        piece['start'] += selection['start']
        piece['end'] += selection['start']
    draft = ''.join(detail['targetSentences'][i] for i in indices)
    revised = proofread(book, pid, settings, plan, {'text': draft})
    first, last = indices[0], indices[-1] + 1
    source = detail['sourceSentences'][:first] + revised['sourceSentences'] + detail['sourceSentences'][last:]
    target = detail['targetSentences'][:first] + revised['targetSentences'] + detail['targetSentences'][last:]
    if ''.join(source) != full_source:
        raise RuntimeError('选句对应校验失败，原译文已保留')
    revision = {'start': selection['start'], 'end': selection['end'], 'model': settings['reviewModel'], 'styleVersion': STYLE_VERSION, 'updated': time.time()}
    revision['terminologyVersion'] = TERMINOLOGY_VERSION
    if revised.get('repetitionCheck'):
        revision['repetitionCheck'] = revised['repetitionCheck']
    if revised.get('militaryTermsVersion'):
        revision['militaryTermsVersion'] = revised['militaryTermsVersion']
    if revised.get('terminology'):
        revision['terminology'] = revised['terminology']
    for key in ['glossary', 'sceneVersion']:
        if revised.get(key):
            revision[key] = revised[key]
    return {**detail, 'text': ''.join(target).strip(), 'sourceSentences': source, 'targetSentences': target,
            'alignment': [{'source': [i], 'target': [i]} for i in range(len(source))], 'alignmentMethod': 'translation-slots',
            'sentenceRevisions': [*detail.get('sentenceRevisions', [])[-19:], revision]}


class Reproofreader:
    def __init__(self, store, review_fn=None, image_translator=None):
        self.store = store
        self.images = image_translator
        self.review_fn = review_fn or (lambda book, pid, settings, snapshot, selection: review_existing(book, pid, settings, snapshot, selection, self.store))
        self.jobs = {job['id']: job for job in store.reproof_jobs()}
        self.active = {}
        self.settings_changed()
        if self.images:
            from reader.translation import profile
            latest = {}
            for job in self.jobs.values():
                if job['scope'] == 'book' and job['profile'] == profile(store.settings()):
                    previous = latest.get(job['book'])
                    if previous is None or (job.get('round', 0), job['created']) > (previous.get('round', 0), previous['created']):
                        latest[job['book']] = job
            for job in latest.values():
                try:
                    book = store.book(job['book'])
                except KeyError:
                    continue
                if self.images.book_jobs.assets(book):
                    self.images.book_jobs.ensure(book, 'review', job['id'], 'running' if job['state'] in {'queued', 'running', 'completed'} else 'paused', job['created'])

    def settings_changed(self):
        from reader.translation import profile
        current = profile(self.store.settings())
        for job in self.jobs.values():
            if job['state'] in {'queued', 'running'} and job['profile'] != current:
                job.update(state='paused', reason='模型设置已变化，原校对任务已暂停')
                self.save(job)

    def save(self, job):
        job['updated'] = time.time()
        self.store.save_reproof_job(job)

    def request(self, book, value):
        from reader.translation import profile
        settings = self.store.settings()
        from reader.providers import text_only
        if text_only(settings):
            raise ValueError('Azure Translate不提供大模型校对；切换到大模型模式后可进行校对')
        fingerprint = profile(settings)
        if value.get('profile', fingerprint) != fingerprint:
            raise ValueError('模型配置已变化，请刷新后重新校对')
        kind = value.get('scope')
        if kind == 'book':
            return self.request_book(book, settings, fingerprint, value.get('action', 'start'))
        selection = None
        if kind == 'chapter':
            chapter = value.get('chapter')
            if type(chapter) is not int or not 0 <= chapter < len(book['chapters']):
                raise ValueError('章节不存在')
            pids = [p['id'] for p in book['paragraphs'] if p['chapter'] == chapter]
        elif kind == 'sentence':
            pid = value.get('paragraph')
            if type(pid) is not int or not 0 <= pid < len(book['paragraphs']):
                raise ValueError('段落不存在')
            chapter = book['paragraphs'][pid]['chapter']
            selection = {'start': value.get('start'), 'end': value.get('end')}
            selected_slots(book, pid, self.store.translation_detail(book['id'], pid, fingerprint), selection)
            pids = [pid]
        else:
            raise ValueError('请选择整本书、章节或英文句子进行校对')
        available = self.store.translations(book['id'], pids, fingerprint)
        if not pids or any(pid not in available for pid in pids):
            raise ValueError('请先完成所选章节或句子的翻译，再重新校对')
        # Identical repeated clicks join the durable job; a failed job retries
        # only missing/failed paragraphs, preserving its successful revisions.
        for job in reversed(list(self.jobs.values())):
            if job.get('parent'):
                continue
            if (job['book'], job['profile'], job['scope'], job['chapter'], job['paragraphs'], job['selection']) == (book['id'], fingerprint, kind, chapter, pids, selection):
                if job['state'] in {'queued', 'running'}:
                    return self.public(job)
                if job['state'] in {'failed', 'paused'}:
                    job.update(state='queued', failures={}, reason='', streak=0)
                    self.save(job)
                    return self.public(job)
                break
        job = {'id': uuid.uuid4().hex, 'book': book['id'], 'profile': fingerprint, 'scope': kind,
               'chapter': chapter, 'paragraphs': pids, 'selection': selection,
               'state': 'queued', 'completed': [], 'failures': {}, 'reason': '', 'model': settings['reviewModel'], 'created': time.time()}
        self.jobs[job['id']] = job
        self.save(job)
        return self.public(job)

    def request_book(self, book, settings, fingerprint, action):
        if action not in {'start', 'pause', 'resume'}:
            raise ValueError('不支持的全书校对操作')
        previous = max((job for job in self.jobs.values()
                        if job['book'] == book['id'] and job['profile'] == fingerprint and job['scope'] == 'book'),
                       key=lambda job: (job.get('round', 0), job['created']), default=None)
        if previous:
            self.sync_book(previous)
            if action == 'pause':
                if previous['state'] in {'queued', 'running'}:
                    self.pause_book(previous, '已暂停；正在处理的段落完成后仍会保存，继续时从未完成处开始。')
                return self.public(previous)
            if previous['state'] in {'queued', 'running'}:
                return self.public(previous)
            if previous['state'] in {'paused', 'failed'}:
                changed = []
                for child in self.children(previous):
                    if child['state'] != 'completed':
                        child.update(state='queued', failures={}, reason='', streak=0, updated=time.time())
                        changed.append(child)
                previous.update(state='queued', reason='', updated=time.time())
                self.store.save_reproof_jobs([*changed, previous])
                if self.images and self.images.book_jobs.assets(book):
                    self.images.book_jobs.action(book, 'resume', 'review', previous['id'], previous['created'])
                return self.public(previous)
            if action == 'resume':
                return self.public(previous)
        elif action != 'start':
            raise ValueError('尚未开始全书校对')
        pids = [p['id'] for p in book['paragraphs']]
        cached = self.store.cached_paragraphs(book['id'], fingerprint)
        if not pids or not all(pid in cached for pid in pids):
            raise ValueError('请先完成整本翻译，再开始全书重新校对')
        created = time.time()
        parent = {'id': uuid.uuid4().hex, 'book': book['id'], 'profile': fingerprint, 'scope': 'book',
                  'round': previous.get('round', 0) + 1 if previous else 1,
                  'chapter': None, 'paragraphs': [], 'selection': None, 'children': [],
                  'state': 'queued', 'completed': [], 'failures': {}, 'reason': '',
                  'model': settings['reviewModel'], 'created': created, 'updated': created}
        by_chapter = {}
        for paragraph in book['paragraphs']:
            by_chapter.setdefault(paragraph['chapter'], []).append(paragraph['id'])
        children = []
        for chapter, paragraphs in by_chapter.items():
            child = {'id': uuid.uuid4().hex, 'book': book['id'], 'profile': fingerprint, 'scope': 'chapter',
                     'chapter': chapter, 'paragraphs': paragraphs, 'selection': None, 'parent': parent['id'],
                     'state': 'queued', 'completed': [], 'failures': {}, 'reason': '',
                     'model': settings['reviewModel'], 'created': created, 'updated': created}
            children.append(child)
            parent['children'].append(child['id'])
        # Cached final translations deliberately do NOT count as completed in
        # this new pass. All paragraphs must go through the editor again.
        self.store.save_reproof_jobs([parent, *children])
        self.jobs.update({job['id']: job for job in [parent, *children]})
        if self.images and self.images.book_jobs.assets(book):
            self.images.book_jobs.action(book, 'start', 'review', parent['id'], parent['created'])
        return self.public(parent)

    def children(self, job):
        return [self.jobs[identity] for identity in job['children']]

    def pause_book(self, job, reason):
        if self.images:
            book = self.store.book(job['book'])
            if self.images.book_jobs.assets(book):
                self.images.book_jobs.action(book, 'pause', 'review', job['id'], job['created'])
        changed = []
        for child in self.children(job):
            if child['state'] in {'queued', 'running'}:
                child.update(state='paused', reason=reason, updated=time.time())
                changed.append(child)
        job.update(state='paused', reason=reason, updated=time.time())
        self.store.save_reproof_jobs([*changed, job])

    def sync_book(self, job):
        children = self.children(job)
        states = {child['state'] for child in children}
        if self.images:
            images = self.images.book_jobs.status(self.store.book(job['book']), 'review', job['id'])
            if images['total']:
                states.add(images['state'])
        if states == {'completed'}:
            state, reason = 'completed', ''
        elif job['state'] == 'paused':
            state, reason = 'paused', job['reason']
        elif 'paused' in states:
            state, reason = 'paused', next((child['reason'] for child in children if child['state'] == 'paused'), '图片任务已暂停，继续后接续处理。')
        elif 'running' in states:
            state, reason = 'running', ''
        elif 'queued' in states:
            state, reason = 'queued', ''
        else:
            state, reason = 'failed', '部分段落未通过校对，原译文保留；点击重试仅处理本轮未成功的段落。'
        if (state, reason) != (job['state'], job['reason']):
            job.update(state=state, reason=reason)
            self.save(job)

    def take(self, active, background_available=True, can_start=None):
        from reader.translation import profile
        settings = self.store.settings()
        fingerprint = profile(settings)
        jobs = sorted(self.jobs.values(), key=lambda job: (job['scope'] != 'sentence', job['created'], job['chapter'] if job['chapter'] is not None else -1))
        for job in jobs:
            if job['scope'] == 'book':
                continue
            if job['state'] not in {'queued', 'running'} or job['profile'] != fingerprint:
                continue
            if job['id'] in self.active.values() or can_start and not can_start(job):
                continue
            if job['scope'] == 'chapter' and not background_available:
                continue
            for pid in job['paragraphs']:
                key = (job['book'], pid, fingerprint)
                if pid in job['completed'] or str(pid) in job['failures'] or key in active:
                    continue
                job['state'] = 'running'
                self.active[key] = job['id']
                self.save(job)
                return key, self.store.book(job['book']), dict(settings), 'revision'
        return None

    def execute(self, key, book, settings):
        job = self.jobs[self.active[key]]
        for attempt in range(2):
            snapshot = self.store.translation_snapshot(*key)
            if not snapshot:
                raise RuntimeError('当前译文已变化，请重新选择')
            result = dict(self.review_fn(book, key[1], settings, copy.deepcopy(snapshot), job['selection']))
            result['lastRevision'] = {'job': job['id'], 'scope': job['scope'], 'updated': time.time()}
            if self.store.replace_translation(*key, snapshot, result, job['id']):
                return
        raise RuntimeError('原译文在校对时已更新，已保留较新结果，请重试')

    def finish(self, key, error):
        job = self.jobs[self.active.pop(key)]
        if error:
            job['failures'][str(key[1])] = str(error)[:350]
            job['streak'] = job.get('streak', 0) + 1
        else:
            job['completed'].append(key[1])
            job['failures'].pop(str(key[1]), None)
            job['streak'] = 0
        if len(job['completed']) + len(job['failures']) == len(job['paragraphs']):
            job['state'] = 'failed' if job['failures'] else 'completed'
        elif error and (any(word in error.lower() for word in ['无法连接', 'timeout', 'timed out']) or job['streak'] >= 3):
            job.update(state='paused', reason='校对已暂停，原译文保留；恢复连接后可右键重新校对，继续未完成部分')
        self.save(job)
        if job.get('parent'):
            parent = self.jobs[job['parent']]
            if error and (job['state'] == 'paused' or any(word in error.lower() for word in ['无法连接', '连接中断', 'timeout', 'timed out'])):
                self.pause_book(parent, '全书校对已暂停，原译文和本轮进度保留；恢复连接或检查段落后可继续。')
            self.sync_book(parent)

    def public(self, job):
        if job['scope'] == 'book':
            self.sync_book(job)
            chapters = [self.public(child) for child in self.children(job)]
            result = {key: job[key] for key in ['id', 'scope', 'chapter', 'state', 'model', 'reason', 'created', 'updated']} | {
                'paragraph': None, 'selection': None, 'activeParagraph': None,
                'round': job.get('round', 1),
                'completed': sum(child['completed'] for child in chapters), 'total': sum(child['total'] for child in chapters),
                'failed': sum(child['failed'] for child in chapters), 'chapters': chapters,
                'completedChapters': sum(child['state'] == 'completed' for child in chapters), 'totalChapters': len(chapters),
                'errors': [error for child in chapters for error in child['errors']][:3]}
            if self.images:
                from reader.image_jobs import merge_progress
                return merge_progress(result, self.images.book_jobs.status(self.store.book(job['book']), 'review', job['id']))
            return result
        active = next((key[1] for key, identity in self.active.items() if identity == job['id']), None)
        return {key: job[key] for key in ['id', 'scope', 'chapter', 'state', 'model', 'reason', 'created', 'updated']} | {
            'paragraph': job['paragraphs'][0] if job['scope'] == 'sentence' else None,
            'selection': job['selection'],
            'completed': len(job['completed']), 'total': len(job['paragraphs']), 'failed': len(job['failures']), 'activeParagraph': active,
            'errors': [{'paragraph': int(pid), 'error': '重新校对失败，原译文已保留：' + error} for pid, error in list(job['failures'].items())[:3]]}

    def status(self, book):
        from reader.translation import profile
        fingerprint = profile(self.store.settings())
        visible = [job for job in self.jobs.values() if job['book'] == book['id'] and job['profile'] == fingerprint and not job.get('parent')]
        parent = max((job for job in visible if job['scope'] == 'book'), key=lambda job: (job.get('round', 0), job['created']), default=None)
        others = sorted((job for job in visible if job['scope'] != 'book'), key=lambda job: (job['state'] not in {'queued', 'running'}, -job['created']))
        return ([self.public(parent)] if parent else []) + [self.public(job) for job in others[:29 if parent else 30]]
