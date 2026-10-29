const $ = selector => document.querySelector(selector);
export class ArchiveTools {
  constructor({ api, refresh, report }) {
    Object.assign(this, { api, refresh, report });
    this.dialog = $('#archive-dialog'); this.busy = false; this.job = null;
    $('#library-archive').onclick = () => this.open('export');
    $('#archive-close').onclick = () => this.dialog.close();
    $('#archive-form').onsubmit = event => { event.preventDefault(); this.submit(); };
    this.dialog.addEventListener('close', () => {
      $('#archive-password').value = ''; $('#archive-confirm').value = '';
      if (!this.busy) this.files = null;
    });
    $('#archive-download-all').onclick = async () => {
      for (const anchor of $('#archive-downloads').querySelectorAll('a')) {
        anchor.click(); await new Promise(resolve => setTimeout(resolve, 500));
      }
      $('#archive-status').textContent = '若浏览器未允许多个下载，可逐个点击上方分卷。恢复时请选择全部分卷。';
    };
  }
  open(kind, files = null) {
    if (this.busy) { this.dialog.showModal(); return; }
    this.kind = kind; this.files = files; this.job = null;
    $('#archive-title').textContent = kind === 'export' ? '加密打包书架' : '恢复并合并书架';
    $('#archive-description').textContent = kind === 'export'
      ? '打包当前整个书架，每卷最多 500 MiB。请自行设置密码并妥善保存；恢复需要同一密码。'
      : `已选择 ${files.length} 个文件。请输入归档密码，校验完成后合并到当前书架。只有原书和已保存内容完全相同的书才会跳过。`;
    $('#archive-confirm-label').hidden = kind !== 'export'; $('#archive-confirm').required = kind === 'export';
    $('#archive-password-fields').hidden = false; $('#archive-password').value = ''; $('#archive-confirm').value = '';
    $('#archive-password').required = true;
    $('#archive-status').textContent = ''; $('#archive-progress').hidden = true;
    $('#archive-downloads').replaceChildren(); $('#archive-download-all').hidden = true;
    $('#archive-submit').hidden = false; $('#archive-submit').disabled = false;
    $('#archive-submit').textContent = kind === 'export' ? '开始加密打包' : '解压并合并书架';
    this.dialog.showModal(); $('#archive-password').focus();
  }
  async submit() {
    if (this.busy) return;
    const password = $('#archive-password').value, confirmation = $('#archive-confirm').value;
    if (!password.trim()) { $('#archive-status').textContent = '请输入你自己的密码。'; return; }
    if (this.kind === 'export' && password !== confirmation) { $('#archive-status').textContent = '两次输入的密码不一致。'; return; }
    this.busy = true;
    $('#archive-submit').disabled = true; $('#archive-password-fields').hidden = true;
    $('#archive-password').value = ''; $('#archive-confirm').value = '';
    $('#archive-progress').hidden = false; $('#archive-progress').removeAttribute('value');
    let uploadId = null;
    try {
      if (this.kind === 'export') this.job = await this.api('/api/archive/export', { password, confirmation });
      else {
        this.job = await this.api('/api/archive/import/start', {}); uploadId = this.job.id;
        for (const [index, file] of this.files.entries()) {
          $('#archive-status').textContent = `正在上传 ${index + 1} / ${this.files.length}：${file.name}`;
          const response = await fetch(`/api/archive/uploads/${uploadId}?name=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
          const result = await response.json(); if (!response.ok) throw new Error(result.error || '上传失败');
        }
        this.job = await this.api('/api/archive/import/finish', { id: uploadId, password });
        uploadId = null;
      }
      $('#archive-submit').hidden = true;
      while (this.job.state === 'working') {
        $('#archive-status').textContent = this.job.message || '处理中…';
        if (this.job.total) { $('#archive-progress').max = this.job.total; $('#archive-progress').value = this.job.completed; }
        await new Promise(resolve => setTimeout(resolve, 1000));
        this.job = await this.api('/api/archive/jobs/' + this.job.id);
      }
      if (this.job.state === 'failed') throw new Error(this.job.error || '归档操作失败');
      $('#archive-progress').hidden = true;
      if (this.kind === 'export') {
        $('#archive-status').textContent = `${this.job.books} 本书已打包，共 ${this.job.parts.length} 卷。请下载并保留全部分卷。`;
        for (const part of this.job.parts) {
          const anchor = document.createElement('a'); anchor.href = part.url; anchor.download = part.file;
          anchor.textContent = `${part.file} · ${(part.bytes / 1024 / 1024).toFixed(1)} MiB`;
          $('#archive-downloads').append(anchor);
        }
        $('#archive-download-all').hidden = false;
      } else {
        $('#archive-status').textContent = `已合并 ${this.job.restoredBooks} 本书，跳过 ${this.job.skippedBooks} 本完全相同的书。当前完整书架可再次打包。`;
        await this.refresh();
      }
    } catch (error) {
      if (uploadId) await this.api('/api/archive/import/cancel', { id: uploadId }).catch(() => {});
      $('#archive-status').textContent = error.message; $('#archive-progress').hidden = true;
      $('#archive-password-fields').hidden = false; $('#archive-submit').hidden = false;
    } finally {
      this.busy = false; $('#archive-submit').disabled = false;
    }
  }
}
