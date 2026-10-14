import { chromium, webkit } from 'playwright';
import fs from 'node:fs';

const PORT = process.env.PORT || '8411';
const BASE = `http://localhost:${PORT}/score_viewer.html`;
const OUT = process.argv[3] || '/tmp/pwtest/shots';
fs.mkdirSync(OUT, { recursive: true });

const which = process.argv[2] === 'webkit' ? webkit : chromium;
const label = process.argv[2] || 'chromium';

const browser = await which.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));

function report(step, ok, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'} [${label}] ${step}${detail ? ' — ' + detail : ''}`);
  if (!ok) process.exitCode = 1;
}

await page.goto(BASE, { waitUntil: 'networkidle' });

// AC9: empty state says what to drop, no error
const emptyVisible = await page.locator('#empty').isVisible();
const emptyText = await page.locator('#empty').innerText();
report('empty state visible', emptyVisible && /drop/i.test(emptyText));
report('no console errors on load', errors.length === 0, errors.join(' | '));
await page.screenshot({ path: `${OUT}/${label}-01-empty.png`, fullPage: false });

// AC5: sample menu filled from the directory index, one click opens
const options = await page.locator('#samples option').allTextContents();
report('samples listed', options.length >= 5, options.join(', '));
const wanted = options.find(t => /greensleeves · countermelody/.test(t));
report('greensleeves countermelody offered', !!wanted);

await page.selectOption('#samples', { label: wanted });
await page.waitForFunction(() =>
  document.querySelectorAll('#decks .deck.on svg').length === 2, null, { timeout: 30000 });

// AC1 + AC6: two scores render side by side, arrangement has both voices
const svgCount = await page.locator('#decks .deck.on svg').count();
report('two scores rendered', svgCount === 2, `svg=${svgCount}`);
const metaB = await page.locator('#deck-b .meta').innerText();
report('arrangement shows 2 parts', /2 parts/.test(metaB), metaB);
const boxA = await page.locator('#deck-a').boundingBox();
const boxB = await page.locator('#deck-b').boundingBox();
report('side by side', boxB.x > boxA.x + boxA.width - 5, `a.x=${boxA.x} b.x=${boxB.x}`);

// AC8: phrase boundaries from the report
const phrases = await page.locator('#deck-b .phrase').count();
report('phrase markers drawn', phrases >= 2, `count=${phrases}`);
const diag = await page.locator('#diag').innerText();
report('diagnostics shown', /structural melody keeps/.test(diag), diag.split('\n')[1] || diag);

await page.screenshot({ path: `${OUT}/${label}-02-sample.png`, fullPage: false });

// AC2 + AC3: play, cursor tracks, audio graph actually gets notes
await page.click('#play');
await page.waitForTimeout(1200);
const state = await page.evaluate(() => ({
  playing: window.__probe ? null : document.getElementById('play').textContent,
  clock: document.getElementById('clock').textContent,
  bar: document.getElementById('bar').textContent,
  headA: getComputedStyle(document.querySelector('#deck-a .head')).display,
  headB: getComputedStyle(document.querySelector('#deck-b .head')).display,
  leftA: document.querySelector('#deck-a .head').style.left,
  leftB: document.querySelector('#deck-b .head').style.left,
}));
report('play toggles to Pause', state.playing === 'Pause', state.playing);
report('clock advances', !/^0:00 \//.test(state.clock), state.clock);
report('playhead visible on both decks',
  state.headA === 'block' && state.headB === 'block', JSON.stringify(state));
report('measure readout', /measure \d+/.test(state.bar), state.bar);

await page.waitForTimeout(1500);
const later = await page.evaluate(() => ({
  leftA: document.querySelector('#deck-a .head').style.left,
  leftB: document.querySelector('#deck-b .head').style.left,
  bar: document.getElementById('bar').textContent,
}));
report('playhead moved', later.leftA !== state.leftA, `${state.leftA} → ${later.leftA}`);
const step = await page.evaluate(() => {
  const marked = key => {
    const mark = window.miareViewer.decks[key].mark;
    return { left: mark.style.left, width: mark.style.width, shown: mark.style.display };
  };
  return { position: window.miareViewer.position(), a: marked('a'), b: marked('b'),
           headA: window.miareViewer.headLeft('a'), headB: window.miareViewer.headLeft('b') };
});
report('both decks mark a measure', step.a.shown === 'block' && step.b.shown === 'block',
  JSON.stringify(step));
// Same measure index on both decks: the mark box each deck shows is the same ordinal.
const sameBar = await page.evaluate(() => {
  const at = window.miareViewer.position();
  const left = key => {
    const deck = window.miareViewer.decks[key];
    const box = deck.geo[at.i];
    return box ? Math.round(box.x) : null;
  };
  const markLeft = key => Math.round(parseFloat(window.miareViewer.decks[key].mark.style.left));
  return { i: at.i, f: at.f, geoA: left('a'), geoB: left('b'),
           markA: markLeft('a'), markB: markLeft('b') };
});
report('each deck marks the shared measure index',
  sameBar.geoA === sameBar.markA && sameBar.geoB === sameBar.markB, JSON.stringify(sameBar));
await page.screenshot({ path: `${OUT}/${label}-03-playing.png`, fullPage: false });

// notes really were scheduled: count oscillators created
const scheduled = await page.evaluate(() => window.miareViewer.notesScheduled());
report('notes scheduled to the audio graph', scheduled > 0, `count=${scheduled}`);

// AC7: audio source switch
await page.selectOption('#audio', 'a');
await page.waitForTimeout(400);
const mix = await page.evaluate(() => ({
  value: document.getElementById('audio').value,
  audible: window.miareViewer.audible(),
}));
report('audio switched to left only',
  mix.value === 'a' && mix.audible.length === 1 && mix.audible[0] === 'a', JSON.stringify(mix));
await page.selectOption('#audio', 'b');
await page.waitForTimeout(300);
const mixB = await page.evaluate(() => window.miareViewer.audible());
report('audio switched to right only', mixB.length === 1 && mixB[0] === 'b', mixB.join(','));
await page.selectOption('#audio', 'both');
await page.waitForTimeout(300);
const mixBoth = await page.evaluate(() => window.miareViewer.audible());
report('audio back to both', mixBoth.length === 2, mixBoth.join(','));

await page.click('#play');                        // pause
await page.click('#stop');
await page.waitForTimeout(200);
const stopped = await page.evaluate(() => ({
  clock: document.getElementById('clock').textContent,
  seek: document.getElementById('seek').value,
  label: document.getElementById('play').textContent,
}));
report('stop resets', /^0:00/.test(stopped.clock) && stopped.seek === '0'
  && stopped.label === 'Play', JSON.stringify(stopped));

// AC10: drag-and-drop of a file outside samples/
const xml = fs.readFileSync(process.env.REPO + '/tests/fixtures/minor_melody.musicxml', 'utf8');
await page.evaluate(async text => {
  const file = new File([text], 'dropped.musicxml', { type: 'application/xml' });
  const dt = new DataTransfer();
  dt.items.add(file);
  document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}, xml);
await page.waitForTimeout(2500);
const names = await page.locator('#decks .deck.on .name').allTextContents();
report('dropped file replaced the right deck', names.includes('dropped.musicxml'), names.join(', '));
await page.screenshot({ path: `${OUT}/${label}-04-dropped.png`, fullPage: false });

// 390px mobile view renders stacked
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(900);
await page.screenshot({ path: `${OUT}/${label}-05-mobile.png`, fullPage: false });

report('no errors overall', errors.length === 0, errors.join(' | '));
await browser.close();
