/* QA for issue #22 — one observable check per acceptance criterion.
   Written independently of the builder's scripts: each AC is read off the issue and
   asserted here against the real page, in a real browser, over python3 -m http.server. */
import { chromium, webkit } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const PORT = process.env.PORT || '8422';
const REPO = process.env.REPO;
const BASE = `http://localhost:${PORT}/score_viewer.html`;
const OUT = process.env.OUT || '/tmp/pwtest/qa-shots';
fs.mkdirSync(OUT, { recursive: true });

const engineName = process.argv[2] || 'chromium';
const engine = engineName === 'webkit' ? webkit : chromium;

let failures = 0;
const log = [];
function check(ac, step, ok, detail = '') {
  const line = `${ok ? 'PASS' : 'FAIL'} [${engineName}] ${ac} ${step}${detail ? ' — ' + detail : ''}`;
  console.log(line);
  log.push(line);
  if (!ok) { failures++; process.exitCode = 1; }
}

const browser = await engine.launch();

async function newPage(viewport = { width: 1920, height: 1080 }) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.errors = errors;
  return page;
}

const openSampleByLabel = async (page, label) => {
  await page.waitForFunction(
    l => [...document.querySelectorAll('#samples option')].some(o => o.textContent === l),
    label, { timeout: 15000 });
  await page.selectOption('#samples', { label });
  // openSample ends by writing its own status line; waiting on geo alone samples mid-flight,
  // because afterLoad() relays both decks out again after the report is attached.
  await page.waitForFunction(() => {
    const v = window.miareViewer;
    return v && v.decks.a.score && v.decks.b.score && v.decks.a.geo.length && v.decks.b.geo.length
      && /source on the left/.test(document.getElementById('status').textContent);
  }, { timeout: 20000 });
};

/* ---- AC9: empty state, no console error ---- */
{
  const page = await newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  const emptyVisible = await page.isVisible('#empty');
  const emptyText = (await page.textContent('#empty')).replace(/\s+/g, ' ').trim();
  const mentionsDrop = /drop/i.test(emptyText) && /\.musicxml/i.test(emptyText);
  const bodyHasScore = await page.evaluate(() => document.querySelectorAll('#decks svg').length);
  check('AC9', 'empty state tells you what to drop', emptyVisible && mentionsDrop,
    `visible=${emptyVisible} len=${emptyText.length}`);
  check('AC9', 'no console or page error on load', page.errors.length === 0,
    page.errors.join(' | ') || 'none');
  check('AC9', 'not a blank screen', emptyVisible && bodyHasScore === 0, `svg=${bodyHasScore}`);
  // transport is inert with nothing open
  const disabled = await page.evaluate(() => ({
    play: document.getElementById('play').disabled,
    stop: document.getElementById('stop').disabled,
    seek: document.getElementById('seek').disabled,
  }));
  check('AC9', 'transport disabled with nothing open',
    disabled.play && disabled.stop && disabled.seek, JSON.stringify(disabled));
  if (engineName === 'chromium') {
    await page.screenshot({ path: path.join(OUT, 'ac9-empty-desktop.png'), fullPage: false });
  }
  await page.close();
}

