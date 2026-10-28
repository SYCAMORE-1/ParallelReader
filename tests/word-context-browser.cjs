/* Regression for the user's 'her' example, including exact clicked position. */
const { chromium } = require(require('node:path').join(process.env.PARALLEL_NODE_MODULES, 'playwright'));
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().endsWith('/word')) requests.push(request.postDataJSON()); });
  try {
    await page.goto('http://127.0.0.1:8765', { waitUntil: 'networkidle' });
    await page.locator('#reader').waitFor();
    const originalPage = Number(await page.locator('#page-input').inputValue());
    const library = await (await page.request.get('http://127.0.0.1:8765/api/books')).json();
    const title = await page.locator('#book-title').textContent();
    const book = await (await page.request.get('http://127.0.0.1:8765/api/books/' + library.find(book => book.title === title).id)).json();
    const paragraph = book.paragraphs.find(paragraph => paragraph.text.includes('Don’t you recognize her?'));
    if (!paragraph) throw Error('Sample sentence missing');
    const samplePage = book.pages.find(page => page.paragraphs.includes(paragraph.id)).id + 1;
    await page.locator('#page-input').fill(String(samplePage)); await page.locator('#page-input').press('Enter');
    const sentence = page.locator(`.paragraph-row[data-pid="${paragraph.id}"] .original .sentence`).filter({ hasText: 'her' }).first();
    async function openWord() {
      await sentence.scrollIntoViewIfNeeded();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const point = await sentence.evaluate(span => {
        const text = span.firstChild.textContent, start = text.indexOf('her');
        const range = document.createRange(); range.setStart(span.firstChild, start); range.setEnd(span.firstChild, start + 3);
        const box = range.getClientRects()[0]; return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      });
      await page.mouse.dblclick(point.x, point.y);
      await page.locator('#word-popover').waitFor();
      await page.waitForFunction(() => !document.querySelector('#word-definitions').hidden || document.querySelector('#word-popover').classList.contains('word-error'), null, { timeout: 60000 });
      if (await page.locator('#word-definitions').isHidden()) throw Error(await page.locator('#word-status').textContent());
    }
    await page.locator('#immersive-button').click();
    await openWord();
    const basic = await page.locator('#word-basic').textContent(), contextual = await page.locator('#word-meaning').textContent();
    if (!basic.includes('她') || !contextual.includes('车')) throw Error('her must have its basic meaning and a contextual vehicle referent');
    if (requests.at(-1).offset !== paragraph.text.indexOf('her')) throw Error('Wrong selected word offset');
    if (!await page.locator('#word-context').textContent().then(text => text.includes('前 10 句') && text.includes('不读取后文'))) throw Error('Expected past-only context');
    if ((await page.locator('#word-pos').textContent()) !== 'pron.') throw Error('Expected an English part-of-speech abbreviation');
    fs.mkdirSync('test-results', { recursive: true });
    await page.screenshot({ path: 'test-results/word-context-her.png' });
    await page.locator('#close-word').click(); await page.locator('#exit-immersive').click();
    await page.waitForFunction(() => !document.fullscreenElement && !document.body.classList.contains('immersive'));
    await page.setViewportSize({ width: 390, height: 844 });
    await openWord();
    const bounds = await page.locator('#word-popover').boundingBox();
    if (bounds.x < 0 || bounds.y < 0 || bounds.x + bounds.width > 391 || bounds.y + bounds.height > 845) throw Error('Mobile word card exceeds viewport');
    await page.screenshot({ path: 'test-results/word-context-mobile.png' });
    await page.locator('#close-word').click();
    await page.locator('#page-input').fill(String(originalPage)); await page.locator('#page-input').press('Enter');
    if (errors.length) throw Error(errors.join('; '));
    console.log(JSON.stringify({ ok: true, basicMeaning: basic, contextMeaning: contextual, exactOccurrence: true, contextSentences: [10, 0], immersive: true, mobile: true, consoleErrors: errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
