/* Reading a saved version does not change the service used for missing text. */
export class TranslationSources {
  constructor({ state, api }) {
    this.state = state; this.api = api;
    this.button = document.querySelector('#translation-source-button');
    this.dialog = document.querySelector('#translation-source-dialog');
    this.select = document.querySelector('#translation-source-select');
    this.dialog.addEventListener('cancel', event => event.preventDefault());
  }
  label(source) {
    const provider = { llm: 'LLM', azure: 'Azure Translate', microsoft: 'Azure Translate' }[source.provider] || '本地译文';
    return `${provider}${source.provider === 'llm' && source.model ? ' · ' + source.model : ''} · ${source.completed}/${source.total} 段${source.targetLanguage ? ' · ' + source.targetLanguage : ' · 语言未记录'}`;
  }
  update(value) {
    if (!value?.selectedProfile) return;
    this.value = value; this.state.readingProfile = value.selectedProfile;
    const source = value.sources?.find(item => item.profile === value.selectedProfile);
    this.button.hidden = !this.state.book;
    this.button.textContent = source ? '本地译文：' + ({llm: 'LLM', azure: 'Azure Translate', microsoft: 'Azure Translate'}[source.provider] || '已保存') + ` · ${source.completed}/${source.total} 段` : '译文：当前服务';
    this.button.title = source ? this.label(source) + ' · 点击切换阅读译文；缺失部分使用当前翻译服务' : '当前配置尚无已保存译文；点击查看本地译文';
  }
  async resolve(book, { force = false, epoch = this.state.epoch, preferred = null } = {}) {
    this.state.readingChoicePending = true;
    const value = await this.api(`/api/books/${book.id}/translation-sources`);
    if (epoch !== this.state.epoch || book.id !== this.state.book?.id) return false;
    if (!value || typeof value.selectedProfile !== 'string' || typeof value.configurationProfile !== 'string' || !Array.isArray(value.sources)) {
      throw new Error('无法读取本地译文清单，请点击“本地译文”重试；尚未开始新的翻译。');
    }
    if ((value.selectionRequired || force) && value.sources.length) {
      this.select.replaceChildren();
      for (const source of value.sources) {
        const option = document.createElement('option'); option.value = source.profile; option.textContent = this.label(source); this.select.append(option);
      }
      const candidate = preferred || value.selectedProfile;
      if (value.sources.some(source => source.profile === candidate)) this.select.value = candidate;
      document.querySelector('#translation-source-description').textContent = value.selectionRequired
        ? '当前翻译配置还没有这本书的译文。本地已有以下译文，是否继续使用？已保存段落不会重新翻译，缺失部分由当前服务补齐。'
        : '选择阅读哪份已保存译文。翻译服务设置保持不变，缺失部分由当前服务补齐。';
      document.querySelector('#translate-with-current').textContent = value.sources.some(source => source.current) ? '切换到当前服务译文' : '用当前服务重新翻译';
      const chosen = await new Promise(resolve => {
        document.querySelector('#use-saved-translation').onclick = () => resolve(this.select.value);
        document.querySelector('#translate-with-current').onclick = () => resolve(value.configurationProfile);
        this.dialog.showModal();
      });
      this.dialog.close();
      if (epoch !== this.state.epoch || book.id !== this.state.book?.id) return false;
      const saved = await this.api(`/api/books/${book.id}/translation-sources`, { sourceProfile: chosen, configurationProfile: value.configurationProfile });
      if (epoch !== this.state.epoch) return false;
      Object.assign(value, saved);
    }
    this.update(value); this.state.readingChoicePending = false;
    return true;
  }
}