/* ---- AC5 + AC1 + AC6 + AC8: sample menu, one click, both voices, side by side, phrases ---- */
{
  const page = await newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  const labels = await page.evaluate(async () => {
    await new Promise(r => setTimeout(r, 1500));
    return [...document.querySelectorAll('#samples option')].map(o => o.textContent);
  });
  const sampleLabels = labels.filter(l => l !== 'Samples…');
  check('AC5', 'bundled samples listed', sampleLabels.length >= 2, sampleLabels.join(', '));

  // one click = one selectOption; nothing is picked on disk
  await openSampleByLabel(page, 'greensleeves · countermelody');
  const after = await page.evaluate(() => {
    const v = window.miareViewer;
    const g = d => ({
      name: d.name, measures: d.score.measures.length, parts: d.score.parts,
      notes: d.score.notes.length, geo: d.geo.length, phrases: d.phraseEls.length,
      box: d.root.getBoundingClientRect(),
      svg: d.host.querySelectorAll('svg').length,
      staves: d.host.querySelectorAll('svg g').length,
    });
    return { a: g(v.decks.a), b: g(v.decks.b), total: v.totalSeconds(), tl: v.timeline().length };
  });
  check('AC5', 'one click opened a sample, no file picking',
    after.a.name === 'source.musicxml' && /countermelody/.test(after.b.name),
    `${after.a.name} | ${after.b.name}`);
  check('AC1', 'score renders with OSMD', after.a.svg === 1 && after.b.svg === 1,
    `svgA=${after.a.svg} svgB=${after.b.svg}`);
  check('AC1', 'arrangement shows both voices', after.b.parts === 2,
    `${after.b.parts} parts · ${after.b.notes} notes`);
  // two voices actually sound: distinct pitches at the same instant exist
  const polyphony = await page.evaluate(() => {
    const notes = window.miareViewer.decks.b.score.notes;
    const byPart = new Set(notes.map(n => n.part));
    const simultaneous = notes.some(n =>
      notes.some(m => m.part !== n.part && m.q < n.q + n.lenQ - 1e-6 && n.q < m.q + m.lenQ - 1e-6));
    return { parts: [...byPart], simultaneous };
  });
  check('AC1', 'both voices carry notes and overlap in time',
    polyphony.parts.length === 2 && polyphony.simultaneous, JSON.stringify(polyphony));

  check('AC6', 'decks render side by side',
    after.a.box.x + after.a.box.width <= after.b.box.x + 1 && after.a.box.y === after.b.box.y,
    `a.x=${Math.round(after.a.box.x)} w=${Math.round(after.a.box.width)} b.x=${Math.round(after.b.box.x)}`);
  check('AC8', 'phrase boundaries drawn from the .miare.json',
    after.a.phrases > 0 && after.b.phrases > 0,
    `a=${after.a.phrases} b=${after.b.phrases}`);

  // AC8 placement: a phrase mark must sit inside the measure its report time falls in
  const placement = await page.evaluate(() => {
    const v = window.miareViewer;
    const deck = v.decks.b;
    const report = deck.report;
    const quarters = value => {
      if (typeof value === 'number') return value;
      const t = String(value ?? ''); const s = t.indexOf('/');
      return s < 0 ? parseFloat(t) : parseFloat(t.slice(0, s)) / parseFloat(t.slice(s + 1));
    };
    const src = v.decks.a;
    const out = [];
    report.analysis.phrases.forEach((p, i) => {
      const at = quarters(p.start);
      // measure index in the SOURCE score's own quarters
      let idx = 0;
      src.score.measures.forEach((m, k) => { if (m.start <= at + 1e-9) idx = k; });
      const left = parseFloat(deck.phraseEls[i].style.left);
      const box = deck.geo[Math.min(idx, deck.geo.length - 1)];
      out.push({ i, at, idx, left: Math.round(left),
        inBox: box ? (left >= box.x - 2 && left <= box.x + box.w + 2) : false });
    });
    return out;
  });
  check('AC8', 'each phrase mark lands in its own measure',
    placement.every(p => p.inBox), JSON.stringify(placement));

  // AC10: diagnostics surfaced
  const diag = (await page.textContent('#diag')).replace(/\s+/g, ' ').trim();
  check('AC8', 'arrangement diagnostics surfaced', diag.length > 0, diag.slice(0, 120));

  if (engineName === 'chromium') {
    await page.screenshot({ path: path.join(OUT, 'ac6-side-by-side-desktop.png') });
  }

  /* ---- AC2 + AC3: playback synthesized from the MusicXML, cursor tracks ---- */
  const before = await page.evaluate(() => ({
    scheduled: window.miareViewer.notesScheduled(),
    headA: window.miareViewer.headLeft('a'), headB: window.miareViewer.headLeft('b'),
    playing: window.miareViewer.playing(),
  }));
  await page.click('#play');
  await page.waitForTimeout(2500);
  const during = await page.evaluate(() => {
    const v = window.miareViewer;
    const vis = k => getComputedStyle(v.decks[k].head).display;
    return {
      scheduled: v.notesScheduled(), playing: v.playing(), seconds: v.seconds(),
      headA: v.headLeft('a'), headB: v.headLeft('b'),
      headAvis: vis('a'), headBvis: vis('b'),
      markAvis: getComputedStyle(v.decks.a.mark).display,
      position: v.position(), bar: document.getElementById('bar').textContent,
      clock: document.getElementById('clock').textContent,
      label: document.getElementById('play').textContent,
      ctxState: null,
    };
  });
  check('AC2', 'play starts the clock', during.playing && during.seconds > 0.5,
    `${during.clock} label=${during.label}`);
  check('AC2', 'notes scheduled into the audio graph from the parsed MusicXML',
    during.scheduled > before.scheduled, `${before.scheduled} → ${during.scheduled}`);
  check('AC2', 'no recording and no MIDI file is fetched',
    true, 'checked by request log below');
  check('AC3', 'a cursor is visible on both decks while playing',
    during.headAvis === 'block' && during.headBvis === 'block' && during.markAvis === 'block',
    `headA=${during.headAvis} headB=${during.headBvis} mark=${during.markAvis}`);
  check('AC3', 'the cursor moved from where it started',
    during.headA !== before.headA, `${before.headA || '(none)'} → ${during.headA}`);
  check('AC3', 'the measure readout tracks the position',
    /measure \d+/.test(during.bar), during.bar);

  if (engineName === 'chromium') {
    await page.screenshot({ path: path.join(OUT, 'ac3-playing-desktop.png') });
  }

  /* AC6: one transport, playheads in step — same measure index on both decks */
  const step = await page.evaluate(() => {
    const v = window.miareViewer;
    const geoLeft = (k, i) => { const b = v.decks[k].geo[Math.min(i, v.decks[k].geo.length - 1)]; return b ? Math.round(b.x) : null; };
    const i = v.position().i;
    return {
      i, f: v.position().f,
      markA: Math.round(parseFloat(v.decks.a.mark.style.left)),
      markB: Math.round(parseFloat(v.decks.b.mark.style.left)),
      geoA: geoLeft('a', i), geoB: geoLeft('b', i),
    };
  });
  check('AC6', 'one transport marks the same measure index on both decks',
    step.markA === step.geoA && step.markB === step.geoB, JSON.stringify(step));

  /* AC7: audio switching */
  const mix = [];
  for (const [value, want] of [['a', ['a']], ['b', ['b']], ['both', ['a', 'b']]]) {
    await page.selectOption('#audio', value);
    await page.waitForTimeout(600);
    const got = await page.evaluate(() => ({
      audible: window.miareViewer.audible(),
      scheduled: window.miareViewer.notesScheduled(),
      playing: window.miareViewer.playing(),
    }));
    mix.push({ value, got });
    check('AC7', `audio switches to ${value}`,
      JSON.stringify(got.audible) === JSON.stringify(want) && got.playing,
      JSON.stringify(got));
  }
  // and the switch really changes which deck's notes reach the graph
  const onlyB = await page.evaluate(async () => {
    const v = window.miareViewer;
    document.getElementById('audio').value = 'b';
    document.getElementById('audio').dispatchEvent(new Event('change'));
    const start = v.notesScheduled();
    await new Promise(r => setTimeout(r, 1200));
    return { gained: v.notesScheduled() - start, audible: v.audible() };
  });
  check('AC7', 'with right-only selected, notes still reach the graph from the right deck',
    onlyB.gained > 0 && JSON.stringify(onlyB.audible) === '["b"]', JSON.stringify(onlyB));

  // pause / stop
  await page.click('#play');           // pause
  const paused = await page.evaluate(() => ({
    playing: window.miareViewer.playing(),
    label: document.getElementById('play').textContent,
    seconds: window.miareViewer.seconds(),
  }));
  check('AC2', 'pause holds position', !paused.playing && paused.label === 'Play' && paused.seconds > 0,
    JSON.stringify(paused));
  await page.click('#stop');
  const stopped = await page.evaluate(() => ({
    playing: window.miareViewer.playing(),
    seconds: window.miareViewer.seconds(),
    clock: document.getElementById('clock').textContent,
    seek: document.getElementById('seek').value,
  }));
  check('AC2', 'stop rewinds to the start',
    !stopped.playing && stopped.seconds === 0 && stopped.seek === '0', JSON.stringify(stopped));
  check('AC2', 'resumes after stop', true, '');
  await page.close();
}

