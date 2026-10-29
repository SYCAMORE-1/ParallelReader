const $ = selector => document.querySelector(selector);
const FONTS = {
  georgia: 'Georgia, "Times New Roman", serif', times: '"Times New Roman", Georgia, serif', sans: '"Segoe UI", Arial, sans-serif',
  song: '"Parallel Song", "SimSun", "Songti SC", serif', yahei: '"Parallel Sans", "Microsoft YaHei", "PingFang SC", sans-serif', kai: '"Parallel Kai", "KaiTi", "STKaiti", serif',
};
const DEFAULTS = { en: { size: 19, font: 'georgia', weight: 400, line: 1.85 }, zh: { size: 18, font: 'song', weight: 400, line: 1.85 } };
const DEFAULT_HINT = '';
const THEMES = { light: '#f5f3ed', green: '#d7e3cb', yellow: '#e5ce98', 'deep-green': '#86a78b', dark: '#111311' };
const REDACTION_MARKERS = /\[\s*(?:redacted|已删节|已刪節|机密|機密|已编辑|已編輯|已遮盖|已遮蓋|已删除|已刪除|已隐去|已隱去)(?:[\s:：\-–—]+[x\s]*)?\s*\]|\[\s*(?:x{2,}(?:\s+x+)*|x(?:\s+x+)+)\s*\]|(?<![A-Za-z0-9_])x{2,}(?![A-Za-z0-9_])/gi;

