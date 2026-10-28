/* Real isolated server + SQLite: no production data or model calls. */
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const modules = process.env.PARALLEL_NODE_MODULES;
const { chromium } = require(modules ? path.join(modules, 'playwright') : 'playwright');

(async () => {
  const destination = path.resolve('test-results/reproof-browser'); fs.mkdirSync(destination, { recursive: true });
  const data = fs.mkdtempSync(path.join(destination, 'data-'));
  const server = spawn('python', ['-X', 'utf8', 'tests/reproof-fixture.py', '--data', data], { cwd: path.resolve('.'), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const errors = []; server.stderr.on('data', chunk => errors.push(chunk.toString()));
  let browser;
  try {
    const { url, book } = await new Promise((resolve, reject) => {
      let text = ''; const timer = setTimeout(() => reject(Error('Fixture startup timed out')), 10000);
      server.stdout.on('data', chunk => { text += chunk; if (text.includes('\n')) { clearTimeout(timer); resolve(JSON.parse(text.split('\n')[0])); } });
      server.once('error', reject);
    });
    browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
    await context.addInitScript(() => { localStorage.setItem('parallel.prefetch', 'false'); localStorage.setItem('parallel.flow', 'scroll'); localStorage.setItem('parallel.theme', 'green'); });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const api = '/api/books/' + book;
    const get = async path => (await context.request.get(url + api + path)).json();
    const jobs = async () => (await get('/reproofread')).jobs;
    const waitJob = async (scope, state = 'completed') => {
      for (let i = 0; i < 100; i++) {
        const job = (await jobs()).find(job => job.scope === scope);
        if (job?.state === state) return job;
        await page.waitForTimeout(150);
      }
      throw Error('Job did not reach ' + state);
    };
    const row = () => page.locator('.paragraph-row[data-pid="2"]');
    const source = index => row().locator('.original .sentence').nth(index);
    await page.goto(url); await source(1).waitFor();
    const before = (await get('/status?page=1')).items['2'];
    await source(1).click(); await source(1).click({ button: 'right' });
    await page.locator('#reproof-menu').waitFor();
    const position = await page.evaluate(() => scrollY), progress = (await get('')).currentPage;
    await page.screenshot({ path: path.join(destination, 'sentence-menu.png') });
    await page.locator('#reproof-sentence').click();
    assert.equal(await row().locator('.translation').textContent(), before.text, 'Old text stays visible while review runs');
    await waitJob('sentence');
    await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="2"] .translation')?.textContent.includes('原地新译2-1'));
    const after = (await get('/status?page=1')).items['2'];
    assert.equal(after.targetSentences[0], before.targetSentences[0]);
    assert.equal(after.targetSentences[2], before.targetSentences[2]);
    assert.equal(after.targetSentences[1], '原地新译2-1。');
    assert.equal((await get('')).currentPage, progress);
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - position) <= 2, 'In-place review preserves reading position');
    assert.equal(await row().locator('.original .sentence.sentence-selected').count(), 1);
    await page.reload(); await source(1).waitFor();
    await page.waitForFunction(() => document.querySelector('.paragraph-row[data-pid="2"] .translation')?.textContent.includes('原地新译2-1'));
    assert.match(await row().locator('.translation').textContent(), /原地新译2-1/);
    await source(0).scrollIntoViewIfNeeded();
    await row().evaluate(node => {
      const parts = node.querySelectorAll('.original .sentence');
      const range = document.createRange(); range.setStart(parts[0].firstChild, 3); range.setEnd(parts[1].firstChild, 10);
      getSelection().removeAllRanges(); getSelection().addRange(range);
      const rect = parts[0].getBoundingClientRect(); parts[0].dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: rect.left + 15, clientY: rect.top + 10 }));
    });
    assert.match(await page.locator('#reproof-sentence').textContent(), /2 句/);
    await page.locator('#reproof-sentence').click();
    await waitJob('sentence');
    const multi = (await jobs()).find(job => job.scope === 'sentence');
    assert.equal(multi.selection.start, 0);
    assert.equal(multi.selection.end, Array.from(before.sourceSentences[0] + before.sourceSentences[1]).length);
    assert.equal((await get('/status?page=1')).items['2'].targetSentences[2], before.targetSentences[2]);
    await page.evaluate(() => getSelection().removeAllRanges());
    const oldPage = (await get('')).currentPage;
    await page.locator('.toc-item[data-chapter="1"]').click({ button: 'right' });
    assert.equal(await page.locator('#reproof-sentence').isVisible(), false);
    await page.locator('#reproof-chapter').click();
    await page.locator('.toc-item[data-chapter="1"] .reproof-chapter-status').waitFor();
    await page.reload(); await source(1).waitFor();
    const chapter = await waitJob('chapter'); assert.equal(chapter.completed, 6);
    assert.equal((await get('')).currentPage, oldPage, 'Reviewing another chapter does not navigate');
    const revisedChapter = await get('/status?page=3');
    for (const pid of [6, 7, 8, 9, 10, 11]) if (revisedChapter.items[pid]) assert.match(revisedChapter.items[pid].text, new RegExp('原地新译' + pid));
    const old = (await get('/status?page=1')).items['2'].text;
    fs.writeFileSync(path.join(data, 'fail-review'), '1');
    await source(2).click({ button: 'right' }); await page.locator('#reproof-sentence').click();
    await waitJob('sentence', 'failed');
    assert.equal((await get('/status?page=1')).items['2'].text, old, 'Failure retains all existing text');
    fs.unlinkSync(path.join(data, 'fail-review'));
    await source(2).click({ button: 'right' }); await page.locator('#reproof-sentence').click();
    await waitJob('sentence');
    assert.equal((await get('/status?page=1')).items['2'].targetSentences[2], '原地新译2-2。');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#sidebar-toggle').click();
    await page.locator('.toc-item[data-chapter="1"]').click({ button: 'right' });
    const menu = await page.locator('#reproof-menu').boundingBox();
    assert.ok(menu.x >= 0 && menu.y >= 0 && menu.x + menu.width <= 390 && menu.y + menu.height <= 844);
    await page.screenshot({ path: path.join(destination, 'chapter-menu-mobile.png') });
    await page.keyboard.press('Escape'); assert.equal(await page.locator('#reproof-menu').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, checks: ['sentence-context-menu', 'selected-only-update', 'old-text-during-review', 'reading-position', 'highlight-preserved', 'reload-persistence', 'multi-sentence-selection', 'unicode-offsets', 'chapter-context-menu', 'chapter-progress', 'background-after-reload', 'failure-keeps-text', 'retry', 'mobile-menu-bounds', 'escape'], screenshots: destination }));
  } finally { await browser?.close(); server.kill(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
