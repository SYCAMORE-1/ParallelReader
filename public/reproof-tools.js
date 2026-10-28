import { readingTop } from './reading-tools.js';

export class ReproofTools {
  constructor({ state, api, toast, report, reading, refresh }) {
    Object.assign(this, { state, api, toast, report, reading, refresh });
    this.jobs = []; this.seen = new Map(); this.target = null;
    this.menu = document.createElement('div');
    this.menu.id = 'reproof-menu'; this.menu.className = 'reproof-menu'; this.menu.hidden = true; this.menu.setAttribute('role', 'menu');
    this.label = document.createElement('div'); this.label.className = 'reproof-menu-label'; this.menu.append(this.label);
    for (const [scope, text] of [['sentence', '重新校对选中句子'], ['chapter', '重新校对本章']]) {
      const button = document.createElement('button'); button.id = 'reproof-' + scope;
      button.type = 'button'; button.textContent = text; button.setAttribute('role', 'menuitem');
      button.onclick = () => this.submit(scope); this.menu.append(button); this[scope + 'Button'] = button;
    }
    document.body.append(this.menu);
    document.addEventListener('contextmenu', event => this.open(event));
    document.addEventListener('pointerdown', event => { if (!this.menu.contains(event.target)) this.hide(); });
    document.addEventListener('scroll', () => this.hide(), true);
    window.addEventListener('resize', () => this.hide());
    document.addEventListener('keydown', event => {
      if (this.menu.hidden) return;
      if (event.key === 'Tab') { this.hide(); return; }
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); this.hide(true); }
      if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); event.stopImmediatePropagation();
        const buttons = [...this.menu.querySelectorAll('button:not([hidden]):not(:disabled)')];
        let index = buttons.indexOf(document.activeElement);
        index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[index]?.focus({ preventScroll: true });
      }
    }, true);
  }

  hide(restore = false) {
    this.menu.hidden = true;
    if (restore && this.focusBefore?.isConnected) this.focusBefore.focus({ preventScroll: true });
  }

  reset() { this.hide(); this.jobs = []; }

  open(event) {
    if (['azure', 'microsoft'].includes(this.state.settings.translationProvider)) return;
    if (!this.state.book) return;
    const chapterNode = event.target.closest?.('.toc-item[data-chapter]');
    const sentence = event.target.closest?.('.original .sentence[data-side="source"]');
    if (!chapterNode && !sentence) return;
    event.preventDefault(); this.reading.closeWord();
    const book = this.state.book;
    let selection = null, chapter;
    if (chapterNode) chapter = Number(chapterNode.dataset.chapter);
    else {
      const cell = sentence.closest('.original'), pid = Number(sentence.closest('.paragraph-row').dataset.pid);
      chapter = book.paragraphs[pid].chapter;
      let chosen = [sentence];
      const selected = window.getSelection();
      if (selected && !selected.isCollapsed && selected.toString().trim()) {
        const range = selected.getRangeAt(0);
        if (!cell.contains(range.startContainer) || !cell.contains(range.endContainer)) {
          this.hide(); this.toast('请在同一段英文内选句，再右键校对'); return;
        }
        const prefix = document.createRange(); prefix.selectNodeContents(cell); prefix.setEnd(range.startContainer, range.startOffset);
        const start = prefix.toString().length;
        prefix.setEnd(range.endContainer, range.endOffset); const end = prefix.toString().length;
        chosen = [...cell.querySelectorAll('.sentence')].filter(node => Number(node.dataset.start) < end && Number(node.dataset.start) + node.textContent.length > start);
      }
      if (!chosen.length) return;
      const original = book.paragraphs[pid].text;
      const start = Number(chosen[0].dataset.start), last = chosen.at(-1), end = Number(last.dataset.start) + last.textContent.length;
      selection = { paragraph: pid, start: Array.from(original.slice(0, start)).length, end: Array.from(original.slice(0, end)).length, count: chosen.length };
    }
    this.target = { book: book.id, profile: this.state.profile, chapter, selection };
    this.focusBefore = document.activeElement;
    this.label.textContent = book.chapters[chapter].title;
    this.sentenceButton.hidden = !selection;
    this.sentenceButton.textContent = selection?.count > 1 ? `重新校对选中句子（${selection.count} 句）` : '重新校对选中句子';
    this.sentenceButton.disabled = !!selection && this.state.items[selection.paragraph]?.state !== 'ready';
    const active = this.jobs.find(job => job.scope === 'chapter' && job.chapter === chapter && ['queued', 'running'].includes(job.state));
    this.chapterButton.disabled = !!active;
    this.chapterButton.textContent = active ? `本章正在校对（${active.completed}/${active.total}）` : '重新校对本章';
    this.menu.hidden = false;
    const rect = (chapterNode || sentence).getBoundingClientRect();
    this.menu.style.left = Math.max(8, Math.min(event.clientX || rect.left, innerWidth - this.menu.offsetWidth - 8)) + 'px';
    this.menu.style.top = Math.max(8, Math.min(event.clientY || rect.bottom, innerHeight - this.menu.offsetHeight - 8)) + 'px';
    this.menu.querySelector('button:not([hidden]):not(:disabled)')?.focus({ preventScroll: true });
  }

  async submit(scope) {
    const target = this.target; this.hide();
    if (!target) return;
    try {
      const payload = { scope, profile: target.profile, ...(scope === 'chapter' ? { chapter: target.chapter } : target.selection) };
      const job = await this.api(`/api/books/${target.book}/reproofread`, payload);
      if (this.state.book?.id === target.book) {
        this.update([job, ...this.jobs.filter(old => old.id !== job.id)]);
        this.refresh();
      }
      this.toast(scope === 'chapter' ? '已开始重新校对本章，结果会逐段就地保存' : '已开始校对选中句子，完成后就地保存');
    } catch (error) { this.report(error); }
  }

  update(jobs = []) {
    this.jobs = jobs;
    for (const job of jobs) {
      const previous = this.seen.get(job.id);
      if (previous && previous !== job.state && ['completed', 'failed'].includes(job.state)) {
        const subject = job.scope === 'book' ? '全书' : job.scope === 'chapter' ? '本章' : '选中句子';
        this.toast(job.state === 'completed' ? `${subject}已重新校对并保存` : `有 ${job.failed} 段校对失败，原译文已保留；${job.scope === 'book' ? '可在全书校对处重试' : '可右键重试'}`);
      }
      if (previous && previous !== job.state && job.state === 'paused') this.toast(job.reason || '校对已暂停，原译文已保留');
      this.seen.set(job.id, job.state);
    }
    this.decorateToc();
  }

  decorateToc() {
    const priority = { running: 0, queued: 1, paused: 2, failed: 3, completed: 4 };
    const jobs = this.jobs.flatMap(job => job.scope === 'book' ? job.chapters || [] : [job])
      .sort((a, b) => priority[a.state] - priority[b.state] || b.created - a.created);
    for (const button of document.querySelectorAll('.toc-item[data-chapter]')) {
      const job = jobs.find(job => job.scope === 'chapter' && job.chapter === Number(button.dataset.chapter));
      let badge = button.querySelector('.reproof-chapter-status');
      if (!job) { badge?.remove(); continue; }
      if (!badge) { badge = document.createElement('small'); badge.className = 'reproof-chapter-status'; button.append(badge); }
      badge.textContent = job.state === 'completed' ? '已重新校对' : job.state === 'failed' ? `${job.failed} 段待重试` : job.state === 'paused' ? '校对已暂停' : `校对 ${job.completed}/${job.total}`;
      badge.title = `重新校对 ${job.completed}/${job.total} · ${job.model}`;
    }
  }

  anchorBeforeUpdate(items) {
    if (!Object.entries(items).some(([pid, item]) => item.state === 'ready' && this.state.items[pid]?.state === 'ready' && this.state.items[pid].text !== item.text)) return () => {};
    const top = readingTop();
    let node = [...document.querySelectorAll('.original .sentence')].find(node => { const rect = node.getBoundingClientRect(); return rect.bottom > top && rect.top < innerHeight - 60; });
    node ||= [...document.querySelectorAll('.paragraph-row')].find(node => { const rect = node.getBoundingClientRect(); return rect.bottom > top && rect.top < innerHeight - 60; });
    if (!node) return () => {};
    const before = node.getBoundingClientRect().top, epoch = this.state.epoch;
    const pid = node.closest('.paragraph-row').dataset.pid, start = node.dataset.start;
    return () => {
      if (epoch !== this.state.epoch) return;
      const after = node.isConnected ? node : document.querySelector(`.paragraph-row[data-pid="${pid}"] .original .sentence[data-start="${start}"]`) || document.querySelector(`.paragraph-row[data-pid="${pid}"]`);
      if (after) window.scrollBy({ top: after.getBoundingClientRect().top - before, behavior: 'instant' });
    };
  }
}
