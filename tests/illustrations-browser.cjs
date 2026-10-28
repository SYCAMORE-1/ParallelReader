const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(path.join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
(async () => {
  const destination = path.resolve('test-results/illustrations'); fs.mkdirSync(destination, { recursive: true });
  const books = process.argv.slice(2);
  if (!books.length) throw new Error('Pass paths to your own illustrated EPUB files');
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  try {
    for (let index = 0; index < books.length; index++) {
      const directory = fs.mkdtempSync(path.join(destination, 'fixture-'));
      const server = spawn('python', ['-X', 'utf8', 'tests/illustrations-fixture.py', '--data', directory, '--book', path.resolve(books[index])], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        const info = await new Promise((resolve, reject) => {
          let output = '', diagnostics = '';
          server.stdout.on('data', chunk => { output += chunk; if (output.includes('\n')) resolve(JSON.parse(output.split('\n')[0])); });
          server.stderr.on('data', chunk => { diagnostics += chunk; });
          server.on('exit', code => reject(new Error('Fixture exited: ' + code + ' ' + diagnostics)));
          server.on('error', reject);
        });
        await page.goto(info.url, { waitUntil: 'networkidle' });
        const book = await (await page.request.get(info.url + '/api/books/' + info.book)).json();
        assert.ok(book.cover); assert.ok(book.illustrations.length >= 100);
        await page.locator('#tab-library').click();
        await page.waitForFunction(() => { const image = document.querySelector('.book-cover'); return image?.complete && image.naturalWidth > 0; });
        await page.locator('#tab-toc').click();
        await page.locator('.illustration-image').first().evaluate(image => image.scrollIntoView({ block: 'start' }));
        await page.waitForFunction(() => { const image = document.querySelector('.illustration-image'); return image?.complete && image.naturalWidth > 0; });
        for (const mode of ['original', 'translation', 'both', 'follow']) {
          await page.locator('#show-' + mode).click();
          assert.ok(await page.locator('.illustration-image').first().isVisible(), 'Images remain visible in every reading mode');
        }
        await page.locator('.illustration-open').first().click();
        await page.locator('.illustration-viewer[open]').waitFor();
        await page.waitForFunction(() => document.querySelector('.illustration-viewer img').naturalWidth > 0);
        const before = await page.locator('.illustration-viewer img').evaluate(image => image.getBoundingClientRect().width);
        await page.locator('.illustration-viewer').getByRole('button', { name: '放大插图', exact: true }).click();
        assert.ok(await page.locator('.illustration-viewer img').evaluate(image => image.getBoundingClientRect().width) > before);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.illustration-viewer').isVisible(), false);
        if (book.chapters.filter(chapter => /^\d{3}[.)]/.test(chapter.title)).length === 100) {
          const skills = book.chapters.filter(chapter => /^\d{3}(?:[.)]|\s)/.test(chapter.title));
          assert.equal(skills.length, 100);
          await page.locator('.toc-item').filter({ hasText: '100.' }).click();
          assert.equal(await page.locator('#page-input').inputValue(), String(skills.at(-1).startPage + 1));
          assert.match(await page.locator('.paragraph-row.heading .original').first().textContent(), /^100\./);
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const headingTop = await page.locator('.paragraph-row.heading').first().evaluate(row => row.getBoundingClientRect().top);
          const barBottom = await page.locator('.column-labels').evaluate(bar => bar.getBoundingClientRect().bottom);
          assert.ok(headingTop >= barBottom - 1 && headingTop < 500, 'A directory click brings the skill heading into view without the previous skill image');
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator('#page-input').evaluate(input => { input.value = '1'; input.dispatchEvent(new Event('change')); });
        for (const flow of ['pages', 'scroll']) {
          await page.locator('#flow-' + flow).click();
          await page.locator('.illustration-image').first().evaluate(image => image.scrollIntoView({ block: 'start' }));
          await page.waitForFunction(() => { const image = document.querySelector('.illustration-image'); return image?.complete && image.naturalWidth > 0; });
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
          await page.screenshot({ path: path.join(destination, `book-${index}-${flow}-390.png`) });
        }
        await page.locator('#immersive-button').click();
        await page.locator('.illustration-open').first().click();
        await page.locator('.illustration-viewer[open]').waitFor();
        await page.screenshot({ path: path.join(destination, `book-${index}-zoom-390.png`) });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('body').evaluate(body => body.classList.contains('immersive')), true);
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ book: books[index], images: book.illustrations.length, chapters: book.chapters.length, cover: true, fourModes: true, mobile: true, scroll: true, zoom: true }));
      } finally { await context.close(); server.kill(); }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