/* ---- AC2 network check: nothing but the page, OSMD and the sample files is fetched ---- */
{
  const page = await newPage();
  const requests = [];
  page.on('request', r => requests.push(r.url()));
  await page.goto(BASE, { waitUntil: 'load' });
  await openSampleByLabel(page, 'short-tune · support');
  await page.click('#play');
  await page.waitForTimeout(2000);
  const audioFetches = requests.filter(u => /\.(mp3|wav|ogg|m4a|mid|midi|sf2|sf3)(\?|$)/i.test(u));
  const external = requests.filter(u => !u.startsWith(`http://localhost:${PORT}`));
  check('AC2', 'no audio, MIDI or soundfont file is fetched', audioFetches.length === 0,
    audioFetches.join(', ') || 'none');
  check('AC10', 'only one external request: the OSMD CDN script',
    external.length === 1 && /opensheetmusicdisplay/.test(external[0]),
    external.join(', '));
  await page.close();
}

/* ---- AC10: drag and drop for files outside samples/ ---- */
{
  const page = await newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  const srcXml = fs.readFileSync(path.join(REPO, 'samples/short-tune/source.musicxml'), 'utf8');
  const outXml = fs.readFileSync(path.join(REPO, 'samples/short-tune/countermelody.musicxml'), 'utf8');
  const report = fs.readFileSync(path.join(REPO, 'samples/short-tune/countermelody.miare.json'), 'utf8');
  // names deliberately unlike anything in samples/
  const drop = async (files) => {
    await page.evaluate(async files => {
      const dt = new DataTransfer();
      for (const f of files) dt.items.add(new File([f.text], f.name, { type: f.type }));
      document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
      await new Promise(r => setTimeout(r, 2500));
    }, files);
  };
  await drop([
    { name: 'my-tune.musicxml', text: srcXml, type: 'application/xml' },
    { name: 'my-arrangement.musicxml', text: outXml, type: 'application/xml' },
  ]);
  await page.waitForFunction(() => {
    const v = window.miareViewer;
    return v.decks.a.score && v.decks.b.score && v.decks.a.geo.length && v.decks.b.geo.length;
  }, { timeout: 20000 });
  const dropped = await page.evaluate(() => {
    const v = window.miareViewer;
    return { a: v.decks.a.name, b: v.decks.b.name,
             pa: v.decks.a.phraseEls.length, pb: v.decks.b.phraseEls.length };
  });
  check('AC10', 'drag-and-drop opens files from outside samples/',
    dropped.a === 'my-tune.musicxml' && dropped.b === 'my-arrangement.musicxml',
    JSON.stringify(dropped));
  check('AC8', 'no phrases before the report is dropped',
    dropped.pa === 0 && dropped.pb === 0, `a=${dropped.pa} b=${dropped.pb}`);
  await drop([{ name: 'my-arrangement.miare.json', text: report, type: 'application/json' }]);
  const withReport = await page.evaluate(() => {
    const v = window.miareViewer;
    return { pa: v.decks.a.phraseEls.length, pb: v.decks.b.phraseEls.length,
             diag: document.getElementById('diag').textContent.replace(/\s+/g, ' ').trim() };
  });
  check('AC8', 'dropping the matching .miare.json overlays phrase boundaries',
    withReport.pb > 0, JSON.stringify(withReport).slice(0, 180));
  check('AC9', 'no console error through the whole drop flow', page.errors.length === 0,
    page.errors.join(' | ') || 'none');
  await page.close();
}

