/* Isolated real HTTP + SQLite; never starts a production book/model job. */
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const modules = process.env.PARALLEL_NODE_MODULES;
const { chromium } = require(modules ? path.join(modules, 'playwright') : 'playwright');

(async () => {
  const destination = path.resolve('test-results/book-reproof-browser'); fs.mkdirSync(destination, { recursive: true });
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
    await context.addInitScript(() => { localStorage.setItem('parallel.prefetch', 'false'); localStorage.setItem('parallel.flow', 'pages'); localStorage.setItem('parallel.theme', 'green'); });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const get = async endpoint => (await context.request.get(url + '/api/books/' + book + endpoint)).json();
    const current = async () => (await get('/reproofread')).jobs.find(job => job.scope === 'book');
    const waitJob = async (state, extra = () => true) => {
      for (let i = 0; i < 120; i++) {
        const job = await current();
        if (job?.state === state && extra(job)) return job;
        await page.waitForTimeout(100);
      }
      throw Error('Whole-book job did not reach ' + state);
    };
    const button = page.locator('#book-reproof-action');
    await page.goto(url); await page.locator('.paragraph-row .translation .sentence').first().waitFor();
    await page.locator('#book-translation > summary').click();
    await page.waitForFunction(() => !document.querySelector('#book-reproof-action').disabled);
    assert.equal(await page.locator('#book-translation-action').isDisabled(), true, 'Initial book translation is already completed');
    assert.match(await button.textContent(), /全书重新校对/);
    const pageBefore = (await get('')).currentPage;
    await page.screenshot({ path: path.join(destination, 'completed-book-button.png') });
    await button.click();
    await page.waitForFunction(() => document.querySelector('#book-reproof-action').dataset.action === 'pause' && !document.querySelector('#book-reproof-action').disabled);
    const first = await current(); assert.equal(first.total, 12); assert.ok(first.completed < 12);
    assert.equal(Number(await page.locator('#book-translation-progress').getAttribute('max')), 12);
    await button.click();
    const paused = await waitJob('paused', job => job.chapters.every(chapter => chapter.activeParagraph == null));
    assert.ok(paused.completed < paused.total);
    await page.reload(); await page.locator('.paragraph-row .translation .sentence').first().waitFor();
    await page.locator('#book-translation > summary').click();
    await page.waitForFunction(() => document.querySelector('#book-reproof-action').dataset.action === 'resume' && !document.querySelector('#book-reproof-action').disabled);
    assert.equal((await current()).id, first.id);
    assert.equal((await current()).completed, paused.completed);
    await button.click();
    const completed = await waitJob('completed'); assert.equal(completed.completed, 12); assert.equal(completed.completedChapters, 2);
    await page.waitForFunction(() => document.querySelector('#book-reproof-action').dataset.action === 'start' && !document.querySelector('#book-reproof-action').disabled);
    assert.equal((await get('')).currentPage, pageBefore, 'Reproof does not change saved reading position');
    assert.match(await page.locator('#book-reproof-count').textContent(), /12 \/ 12/);
    await page.screenshot({ path: path.join(destination, 'first-pass-completed.png') });
    await button.click();
    await page.waitForFunction(() => document.querySelector('#book-reproof-action').dataset.action === 'pause' && !document.querySelector('#book-reproof-action').disabled);
    const second = await current(); assert.notEqual(second.id, first.id); assert.equal(second.round, first.round + 1); assert.ok(second.completed < 12);
    await button.click(); await waitJob('paused', job => job.chapters.every(chapter => chapter.activeParagraph == null));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(250);
    await button.scrollIntoViewIfNeeded();
    const rect = await button.boundingBox(); assert.ok(rect.x >= 0 && rect.x + rect.width <= 391);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No mobile horizontal overflow');
    await page.screenshot({ path: path.join(destination, 'paused-mobile.png') });
    assert.equal(errors.length, 0, errors.join('\n'));
    fs.writeFileSync(path.join(destination, 'report.json'), JSON.stringify({ result: 'passed', first, completed, second, errors }, null, 2));
    process.stdout.write('Whole-book reproof browser checks passed: completed book, pause/resume, reload, new pass, mobile, reading position.\n');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
