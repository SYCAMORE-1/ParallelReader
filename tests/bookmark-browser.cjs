const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const modules = process.env.PARALLEL_NODE_MODULES;
const { chromium } = require(modules ? path.join(modules, 'playwright') : 'playwright');

(async () => {
  const destination = path.resolve('test-results/bookmark-browser'); fs.mkdirSync(destination, { recursive: true });
  const data = fs.mkdtempSync(path.join(destination, 'data-'));
  const server = spawn('python', ['-X', 'utf8', 'tests/bookmark-fixture.py', '--data', data], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const errors = []; server.stderr.on('data', data => errors.push(data.toString()));
  let browser;
  try {
    const { url } = await new Promise((resolve, reject) => {
      let value = ''; const timer = setTimeout(() => reject(Error('Fixture timeout')), 10000);
      server.stdout.on('data', data => { value += data; if (value.includes('\n')) { clearTimeout(timer); resolve(JSON.parse(value.split('\n')[0])); } });
      server.once('error', reject);
    });
    browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
    await context.addInitScript(() => { localStorage.setItem('parallel.prefetch', 'false'); localStorage.setItem('parallel.lastBook', 'bookmark-a'); localStorage.setItem('parallel.theme', 'green'); });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const get = async (id = 'a') => (await context.request.get(url + '/api/books/bookmark-' + id)).json();
    const go = async index => { await page.locator('#page-input').fill(String(index + 1)); await page.locator('#page-input').press('Enter'); await page.waitForTimeout(120); };
    const pin = async index => {
      await go(index); await page.locator('#bookmark-button').click();
      await page.waitForFunction(() => !document.querySelector('#bookmark-button').disabled);
      assert.equal((await get()).bookmark.page, index);
    };
    const jump = async (chapter, index) => {
      await page.locator(`.toc-item[data-chapter="${chapter}"]`).click();
      await page.waitForFunction(value => Number(document.querySelector('#page-input').value) === value, index + 1);
      assert.ok(await page.locator(`.paragraph-row[data-page="${index}"]`).count());
    };
    await page.goto(url); await page.locator('.toc-bookmark').waitFor();
    assert.equal(await page.locator('.toc-bookmark').count(), 1);
    assert.equal((await get()).bookmark.manual, false);
    await go(0); await jump(0, 0);
    await pin(7); // Chapter 2's THIRD page, not its start at page 5.
    assert.equal(await page.locator('.toc-item[data-chapter="1"] .toc-bookmark').count(), 1);
    assert.equal(await page.locator('.toc-bookmark').count(), 1);
    await go(0); await jump(1, 7);
    await pin(2);
    assert.equal(await page.locator('.toc-item[data-chapter="1"] .toc-bookmark').count(), 0);
    assert.equal(await page.locator('.toc-item[data-chapter="0"] .toc-bookmark').count(), 1);
    await go(8); await page.reload(); await page.locator('.toc-bookmark').waitFor();
    assert.equal((await get()).bookmark.page, 2);
    assert.equal(Number(await page.locator('#page-input').inputValue()), 9, 'Automatic reading position remains independent of the pinned bookmark');
    await page.locator('#tab-library').click(); await page.getByRole('button', { name: '阅读 Bookmark B', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#book-title').textContent === 'Bookmark B');
    await go(4); await page.locator('#bookmark-button').click();
    await page.waitForFunction(() => !document.querySelector('#bookmark-button').disabled);
    assert.equal((await get('b')).bookmark.page, 4);
    assert.equal((await get('a')).bookmark.page, 2);
    await page.locator('#tab-library').click(); await page.getByRole('button', { name: '阅读 Bookmark A', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#book-title').textContent === 'Bookmark A');
    await page.waitForFunction(() => document.querySelector('#page-input').value === '3');
    assert.equal(Number(await page.locator('#page-input').inputValue()), 3, 'Shelf clicks open the bookmark rather than the later reading position');
    for (const flow of ['pages', 'scroll']) {
      await page.locator('#flow-' + flow).click();
      for (const mode of ['original', 'translation', 'both', 'follow']) {
        await page.locator('#show-' + mode).click(); await pin(7);
        await go(0);
        await page.locator('#tab-library').click(); await page.getByRole('button', { name: '阅读 Bookmark A', exact: true }).click();
        await page.waitForFunction(() => document.querySelector('#page-input').value === '8');
        assert.ok(await page.locator('.paragraph-row[data-page="7"]').count());
        assert.equal((await get()).bookmark.page, 7);
      }
    }
    await page.screenshot({ path: path.join(destination, 'bookmark-desktop.png') });
    await page.locator('#immersive-button').click();
    assert.equal(await page.locator('#bookmark-button').isVisible(), true);
    await go(8); await page.locator('#bookmark-button').click();
    await page.waitForFunction(() => !document.querySelector('#bookmark-button').disabled);
    assert.equal((await get()).bookmark.page, 8);
    await page.locator('#exit-immersive').click();
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(await page.locator('#bookmark-button').isVisible(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(destination, 'bookmark-mobile-' + width + '.png') });
    }
    await page.locator('#sidebar-toggle').click();
    await page.screenshot({ path: path.join(destination, 'bookmark-toc-mobile.png') });
    assert.equal(await page.locator('.toc-bookmark').count(), 1);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, checks: ['auto-reading-position', 'one-bookmark', 'manual-pin', 'replace-old-bookmark', 'chapter-third-page-jump', 'reload', 'per-book-independence', 'shelf-opens-bookmark', 'all-four-modes', 'pages-and-scroll', 'immersive', 'mobile-390-and-320'], screenshots: destination }));
  } finally { await browser?.close(); server.kill(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