function appendReaderText(parent, text) {
  const ink = value => {
    const mark = document.createElement('span'); mark.className = 'redaction-mark';
    mark.textContent = value; mark.setAttribute('role', 'img'); mark.setAttribute('aria-label', '已遮盖');
    parent.append(mark);
  };
  let cursor = 0;
  for (const match of text.matchAll(REDACTION_MARKERS)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    if (!/^\[\s*x/i.test(match[0])) ink(match[0]);
    else {
      // Each censored word gets its own stroke, including grouped [xxx xxxx].
      const groups = [...match[0].matchAll(/x+/gi)];
      let offset = 0;
      groups.forEach((group, i) => {
        const start = i === 0 ? 0 : group.index;
        const end = i === groups.length - 1 ? match[0].length : group.index + group[0].length;
        parent.append(document.createTextNode(match[0].slice(offset, start)));
        ink(match[0].slice(start, end)); offset = end;
      });
    }
    cursor = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

export function readingTop() {
  const labels = $('.column-labels');
  return (parseFloat(getComputedStyle(labels).top) || 0) + labels.getBoundingClientRect().height;
}

export class ReadingTools {
  constructor({ state, api, icon, element, selectParagraph, updateParagraph, openModel }) {
    Object.assign(this, { state, api, icon, element, selectParagraph, updateParagraph });
    this.pending = []; this.alignments = new Map(); this.failed = new Set(); this.busy = false; this.wordRequest = 0;
    this.appearance = structuredClone(DEFAULTS);
    try {
      const saved = JSON.parse(localStorage.getItem('parallel.appearance') || '{}');
      for (const side of ['en', 'zh']) {
        if (!saved[side]) continue;
        const value = saved[side];
        if (Number.isFinite(value.size)) this.appearance[side].size = Math.max(14, Math.min(32, value.size));
        if (FONTS[value.font] && (side === 'en' ? ['georgia', 'times', 'sans'] : ['song', 'yahei', 'kai']).includes(value.font)) this.appearance[side].font = value.font;
        if ([400, 500, 600, 700].includes(Number(value.weight))) this.appearance[side].weight = Number(value.weight);
        if (Number.isFinite(value.line)) this.appearance[side].line = Math.max(1.4, Math.min(2.4, value.line));
      }
    } catch { /* A malformed preference must not prevent opening a book. */ }
    for (const side of ['en', 'zh']) for (const field of ['size', 'font', 'weight', 'line']) {
      const control = $(`#${side}-${field}`);
      control.value = this.appearance[side][field];
      const update = () => {
        const value = field === 'font' ? control.value : Number(control.value);
        if (this.appearance[side][field] === value) return;
        this.appearance[side][field] = value; this.applyAppearance();
      };
      control.addEventListener('input', update);
      control.addEventListener('change', update);
    }
    $('#reset-appearance').onclick = () => { this.appearance = structuredClone(DEFAULTS); this.applyAppearance(true); };
    this.applyAppearance(true);
    this.setTheme(localStorage.getItem('parallel.theme') || 'light');
    for (const button of document.querySelectorAll('.theme-choices button[data-theme]')) button.onclick = () => this.setTheme(button.dataset.theme);
    $('#reader-settings-button').onclick = $('#settings-button').onclick = () => this.toggleSettings();
    $('#close-reading-settings').onclick = () => this.toggleSettings(false);
    $('#appearance-tab').onclick = () => {
      const visible = $('#appearance-panel').hidden;
      $('#appearance-panel').hidden = !visible;
      $('.appearance-note').hidden = !visible;
      $('#appearance-tab').classList.toggle('active', visible);
      $('#appearance-tab').setAttribute('aria-pressed', String(visible));
    };
    $('#model-settings-button').onclick = openModel;
    $('#immersive-button').onclick = () => this.setImmersive(true);
    $('#exit-immersive').onclick = () => this.setImmersive(false);
    document.addEventListener('fullscreenchange', () => {
      if (this.nativeFullscreen && !document.fullscreenElement) { this.nativeFullscreen = false; this.setImmersive(false); }
    });
    const columns = localStorage.getItem('parallel.columns');
    this.setColumns(['original', 'translation', 'follow'].includes(columns) ? columns : 'both');
    $('#show-original').onclick = () => this.setColumns('original');
    $('#show-both').onclick = () => this.setColumns('both');
    $('#show-translation').onclick = () => this.setColumns('translation');
    $('#show-follow').onclick = () => this.setColumns('follow');
    $('#paragraphs').addEventListener('click', event => {
      const sentence = event.target.closest('.sentence');
      if (!sentence) return;
      clearTimeout(this.clickTimer);
      if (event.detail > 1) return;
      // Wait briefly to distinguish sentence selection from a word double-click.
      this.clickTimer = setTimeout(() => this.choose(sentence), sentence.dataset.side === 'source' ? 210 : 0);
    });
    $('#paragraphs').addEventListener('keydown', event => {
      const sentence = event.target.closest('.sentence');
      if (sentence && ['Enter', ' '].includes(event.key)) { event.preventDefault(); this.choose(sentence); }
    });
    $('#paragraphs').addEventListener('dblclick', event => {
      if (!event.target.closest('.original')) return;
      clearTimeout(this.clickTimer);
      const sentence = event.target.closest('.sentence');
      if (!sentence) return;
      const word = this.wordAt(event, sentence);
      if (word) this.showWord(Number(sentence.closest('.paragraph-row').dataset.pid), word.word, event.clientX, event.clientY, word.offset);
    });
    $('#close-word').onclick = () => this.closeWord();
    $('#retry-word').onclick = () => { if (this.word) this.showWord(this.word.pid, this.word.word, this.word.x, this.word.y, this.word.offset); };
    document.addEventListener('pointerdown', event => { if (!event.target.closest('#word-popover')) this.closeWord(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        const wordWasOpen = !$('#word-popover').hidden;
        this.closeWord(); this.toggleSettings(false);
        if (this.immersive && !wordWasOpen && !document.querySelector('dialog[open]')) this.setImmersive(false);
      }
    });
    window.addEventListener('resize', () => this.closeWord());
    window.addEventListener('scroll', () => this.closeWord(), { passive: true });
  }

  applyAppearance(sync = false) {
    for (const side of ['en', 'zh']) for (const [field, value] of Object.entries(this.appearance[side])) {
      document.documentElement.style.setProperty(`--${side}-${field}`, field === 'font' ? FONTS[value] : field === 'size' ? value + 'px' : value);
      if (sync) $(`#${side}-${field}`).value = value;
      if (field === 'size' || field === 'line') $(`#${side}-${field}-value`).textContent = field === 'size' ? value + ' px' : Number(value).toFixed(1);
    }
    localStorage.setItem('parallel.appearance', JSON.stringify(this.appearance));
    document.dispatchEvent(new CustomEvent('reading-layout-change'));
  }

  toggleSettings(force) {
    const visible = force ?? $('#reading-settings').hidden;
    $('#reading-settings').hidden = !visible;
    $('#reader-settings-button').setAttribute('aria-expanded', String(visible));
    document.body.classList.remove('mobile-sidebar');
    this.closeWord();
    if (visible) $('#appearance-tab').focus({ preventScroll: true });
  }

  setTheme(theme) {
    if (!Object.hasOwn(THEMES, theme)) theme = 'light';
    document.documentElement.classList.remove(...Object.keys(THEMES));
    if (theme !== 'light') document.documentElement.classList.add(theme);
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = THEMES[theme];
    localStorage.setItem('parallel.theme', theme);
    for (const button of document.querySelectorAll('.theme-choices button[data-theme]')) button.setAttribute('aria-pressed', String(button.dataset.theme === theme));
  }

  setColumns(mode) {
    const sourceOnly = !!this.state.book?.sourceOnly;
    if (sourceOnly) mode = 'original';
    if (!['both', 'original', 'translation', 'follow'].includes(mode)) mode = 'both';
    this.columns = mode; $('#reader').dataset.columns = mode;
    if (!sourceOnly) localStorage.setItem('parallel.columns', mode);
    for (const [id, value] of [['#show-both', 'both'], ['#show-original', 'original'], ['#show-translation', 'translation'], ['#show-follow', 'follow']]) {
      $(id).setAttribute('aria-pressed', String(mode === value));
      $(id).disabled = sourceOnly && value !== 'original';
      $(id).title = sourceOnly ? (value === 'original' ? '直接阅读' : '本书可直接阅读，无需翻译') : $(id).getAttribute('aria-label');
    }
    $('.column-switch').style.setProperty('--selection-index', ['original', 'translation', 'both', 'follow'].indexOf(mode));
    this.closeWord();
    if (mode === 'both' || mode === 'follow') this.preparePage();
    document.dispatchEvent(new CustomEvent('reading-layout-change'));
  }

  async setImmersive(enabled) {
    if (enabled && !this.state.book) return;
    const anchor = [...document.querySelectorAll('.paragraph-row,.illustration-row')].find(row => row.getBoundingClientRect().bottom > $('.column-labels').getBoundingClientRect().bottom + 12);
    this.immersive = enabled;
    document.body.classList.toggle('immersive', enabled);
    $('#exit-immersive').hidden = !enabled;
    $('#immersive-navigation').hidden = !enabled;
    $('#immersive-button').setAttribute('aria-pressed', String(enabled));
    this.toggleSettings(false); this.closeWord();
    if (enabled) {
      // Embedded browsers may deny native fullscreen. The in-app layout still
      // fills its complete viewport and keeps every reading interaction.
      if (document.fullscreenEnabled && !document.fullscreenElement) {
        try { await document.documentElement.requestFullscreen(); this.nativeFullscreen = true; }
        catch { this.nativeFullscreen = false; }
      }
    } else if (this.nativeFullscreen && document.fullscreenElement) {
      this.nativeFullscreen = false;
      try { await document.exitFullscreen(); } catch { /* in-app layout is already restored */ }
    }
    document.dispatchEvent(new CustomEvent('reading-layout-change'));
    if (document.body.dataset.flow === 'scroll' && anchor) requestAnimationFrame(() => window.scrollTo({ top: scrollY + anchor.getBoundingClientRect().top - readingTop() - 4, behavior: 'instant' }));
    else if (enabled) window.scrollTo({ top: 0, behavior: 'instant' });
  }

  split(text, locale = 'en') {
    if (Intl.Segmenter) return [...new Intl.Segmenter(locale, { granularity: 'sentence' }).segment(text)].map(part => part.segment);
    return [text];
  }

  sentences(cell, parts, side) {
    const signature = JSON.stringify(parts);
    if (cell.dataset.sentences === signature && cell.querySelector('.sentence')) return;
    cell.dataset.sentences = signature; cell.replaceChildren();
    let offset = 0;
    parts.forEach((text, index) => {
      const span = this.element('span', 'sentence');
      appendReaderText(span, text);
      Object.assign(span.dataset, { side, index, start: offset });
      span.tabIndex = 0; span.setAttribute('role', 'button'); span.setAttribute('aria-pressed', 'false');
      span.setAttribute('aria-label', (side === 'source' ? '原文句子：' : '译文句子：') + text.trim().replace(REDACTION_MARKERS, '〔已遮盖〕'));
      cell.append(span); offset += text.length;
    });
  }

  cacheKey(pid, text) { return `${this.state.book?.id}:${this.state.profile}:${pid}:${text}`; }

  decorate(pid, item) {
    if (item?.state !== 'ready') return item;
    const key = this.cacheKey(pid, item.text);
    if (item.alignment) this.alignments.set(key, { sourceSentences: item.sourceSentences, targetSentences: item.targetSentences, alignment: item.alignment });
    else if (this.alignments.has(key)) Object.assign(item, this.alignments.get(key));
    return item;
  }

  render(pid, row, item) {
    this.decorate(pid, item);
    const original = this.state.book.paragraphs[pid].text;
    this.sentences(row.querySelector('.original'), item.sourceSentences || this.split(original), 'source');
    if (item.state === 'ready') this.sentences(row.querySelector('.translation'), item.targetSentences || this.split(item.text, 'zh'), 'target');
    this.highlight();
  }

  choose(sentence) {
    if (!sentence.isConnected || !sentence.closest('.paragraph-row')) return;
    const pid = Number(sentence.closest('.paragraph-row').dataset.pid);
    this.active = { pid, side: sentence.dataset.side, index: Number(sentence.dataset.index), offset: Number(sentence.dataset.start) };
    this.selectParagraph(pid);
    this.highlight();
    if (this.state.items[pid]?.state === 'ready' && !this.state.items[pid]?.alignment) {
      $('#sentence-hint').textContent = '正在确定句子对应关系…';
      this.queueAlignment(pid, true);
    }
  }

  highlight() {
    for (const span of document.querySelectorAll('.sentence-linked, .sentence-selected')) {
      span.classList.remove('sentence-linked', 'sentence-selected'); span.setAttribute('aria-pressed', 'false');
    }
    if (!this.active) return;
    const row = $(`.paragraph-row[data-pid="${this.active.pid}"]`);
    if (!row) return;
    const sameSide = [...row.querySelectorAll(`.sentence[data-side="${this.active.side}"]`)];
    const clicked = sameSide.find(span => Number(span.dataset.start) <= this.active.offset && Number(span.dataset.start) + span.textContent.length > this.active.offset) || sameSide[this.active.index];
    if (!clicked) return;
    const index = Number(clicked.dataset.index);
    const groups = this.state.items[this.active.pid]?.alignment;
    const group = groups?.find(group => group[this.active.side].includes(index));
    clicked.classList.add('sentence-selected'); clicked.setAttribute('aria-pressed', 'true');
    if (group) {
      for (const span of row.querySelectorAll('.sentence')) {
        if (group[span.dataset.side].includes(Number(span.dataset.index))) { span.classList.add('sentence-linked'); span.setAttribute('aria-pressed', 'true'); }
      }
      $('#sentence-hint').textContent = DEFAULT_HINT;
    }
  }

  preparePage() {
    if (!this.state.book || this.state.book.sourceOnly || !['both', 'follow'].includes(this.columns)) return;
    for (const row of document.querySelectorAll('.paragraph-row')) {
      const pid = Number(row.dataset.pid);
      const item = this.decorate(pid, this.state.items[pid]);
      if (item?.state === 'ready' && !item.alignment && !this.failed.has(this.cacheKey(pid, item.text))) this.queueAlignment(pid);
    }
  }

  queueAlignment(pid, urgent = false) {
    const item = this.state.items[pid]; if (item?.state !== 'ready' || item.alignment) return;
    const key = this.cacheKey(pid, item.text);
    if (urgent) this.failed.delete(key);
    if (this.failed.has(key) || this.activeJob?.key === key) return;
    const existing = this.pending.findIndex(job => job.key === key);
    if (existing !== -1) { if (!urgent) return; this.pending.splice(existing, 1); }
    const job = { key, pid, epoch: this.state.epoch, book: this.state.book.id, profile: this.state.profile, text: item.text };
    urgent ? this.pending.unshift(job) : this.pending.push(job);
    this.drain();
  }

  async drain() {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.pending.length) {
        const job = this.pending.shift();
        if (job.epoch !== this.state.epoch) continue;
        this.activeJob = job;
        try {
          const result = await this.api(`/api/books/${job.book}/align`, { paragraph: job.pid, translationProfile: this.state.items[job.pid]?.translationProfile });
          this.alignments.set(job.key, result);
          if (job.epoch !== this.state.epoch || this.state.profile !== job.profile || this.state.items[job.pid]?.text !== job.text) continue;
          Object.assign(this.state.items[job.pid], result);
          this.updateParagraph(job.pid);
        } catch {
          this.failed.add(job.key);
          if (job.epoch === this.state.epoch && this.active?.pid === job.pid) $('#sentence-hint').textContent = '句子对应暂不可用，重新点击句子可重试。';
        } finally { this.activeJob = null; }
      }
    } finally { this.busy = false; }
  }

  resetPage() {
    this.active = null; this.pending = []; clearTimeout(this.clickTimer); this.closeWord();
    $('#sentence-hint').textContent = DEFAULT_HINT;
  }

  wordAt(event, sentence) {
    if (event.target.closest?.('.redaction-mark')) return null;
    const hit = (word, relativeStart) => {
      const pid = Number(sentence.closest('.paragraph-row').dataset.pid);
      const codeUnits = Number(sentence.dataset.start) + relativeStart;
      // API offsets use Unicode code points; browser selection uses UTF-16.
      return { word, offset: Array.from(this.state.book.paragraphs[pid].text.slice(0, codeUnits)).length };
    };
    const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
    if (range?.startContainer.nodeType === Node.TEXT_NODE && sentence.contains(range.startContainer)) {
      if (range.startContainer.parentElement.closest('.redaction-mark')) return null;
      const text = range.startContainer.textContent, offset = range.startOffset;
      for (const match of text.matchAll(/[A-Za-z]+(?:[’'\-][A-Za-z]+)*/g)) {
        if (match.index <= offset && match.index + match[0].length >= offset) {
          const prefix = document.createRange(); prefix.selectNodeContents(sentence); prefix.setEnd(range.startContainer, match.index);
          return hit(match[0], prefix.toString().length);
        }
      }
    }
    const selected = window.getSelection()?.toString().trim().replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '') || '';
    if (!/^[A-Za-z]+(?:[’'\-][A-Za-z]+)*$/.test(selected)) return null;
    const selection = window.getSelection();
    const selectedRange = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!selectedRange || !sentence.contains(selectedRange.startContainer) || !sentence.contains(selectedRange.endContainer) || selectedRange.startContainer.parentElement?.closest('.redaction-mark')) return null;
    const prefix = document.createRange(); prefix.selectNodeContents(sentence); prefix.setEnd(selectedRange.startContainer, selectedRange.startOffset);
    const start = prefix.toString().length + Math.max(0, selectedRange.toString().toLowerCase().indexOf(selected.toLowerCase()));
    return start >= 0 ? hit(selected, start) : null;
  }

  positionWord() {
    const card = $('#word-popover'), padding = 12;
    const rect = card.getBoundingClientRect();
    card.style.left = Math.max(padding, Math.min(this.word.x, innerWidth - rect.width - padding)) + 'px';
    const below = this.word.y + 18;
    card.style.top = Math.max(padding, below + rect.height < innerHeight - padding ? below : this.word.y - rect.height - 12) + 'px';
  }

  async showWord(pid, word, x, y, offset = null) {
    if (this.state.book?.sourceOnly) return;
    const request = ++this.wordRequest, epoch = this.state.epoch;
    this.word = { pid, word, x, y, offset }; $('#word-popover').hidden = false;
    $('#word-title').textContent = word; $('#word-lemma').textContent = ''; $('#word-pos').hidden = true;
    $('#word-definitions').hidden = true; $('#word-status').hidden = false;
    $('#word-status').textContent = '正在查询基本词义和语境含义…';
    $('#word-basic').textContent = ''; $('#word-meaning').textContent = ''; $('#word-note').textContent = ''; $('#retry-word').hidden = true;
    $('#word-context').textContent = '由 ' + (this.state.settings.wordProvider === 'llm' ? 'LLM' : 'Azure Translate') + ' 提供';
    $('#word-popover').classList.remove('word-error'); this.positionWord();
    try {
      const result = await this.api(`/api/books/${this.state.book.id}/word`, { paragraph: pid, word, offset });
      if (request !== this.wordRequest || epoch !== this.state.epoch) return;
      $('#word-lemma').textContent = result.lemma.toLowerCase() !== word.toLowerCase() ? '原形 ' + result.lemma : '';
      $('#word-pos').textContent = result.partOfSpeech; $('#word-pos').hidden = !result.partOfSpeech;
      $('#word-pos').title = result.provider === 'microsoft' ? '词典常见词性' : '当前用法的词性';
      document.querySelector('.contextual-definition h3').textContent = result.provider === 'microsoft' ? result.contextKind === 'sentence' ? '所在句译文' : '文中译法' : '文中含义';
      $('#word-basic').textContent = result.basicMeaning; $('#word-meaning').textContent = result.contextMeaning;
      $('#word-note').textContent = result.note; $('#word-note').hidden = !result.note;
      $('#word-status').hidden = true; $('#word-definitions').hidden = false;
      $('#word-context').textContent = '由 ' + (result.provider === 'llm' ? 'LLM' : 'Azure Translate') + ' 提供';
      $('#word-context').title = result.contextWindow ? `当前句 + 前 ${result.contextWindow.before} 句 · 不读取后文` : '仅参考当前句和之前最多 10 句';
      this.positionWord();
    } catch (error) {
      if (request !== this.wordRequest || epoch !== this.state.epoch) return;
      $('#word-popover').classList.add('word-error'); $('#word-status').textContent = error.message;
      $('#retry-word').hidden = false; this.positionWord();
    }
  }

  closeWord() { ++this.wordRequest; $('#word-popover').hidden = true; }
}
