import { chromium, webkit } from 'playwright';
import fs from 'node:fs';

const PORT = process.env.PORT || '8411';
const BASE = `http://localhost:${PORT}/score_viewer.html`;
const REPO = process.env.REPO;
const OUT = '/tmp/pwtest/shots';
const which = process.argv[2] === 'webkit' ? webkit : chromium;
const label = process.argv[2] || 'chromium';

const browser = await which.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
const report = (step, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'} [${label}] ${step}${detail ? ' — ' + detail : ''}`);
  if (!ok) process.exitCode = 1;
};

await page.goto(BASE, { waitUntil: 'networkidle' });

// controls are off until something is open
const before = await page.evaluate(() => ({
  play: document.getElementById('play').disabled,
  audio: document.getElementById('audio').disabled,
  tempo: document.getElementById('tempo').disabled,
}));
report('transport disabled with nothing open',
  before.play && before.audio && before.tempo, JSON.stringify(before));

// short-tune sample, support relationship
await page.selectOption('#samples', { label: 'short-tune · support' });
await page.waitForFunction(() => document.querySelectorAll('#decks .deck.on svg').length === 2,
  null, { timeout: 30000 });
const meta = await page.locator('#decks .deck.on .meta').allTextContents();
report('short-tune opens both scores', meta.length === 2 && /8 measures/.test(meta[0]),
  meta.join(' | '));
const phrases = await page.evaluate(() => ({
  a: document.querySelectorAll('#deck-a .phrase').length,
  b: document.querySelectorAll('#deck-b .phrase').length,
}));
report('phrases marked on both decks', phrases.a === 2 && phrases.b === 2, JSON.stringify(phrases));

// click a measure to seek — both decks jump to it
await page.evaluate(() => {
  const box = window.miareViewer.decks.a.geo[4];
  const stage = window.miareViewer.decks.a.stage.getBoundingClientRect();
  const event = new MouseEvent('click', {
    clientX: stage.left + box.x + box.w / 2,
    clientY: stage.top + (box.top + box.bottom) / 2,
    bubbles: true,
  });
  window.miareViewer.decks.a.stage.dispatchEvent(event);
});
await page.waitForTimeout(400);
const seeked = await page.evaluate(() => ({
  i: window.miareViewer.position().i,
  clock: document.getElementById('clock').textContent,
}));
report('click a measure seeks there', seeked.i === 4, JSON.stringify(seeked));

// AC7 + AC2 on the short tune: play from here with the right deck muted
await page.selectOption('#audio', 'b');
await page.click('#play');
await page.waitForTimeout(1200);
const during = await page.evaluate(() => ({
  audible: window.miareViewer.audible(),
  scheduled: window.miareViewer.notesScheduled(),
  playing: window.miareViewer.playing(),
}));
report('plays the right deck only', during.playing && during.audible.join() === 'b'
  && during.scheduled > 0, JSON.stringify(during));
await page.click('#stop');

// AC8 by drag-and-drop: score + its report, both outside samples/
const pairScore = fs.readFileSync(`${REPO}/samples/greensleeves/support.musicxml`, 'utf8');
const pairReport = fs.readFileSync(`${REPO}/samples/greensleeves/support.miare.json`, 'utf8');
await page.evaluate(async ([xml, json]) => {
  const dt = new DataTransfer();
  dt.items.add(new File([xml], 'elsewhere.musicxml', { type: 'application/xml' }));
  dt.items.add(new File([json], 'elsewhere.miare.json', { type: 'application/json' }));
  document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}, [pairScore, pairReport]);
await page.waitForTimeout(3000);
const dropped = await page.evaluate(() => ({
  names: [...document.querySelectorAll('#decks .deck.on .name')].map(n => n.textContent),
  phrasesB: document.querySelectorAll('#deck-b .phrase').length,
  diag: document.getElementById('diag').innerText.slice(0, 80),
}));
report('dropped pair renders with its phrases',
  dropped.names.includes('elsewhere.musicxml') && dropped.phrasesB === 6,
  JSON.stringify(dropped));
await page.screenshot({ path: `${OUT}/${label}-06-dropped-pair.png` });

// a file that is not a score is refused, not crashed on
await page.evaluate(() => {
  const dt = new DataTransfer();
  dt.items.add(new File(['not a score'], 'notes.txt', { type: 'text/plain' }));
  document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
});
await page.waitForTimeout(500);
const refusal = await page.locator('#status').innerText();
report('a non-score file is refused by name', /notes\.txt/.test(refusal), refusal);

// broken XML says so rather than throwing
await page.evaluate(() => {
  const dt = new DataTransfer();
  dt.items.add(new File(['<score-partwise><oops'], 'broken.musicxml', { type: 'application/xml' }));
  document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
});
await page.waitForTimeout(800);
const brokenMsg = await page.locator('#status').innerText();
report('broken MusicXML reports a reason', /Could not read broken\.musicxml/.test(brokenMsg), brokenMsg);

report('no page errors', errors.length === 0, errors.join(' | '));
await browser.close();