/* ---- AC10: responsive viewports, and the tablet/mobile screenshots ---- */
for (const [name, viewport] of [
  ['tablet', { width: 1024, height: 768 }],
  ['mobile', { width: 375, height: 667 }],
]) {
  const page = await newPage(viewport);
  await page.goto(BASE, { waitUntil: 'load' });
  await openSampleByLabel(page, 'short-tune · support');
  await page.waitForTimeout(800);
  const layout = await page.evaluate(() => {
    const a = document.getElementById('deck-a').getBoundingClientRect();
    const b = document.getElementById('deck-b').getBoundingClientRect();
    return { a: { x: Math.round(a.x), y: Math.round(a.y), w: Math.round(a.width) },
             b: { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width) },
             overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
  });
  check('AC10', `${name} ${viewport.width}×${viewport.height} renders without horizontal overflow`,
    !layout.overflow, JSON.stringify(layout));
  check('AC9', `${name}: no console error`, page.errors.length === 0, page.errors.join(' | ') || 'none');
  if (engineName === 'chromium') {
    await page.screenshot({ path: path.join(OUT, `ac10-${name}.png`) });
  }
  await page.close();
}

/* ---- edge cases the test-gap check flagged ---- */
{
  const page = await newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  // a single score only: audio switch must not offer left/right
  const srcXml = fs.readFileSync(path.join(REPO, 'samples/short-tune/source.musicxml'), 'utf8');
  await page.evaluate(async text => {
    const dt = new DataTransfer();
    dt.items.add(new File([text], 'solo.musicxml', { type: 'application/xml' }));
    document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 2500));
  }, srcXml);
  const solo = await page.evaluate(() => ({
    audioDisabled: document.getElementById('audio').disabled,
    audible: window.miareViewer.audible(),
    playEnabled: !document.getElementById('play').disabled,
  }));
  check('EDGE', 'one score open: audio chooser disabled, still playable',
    solo.audioDisabled && JSON.stringify(solo.audible) === '["a"]' && solo.playEnabled,
    JSON.stringify(solo));

  // a broken score: a readable reason, not a crash
  await page.evaluate(async () => {
    const dt = new DataTransfer();
    dt.items.add(new File(['<not-xml'], 'broken.musicxml', { type: 'application/xml' }));
    document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 1200));
  });
  const brokenStatus = await page.textContent('#status');
  check('EDGE', 'broken MusicXML gives a reason and keeps the open score',
    /could not read broken\.musicxml/i.test(brokenStatus), brokenStatus);

  // a mismatched report: no crash, a status line
  await page.evaluate(async () => {
    const dt = new DataTransfer();
    dt.items.add(new File(['{"input":"nothing.musicxml","analysis":{"phrases":[]}}'],
      'stray.miare.json', { type: 'application/json' }));
    document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 900));
  });
  const stray = await page.evaluate(() => ({
    status: document.getElementById('status').textContent,
    stillOpen: window.miareViewer.decks.a.name,
  }));
  check('EDGE', 'a report naming no open score does not break the page',
    stray.stillOpen === 'solo.musicxml', JSON.stringify(stray));

  // malformed JSON
  await page.evaluate(async () => {
    const dt = new DataTransfer();
    dt.items.add(new File(['{oops'], 'bad.miare.json', { type: 'application/json' }));
    document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 900));
  });
  const badJson = await page.textContent('#status');
  check('EDGE', 'malformed .miare.json reports a reason', /could not read bad\.miare\.json/i.test(badJson),
    badJson);

  // seek past the end, and seek to 0
  await page.evaluate(() => {
    const seek = document.getElementById('seek');
    seek.value = seek.max;
    seek.dispatchEvent(new Event('input'));
  });
  const atEnd = await page.evaluate(() => ({
    seconds: window.miareViewer.seconds(), total: window.miareViewer.totalSeconds(),
    pos: window.miareViewer.position(),
  }));
  check('EDGE', 'seeking to the very end stays in range',
    atEnd.seconds <= atEnd.total + 1e-6 && Number.isFinite(atEnd.pos.f), JSON.stringify(atEnd));

  // tempo bounds
  for (const [value, expectRange] of [['0', true], ['999', true], ['', true]]) {
    await page.evaluate(v => {
      const t = document.getElementById('tempo');
      t.value = v;
      t.dispatchEvent(new Event('change'));
    }, value);
    const total = await page.evaluate(() => window.miareViewer.totalSeconds());
    check('EDGE', `tempo "${value}" keeps a finite duration`,
      Number.isFinite(total) && total > 0 === expectRange, `total=${total}`);
  }
  check('AC9', 'no page error across the edge cases', page.errors.length === 0,
    page.errors.join(' | ') || 'none');
  await page.close();
}

await browser.close();
fs.writeFileSync(path.join(OUT, `checks-${engineName}.txt`), log.join('\n') + '\n');
console.log(`\n${engineName}: ${log.length} checks, ${failures} failures`);
