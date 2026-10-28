const BUSY = ['queued', 'recognizing', 'proofreading', 'rendering'];
export class ImageTranslationTools {
  constructor({ state, api, element }) {
    Object.assign(this, { state, api, element }); this.entries = new Map();
    this.observer = new IntersectionObserver(items => {
      for (const item of items) if (item.isIntersecting) {
        this.observer.unobserve(item.target); this.load(item.target.imageTranslationEntry);
      }
    }, { rootMargin: '300px' });
    document.addEventListener('reading-layout-change', () => {
      for (const entry of this.entries.values()) { entry.choice = null; this.render(entry); }
    });
  }
  live(entry) {
    entry.views = entry.views.filter(view => {
      if (view.controls.isConnected) view.seenConnected = true;
      return view.controls.isConnected || !view.seenConnected;
    });
    return entry.views;
  }
  attach(host, bookId, asset, { image, originalSrc } = {}) {
    const key = `${bookId}:${asset}:${this.state.profile}`;
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { url: `/api/books/${bookId}/image-translations/${asset}`, value: { state: 'idle' }, views: [], choice: null };
      this.entries.set(key, entry);
    }
    const controls = this.element('div', 'image-translation-controls');
    const button = this.element('button', 'image-translation-action', '翻译图片'); button.type = 'button';
    const switches = this.element('div', 'image-variant-switch'); switches.setAttribute('role', 'group'); switches.setAttribute('aria-label', '图片版本');
    const original = this.element('button', 'image-original', '原图'), translated = this.element('button', 'image-translated', '中文译图');
    for (const [node, choice] of [[original, 'original'], [translated, 'translated']]) {
      node.type = 'button'; node.onclick = () => { entry.choice = choice; this.render(entry); }; switches.append(node);
    }
    const status = this.element('span', 'image-translation-status'); status.setAttribute('aria-live', 'polite');
    const view = { controls, button, switches, original, translated, status, image, originalSrc };
    button.onclick = () => this.start(entry);
    controls.append(switches, button, status); host.append(controls); entry.views.push(view);
    controls.imageTranslationEntry = entry; this.observer.observe(controls); this.render(entry);
    return { load: () => this.load(entry), start: () => this.start(entry, false) };
  }
  async load(entry) {
    if (entry.loading) return;
    entry.loading = true; const revision = entry.revision || 0;
    try {
      const value = await this.api(entry.url);
      if (revision !== (entry.revision || 0)) return;
      entry.value = value; this.render(entry); this.poll(entry);
    } catch (error) {
      if (revision === (entry.revision || 0)) { entry.value = { ...entry.value, state: 'error', error: error.message }; this.render(entry); }
    } finally { entry.loading = false; }
  }
  async start(entry, force) {
    if (entry.posting || BUSY.includes(entry.value.state)) return;
    entry.posting = true; entry.revision = (entry.revision || 0) + 1;
    const retry = force ?? (entry.value.state === 'ready');
    entry.value = { ...entry.value, state: 'queued', error: '' }; this.render(entry);
    try { entry.value = await this.api(entry.url, { retry }); this.render(entry); this.poll(entry); }
    catch (error) { entry.value = { ...entry.value, state: 'error', error: error.message }; this.render(entry); }
    finally { entry.posting = false; }
  }
  poll(entry) {
    clearTimeout(entry.timer);
    if (this.live(entry).length) entry.timer = setTimeout(() => this.load(entry), BUSY.includes(entry.value.state) ? 1000 : 6000);
  }
  render(entry) {
    const { state, result } = entry.value, busy = BUSY.includes(state), translatedUrl = result?.imageUrl;
    const choice = entry.choice || (document.querySelector('#reader')?.dataset.columns === 'original' ? 'original' : 'translated');
    const labels = { idle: '整本预翻译会自动处理图片', queued: '等待处理', recognizing: '正在识别并翻译…', proofreading: '正在校对…', rendering: '正在生成中文译图…',
      draft: '初译已保存', needs_render: '等待生成译图', disabled: '当前服务仅翻译文字，图片保留原图', ready: result?.has_text === false ? '未发现可辨认文字' : '已校对并保存', error: entry.value.error || '图片处理失败' };
    for (const view of this.live(entry)) {
      view.button.disabled = busy || state === 'disabled'; view.button.hidden = state === 'disabled';
      view.button.textContent = busy ? '处理中…' : state === 'ready' ? '重新翻译' : state === 'error' ? '重试' : '翻译图片';
      view.status.textContent = labels[state] || ''; view.status.classList.toggle('error', state === 'error');
      view.status.title = (result?.warnings || []).join('\n'); view.switches.hidden = !translatedUrl;
      view.original.setAttribute('aria-pressed', String(choice === 'original')); view.translated.setAttribute('aria-pressed', String(choice === 'translated'));
      const src = choice === 'translated' && translatedUrl ? translatedUrl : view.originalSrc;
      if (view.image && src && view.image.getAttribute('src') !== src) view.image.src = src;
    }
  }
}
