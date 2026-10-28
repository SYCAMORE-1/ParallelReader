"""Durable whole-book image work shared by pretranslation and review rounds."""
import hashlib
import json
import threading
import time
from reader.providers import images_enabled


def merge_progress(text, images):
    if not images['total']:
        return text
    result = dict(text)
    result.update(textCompleted=text['completed'], textTotal=text['total'], textDrafted=text.get('drafted', text['completed']),
                  imageCompleted=images['completed'], imageTotal=images['total'], imageFailed=images['failed'])
    result['completed'] = text['completed'] + images['completed']
    result['total'] = text['total'] + images['total']
    result['percent'] = 100.0 if result['completed'] == result['total'] else min(99.9, round(result['completed'] * 100 / max(1, result['total']), 1))
    result['failed'] = text.get('failed', 0) + images['failed']
    result['drafted'] = text.get('drafted', text['completed']) + images.get('drafted', images['completed'])
    state = text.get('state', 'completed' if text['completed'] == text['total'] else 'idle')
    states = {state, images['state']}
    result['state'] = ('completed' if result['completed'] == result['total'] else 'paused' if 'paused' in states
                       else 'running' if states & {'running', 'queued'} else 'failed' if 'failed' in states else 'idle')
    if images.get('reason') and images['state'] in {'paused', 'failed'}:
        result['reason'] = images['reason']
    if images.get('errors'):
        result['errors'] = [*text.get('errors', []), *images['errors']][:6]
    return result


class ImageBookJobs:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.records = {}
        with self.store.lock:
            values = [json.loads(row[0]) for row in self.store.db.execute("SELECT value FROM language_cache WHERE key LIKE 'image-book-job:%'")]
        for record in values:
            self.records[record['key']] = record
        self.thread = threading.Thread(target=self.run, name='image-book-scheduler', daemon=True)
        self.thread.start()

    def identity(self, book, kind, token):
        from reader.image_translation import image_profile
        fingerprint = image_profile(self.store.settings())
        return 'image-book-job:' + hashlib.sha256(json.dumps([book['id'], fingerprint, kind, token]).encode()).hexdigest(), fingerprint

    def save(self, record):
        record['updated'] = time.time()
        self.store.cache_language_result(record['key'], record)

    def ensure(self, book, kind='bulk', token='', state='running', started=None):
        key, fingerprint = self.identity(book, kind, token)
        with self.lock:
            if key not in self.records:
                record = {'key': key, 'book': book['id'], 'profile': fingerprint, 'kind': kind, 'token': token,
                          'state': state, 'completed': [], 'failures': {}, 'started': started or time.time(), 'reason': ''}
                self.records[key] = record
                self.save(record)
            return self.records[key]

    def action(self, book, action, kind='bulk', token='', started=None):
        if action not in {'start', 'resume', 'retry', 'pause'}:
            raise ValueError('不支持的图片整书操作')
        with self.lock:
            record = self.ensure(book, kind, token, state='paused', started=started)
            if action == 'pause':
                record.update(state='paused', reason='图片任务已暂停，正在处理的图片完成后会保存。')
            else:
                record.update(state='running', failures={}, reason='', streak=0, attemptStarted=time.time())
            self.save(record)
            return self.status(book, kind, token)

    def assets(self, book):
        if not images_enabled(self.store.settings()):
            return []
        return list(dict.fromkeys(item['asset'] for item in book.get('illustrations', [])))

    def refresh(self, book, record=None):
        settings = self.store.settings()
        assets = self.assets(book)
        done, drafted = [], 0
        for asset in assets:
            result = self.service.ready_result(book, asset, settings)
            if result and (not record or record['kind'] == 'bulk' or asset in record['completed'] or result['created'] >= record['started']):
                done.append(asset)
            if result or self.store.language_result(self.service.key(book, asset, settings) + ':draft'):
                drafted += 1
        failures = {} if record is None else {asset: error for asset, error in record['failures'].items() if asset not in done}
        state = record['state'] if record else 'idle'
        if len(done) == len(assets):
            state = 'completed'
        elif state == 'running' and len(done) + len(failures) == len(assets):
            state = 'failed'
        if record and (record['completed'] != done or record['failures'] != failures or record['state'] != state):
            if set(done) - set(record['completed']):
                record['streak'] = 0
            record.update(completed=done, failures=failures, state=state)
            self.save(record)
        return {'state': state, 'completed': len(done), 'total': len(assets), 'drafted': drafted, 'failed': len(failures),
                'reason': record.get('reason', '') if record else '',
                'errors': [{'image': asset, 'error': error} for asset, error in list(failures.items())[:3]]}

    def status(self, book, kind='bulk', token=''):
        key, _ = self.identity(book, kind, token)
        with self.lock:
            return self.refresh(book, self.records.get(key))

    def library(self, stats):
        for book_id, value in list(stats.items()):
            try:
                book = self.store.book(book_id)
                kind = 'review' if value.get('mode') == 'reproof' else 'bulk'
                images = self.status(book, kind, value.get('reproofId', '') if kind == 'review' else '')
                stats[book_id] = merge_progress(value, images)
            except KeyError:
                continue
        return stats

    def pause_book(self, book_id):
        with self.lock:
            for record in self.records.values():
                if record['book'] == book_id and record['state'] == 'running':
                    record.update(state='paused', reason='图书已从书架移除')
                    self.save(record)

    def run(self):
        from reader.image_translation import image_profile
        while not self.stop.wait(.5):
            if not images_enabled(self.store.settings()):
                continue
            with self.lock:
                records = list(self.records.values())
                for record in records:
                    if record['state'] != 'running' or record['profile'] != image_profile(self.store.settings()):
                        continue
                    try:
                        book = self.store.book(record['book'])
                    except KeyError:
                        record.update(state='paused', reason='图书已从书架移除'); self.save(record); continue
                    self.refresh(book, record)
                    if record['state'] != 'running':
                        continue
                    for asset in self.assets(book):
                        if asset in record['completed'] or asset in record['failures']:
                            continue
                        status = self.service.status(book, asset)
                        if status['state'] == 'error' and status.get('updated', 0) >= record.get('attemptStarted', record['started']):
                            record['failures'][asset] = status['error']
                            record['streak'] = record.get('streak', 0) + 1
                            self.save(record)
                            if '无法连接' in status['error'] or record['streak'] >= 3:
                                record.update(state='paused', reason='图片处理连续失败，已暂停；已有译图保留，继续时只重试未完成图片。'); self.save(record)
                                break
                            continue
                        with self.service.lock:
                            active = sum(job['book'] == book['id'] and job['state'] in {'queued', 'recognizing', 'proofreading', 'rendering'} for job in self.service.jobs.values())
                        if active >= self.service.parallel_images:
                            break
                        try:
                            self.service.request(book, asset, review_only=record['kind'] == 'review')
                        except (RuntimeError, ValueError, KeyError) as exc:
                            record.update(state='paused', reason=str(exc)[:600]); self.save(record)
                            break

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
