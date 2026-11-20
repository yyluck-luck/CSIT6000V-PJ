// Browser self-check for the web UI: starts its own server (mock model, bundled demo tasks,
// throw-away run directory) and drives headless Chrome over the DevTools protocol. No npm packages.
//
//   node scripts/ui_check.mjs            # exit 0 = every check passed (or SKIP when no Chrome / Node < 22)
//   CHROME=/path/to/chrome node scripts/ui_check.mjs
//
// What it covers: landing, page switching (latency + 60 random switches + back/forward), the
// challenge picker, a live run with steps / scrubber / drawer / copy prompt, replay from Runs, the
// four-tier sweep with the compare slider, upload (gooey gold flag), the Ask page (Enter to send,
// typing dots, threads), a narrow viewport, reduced motion, and "no CJK text on screen".
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const [major] = process.versions.node.split(".").map(Number);
if (major < 22 || typeof WebSocket === "undefined") { console.log(`SKIP ui_check: needs Node >= 22 (found ${process.versions.node})`); process.exit(0); }
const CHROME = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(p => p && existsSync(p));
if (!CHROME) { console.log("SKIP ui_check: no Chrome/Chromium found (set CHROME=...)"); process.exit(0); }

const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
const work = mkdtempSync(join(tmpdir(), "fp-ui-"));
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };

// ---------- server ----------
const port = await freePort();
const env = { ...process.env, FLAGPILOT_RUNS: join(work, "runs"), FLAGPILOT_TASKS: join(ROOT, "demo-tasks"), FLAGPILOT_MEMORY: join(work, "mem"), PYTHONDONTWRITEBYTECODE: "1" };
const srv = spawn(process.env.PYTHON || "python3", ["-m", "flagpilot", "web", "--host", "127.0.0.1", "--port", String(port), "--model", "mock"], { cwd: ROOT, env, stdio: "ignore" });
const base = `http://127.0.0.1:${port}`;
for (let i = 0; i < 100; i++) { try { if ((await fetch(base + "/api/tasks")).ok) break; } catch {} await sleep(100); }

// ---------- chrome ----------
const cport = await freePort();
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${cport}`, `--user-data-dir=${join(work, "chrome")}`, "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--window-size=1440,900", "about:blank"], { stdio: "ignore" });
let wsUrl = null;
for (let i = 0; i < 100 && !wsUrl; i++) { try { const l = await (await fetch(`http://127.0.0.1:${cport}/json/list`)).json(); wsUrl = (l.find(t => t.type === "page") || {}).webSocketDebuggerUrl; } catch {} if (!wsUrl) await sleep(100); }
const ws = new WebSocket(wsUrl);
await new Promise(r => (ws.onopen = r));
let seq = 0; const pending = new Map(), pageErrors = [];
ws.onmessage = m => { const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
  if (d.method === "Runtime.exceptionThrown") pageErrors.push((d.params.exceptionDetails.exception || {}).description || d.params.exceptionDetails.text);
  else if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") pageErrors.push("console.error: " + d.params.args.map(a => a.value ?? a.description).join(" "));
  else if (d.method === "Log.entryAdded" && d.params.entry.level === "error" && !/favicon/.test(d.params.entry.url || "")) pageErrors.push("log: " + d.params.entry.text); };
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, d => d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result)); ws.send(JSON.stringify({ id, method, params })); });
const js = async expr => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception || {}).description || expr); return r.result.value; };
const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await js(`!!(${expr})`)) return true; } catch {} await sleep(40); } return false; };
const vp = (w, h) => send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
const nav = async url => { await send("Page.navigate", { url }); await until("document.readyState === 'complete'", 10000); await sleep(150); };
const noCJK = async label => { const hits = await js(`(document.body.innerText.match(/[\\u3000-\\u9fff\\uff00-\\uffef]+/g) || []).slice(0, 5)`); check(`no CJK text on screen (${label})`, !hits.length, hits.join(" ")); };
const SMALL = `(() => { const out = new Set(); const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()){ const n = w.currentNode, t = n.textContent.trim(), el = n.parentElement; if (!t || !el) continue;
    if (el.closest('[hidden],script,style,canvas,svg,.cb,code,pre,.n,#tip') || (el.closest('.page') && !el.closest('.page.on'))) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el); if (!r.width || !r.height || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    if (parseFloat(cs.fontSize) < 14.9) out.add(t.slice(0, 30) + ' @' + cs.fontSize); }
  return [...out].slice(0, 6); })()`;

await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable"); await vp(1440, 900);
await send("Browser.grantPermissions", { permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"], origin: base }).catch(() => {});
try {
  // 1. landing
  await nav(base + "/#/solve");
  check("landing renders", await until("document.querySelectorAll('#tasks .row').length > 0 && !document.querySelector('#landing').hidden"));
  const fill = await js("(() => { const F = document.querySelector('#feats').getBoundingClientRect(); return {table: !!document.querySelector('#latest'), right: Math.round(innerWidth - F.right), bottom: Math.round(F.bottom), vh: innerHeight, ox: document.documentElement.scrollWidth - innerWidth}; })()");
  check("landing fills the window: no side table, cards span the width, one screen", !fill.table && fill.right < 120 && fill.bottom <= fill.vh && fill.ox <= 0, JSON.stringify(fill));
  const smallLanding = await js(SMALL); check("no small text on the landing", !smallLanding.length, smallLanding.join(" | "));
  const w0 = await js("document.querySelector('#flip').textContent");
  check("layout text flip cycles", await until(`document.querySelector('#flip').textContent.trim() !== ${JSON.stringify(w0.trim())}`, 4000));
  await noCJK("landing");

  // 2. page switch latency: the new page must be committed inside the click handler
  const lat = await js(`(async () => { const out = []; for (const p of ['history','lab','upload','chat','solve','history','solve']) {
      await new Promise(r => setTimeout(r, 120)); const t0 = performance.now(); document.querySelector('.navbtn[data-page="' + p + '"]').click();
      const ms = performance.now() - t0; out.push([p, ms, document.querySelector('.page[data-page="' + p + '"]').classList.contains('on')]); } return out; })()`);
  const worst = Math.max(...lat.map(x => x[1]));
  check("page switch commits in the click frame", lat.every(x => x[2]) && worst < 50, `worst handler ${worst.toFixed(1)} ms; ` + lat.map(x => `${x[0]} ${x[1].toFixed(1)}`).join(", "));

  // the rail pill stretches over the items it passes (labels invert under it) and lands exactly on the last one clicked
  const pill = await js(`(async () => { const pl = document.querySelector('#navPill'), at = p => document.querySelector('.navbtn[data-page="' + p + '"]').getBoundingClientRect();
      document.querySelector('.navbtn[data-page="solve"]').click(); await new Promise(r => setTimeout(r, 700));
      document.querySelector('.navbtn[data-page="chat"]').click(); const a = pl.getAnimations()[0]; if (!a) return 'no animation';
      a.pause(); a.currentTime = a.effect.getTiming().duration * .45; const mid = pl.getBoundingClientRect(), span = Math.round(mid.bottom - mid.top), want = Math.round(at('chat').bottom - at('solve').top); a.play();
      for (const p of ['lab', 'upload', 'history']) document.querySelector('.navbtn[data-page="' + p + '"]').click();
      await new Promise(r => setTimeout(r, 800)); const end = pl.getBoundingClientRect(), b = at('history');
      return [span === want, Math.abs(end.top - b.top) < 1 && Math.abs(end.height - b.height) < 1, getComputedStyle(document.querySelector('.navbtn .lb')).mixBlendMode].join(); })()`);
  check("rail pill stretches across the items it passes and lands on the last click", pill === "true,true,difference", pill);

  // 3. stress: 60 random switches with random gaps + back/forward
  const stress = await js(`(async () => { const P = ['solve','history','lab','upload','chat']; let s = 11; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647; const bad = [];
      const ok = async want => { await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); const el = document.querySelector('.page[data-page="' + want + '"]');
        const on = [...document.querySelectorAll('.page.on')].map(x => x.dataset.page); const st = document.querySelector('#stage').getBoundingClientRect();
        const hit = document.elementFromPoint(st.left + st.width / 2, st.top + 160);
        if (on.length !== 1 || on[0] !== want || el.inert || getComputedStyle(el).visibility !== 'visible' || !(hit && el.contains(hit))) bad.push(want + ':' + on.join(',')); };
      for (let i = 0; i < 60; i++) { const w = P[Math.floor(rnd() * 5)]; document.querySelector('.navbtn[data-page="' + w + '"]').click(); await new Promise(r => setTimeout(r, Math.floor(rnd() * 220))); await ok(w); }
      for (let i = 0; i < 6; i++) { history.back(); await new Promise(r => setTimeout(r, 160)); await ok((location.hash.match(/^#\\/(\\w+)/) || [, 'solve'])[1]); }
      return bad; })()`);
  check("60 random page switches + back/forward", !stress.length, stress.slice(0, 5).join(" "));

  // 4. picker
  await js("document.querySelector('.navbtn[data-page=\"solve\"]').click()");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "/", code: "Slash", text: "/" }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "/", code: "Slash" });
  check("'/' opens the picker", await until("!document.querySelector('#sheetWrap').hidden", 2000));
  const name = await js("document.querySelector('#tasks .row').dataset.name");
  await js(`(() => { const s = document.querySelector('#taskSearch'); s.value = ${JSON.stringify(name)}; s.dispatchEvent(new Event('input', {bubbles: true})); })()`);
  check("search filters the list", await js("[...document.querySelectorAll('#tasks .row')].filter(r => !r.hidden).length >= 1 && [...document.querySelectorAll('#tasks .row')].some(r => r.hidden)"));
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  const picked = await until(`document.querySelector('#sheetWrap').hidden && !document.querySelector('#work').hidden && !document.querySelector('#run').disabled`, 3000);
  check("Enter picks and closes", picked, picked ? "" : await js(`JSON.stringify({sheet: document.querySelector('#sheetWrap').hidden, cls: document.querySelector('#sheetWrap').className, work: document.querySelector('#work').hidden, run: document.querySelector('#run').disabled, page: document.querySelector('.page.on').dataset.page, active: document.activeElement.id, q: document.querySelector('#taskSearch').value, vis: [...document.querySelectorAll('#tasks .row')].filter(r => !r.hidden).map(r => r.dataset.name)})`));

  // 5. live run
  await js("document.querySelector('#run').click()");
  check("run reaches a verdict", await until("document.querySelector('#verdict .verdict:not(.run)')", 20000));
  const smallSolve = await js(SMALL); check("no small text on Solve after a run", !smallSolve.length, smallSolve.join(" | "));
  check("verdict bar does not repeat the readouts", await js("!/turns|\\d+\\.\\d+ s/.test((document.querySelector('#verdict .vbar') || {}).textContent || 'x')") && await js("!!document.querySelector('#promptBtn')"));
  check("steps rendered", await js("document.querySelectorAll('#flow details.step').length >= 2"));
  check("scrubber has a tick per step", await js("!document.querySelector('#scrub').hidden && document.querySelectorAll('#scrub .tk').length === document.querySelectorAll('#flow details.step').length"));
  await js("(() => { const s = document.querySelector('#flow details.step'); s.open = true; })()");
  check("step body shows why / thought / tool output", await until("document.querySelector('#flow details.step[open] .sec summary[title*=\"auto-explained\"]') && document.querySelector('#flow details.step[open] .cb')", 2000));
  await js("document.querySelector('#inspBtn').click()");
  check("details drawer opens", await until("document.querySelector('#insp').classList.contains('open')", 1000));
  await js("document.querySelector('#chainTrack .stp').click()");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  check("Esc closes the drawer", await until("!document.querySelector('#insp').classList.contains('open')", 1000));
  await js("document.querySelector('#promptBtn').click()");
  check("copy prompt button completes", await until("/Copied|Copy failed/.test(document.querySelector('#promptBtn').textContent)", 3000), await js("document.querySelector('#promptBtn').textContent"));
  // a TACC run that fails says why and stops the clock (it used to sit on RUNNING while the timer kept counting)
  await js("QW = {phase: 'ready', remaining: 600}"); await js("document.querySelector('#runTacc').click()");
  const taccErr = await until("/ERROR/.test((document.querySelector('#verdict .vcode') || {}).textContent || '') && /not connected/.test((document.querySelector('#verdict .vlabel') || {}).textContent || '')", 8000);
  const clockA = await js("document.querySelector('#mE2e').dataset.s"); await sleep(1200);
  const clockB = await js("document.querySelector('#mE2e').dataset.s"), clockLive = await js("document.querySelector('#mE2e').classList.contains('live')");
  check("a TACC run that fails shows ERROR with the reason and stops the clock", taccErr && !clockLive && clockA === clockB && /^\d+\.\d$/.test(clockA), `${taccErr} live=${clockLive} ${clockA} ${clockB}`);
  await js("QW = {phase: 'idle'}");
  await noCJK("solve");

  // 5b. watching a run: step mode holds each command; it can be edited or skipped; a hint reaches the next step; a step can be explained in Ask
  await js("document.querySelector('.navbtn[data-page=\"solve\"]').click()");
  await until("!document.querySelector('#run').disabled", 5000);
  await js("(() => { const c = document.querySelector('#ctlStep'); c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await js("document.querySelector('#run').click()");
  const stHeld = await until("!document.querySelector('#awaitCard').hidden && document.querySelector('#awaitText')", 20000);
  await js("(() => { const i = document.querySelector('#ctlHint'); i.value = 'try base64'; document.querySelector('#ctlHintGo').click(); })()");
  await sleep(300);
  await js("(() => { const t = document.querySelector('#awaitText'); if (!t.readOnly) t.value = 'echo hello-from-test'; document.querySelector('#awRun').click(); })()");
  const edited = await until("run && run.turns.some(t => t.decision === 'edit' && t.tool && /hello-from-test/.test(t.tool.obs || ''))", 15000);
  const stSecond = await until("!document.querySelector('#awaitCard').hidden", 8000);
  if (stSecond) await js("document.querySelector('#awSkip').click()");
  const stSkipped = !stSecond || await until("run && run.turns.some(t => t.skipped)", 10000);
  await js("(() => { const c = document.querySelector('#ctlStep'); c.checked = false; c.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await js("document.querySelector('#awRun') && document.querySelector('#awRun').click()");
  const finished = await until("document.querySelector('#verdict .verdict:not(.run)') && document.querySelector('#runCtl').hidden", 30000);
  const hinted = await js("!!(run && run.turns.some(t => /base64/.test(t.hint || '')))");
  check("step mode: a held command can be edited, skipped or run; a hint reaches the next step", stHeld && edited && stSkipped && finished && hinted, JSON.stringify({stHeld, edited, stSecond, stSkipped, finished, hinted}));
  await js("localStorage.removeItem('fp.stepmode')");
  await js("(() => { const d = document.querySelector('#flow details.step'); if (d) d.open = true; const b = document.querySelector('[data-explain]'); b && b.click(); })()");
  check("a step can be explained in Ask", await until("document.querySelector('.page.on').dataset.page === 'chat' && /Explain step \\d+ of my run/.test((document.querySelector('#chatLog .bub.me') || {}).textContent || '') && document.querySelector('#chatLog .bub.bot .md')", 15000));
  await js("document.querySelector('.navbtn[data-page=\"solve\"]').click()");

  // 6. runs + replay
  await js("document.querySelector('.navbtn[data-page=\"history\"]').click()");
  check("runs list and stats render", await until("document.querySelectorAll('#histList .row').length >= 1 && /\\d/.test(document.querySelector('#hs-n').textContent)", 5000));
  const smallRuns = await js(SMALL); check("no small text on Runs", !smallRuns.length, smallRuns.join(" | "));
  check("leaving Solve clears the finished verdict from the status pill", await until("document.querySelector('#status').textContent === 'Ready' && !document.querySelector('#islMore').textContent", 2000), await js("document.querySelector('#status').textContent + ' / ' + document.querySelector('#islMore').textContent"));
  await js("document.querySelector('#histList .row').click()");
  check("replay from Runs reaches a verdict", await until("document.querySelector('.page.on').dataset.page === 'solve' && document.querySelector('#verdict .verdict:not(.run)')", 10000));

  // 7. lab: explains itself; a comparison runs in parallel and fills the results; squares open replays; batch map; step diff; probe slider
  await js("document.querySelector('.navbtn[data-page=\"lab\"]').click()");
  check("lab explains itself: tier ladder, legend, what each tab does", await until("document.querySelectorAll('#pLab .ladder li').length === 4 && document.querySelectorAll('#pLab .legend span').length === 4 && /Every experiment/.test(document.querySelector('#labWhy').textContent)", 3000));
  await js("document.querySelector('#labSeg button[data-lab=\"compare\"]').click()");
  await until("document.querySelectorAll('#cmpModels input:checked').length === 1 && document.querySelectorAll('#cmpTiers input:checked').length === 4", 3000);
  const plan = await js("document.querySelector('#cmpPlan').textContent");
  await js("document.querySelector('#sweep').click()");
  const cmpDone = await until("document.querySelector('#cmpView .forest') && document.querySelectorAll('#cmpView .hc i[data-run]').length === 4 && !document.querySelector('#cmpView .livebar')", 60000);
  const concl = cmpDone ? await js("document.querySelector('#cmpView .concl').textContent") : "";
  check("compare: 4 tiers on one challenge give a conclusion, a forest plot and a square per attempt", cmpDone && /4 arms × 1 repeat = 4 attempts/.test(plan) && /verified/.test(concl), JSON.stringify({plan, concl}));
  await js("document.querySelector('#labSeg button[data-lab=\"results\"]').click()");
  const listed = await until("document.querySelectorAll('#expList .exp').length >= 1 && document.querySelector('#expView .forest') && document.querySelector('#expView .hc i[data-run]')", 8000);
  if (listed) await js("document.querySelector('#expView .hc i[data-run]').click()");
  const replayed = listed && await until("document.querySelector('.page.on').dataset.page === 'solve' && /[?&]run=/.test(location.search) && document.querySelector('#verdict .verdict:not(.run)')", 8000);
  check("results: the experiment is listed; a square opens that attempt's replay", listed && replayed, JSON.stringify({listed, replayed}));
  await js("document.querySelector('.navbtn[data-page=\"lab\"]').click()");
  await js("document.querySelector('#labSeg button[data-lab=\"batch\"]').click()");
  await until("document.querySelectorAll('#batCats input').length > 0 && !document.querySelector('#batRun').disabled", 3000);
  await js("document.querySelector('#batRun').click()");
  const batDone = await until("document.querySelector('#batView .heat') && !document.querySelector('#batView .livebar') && document.querySelectorAll('#batView .hc i[data-run]').length >= 4", 90000);
  check("batch: the challenge × tier map fills in", batDone);
  await js("(document.querySelector('#batView [data-diff]') || {click(){}}).click()");
  const diffed = await until("document.querySelector('#labSeg button.on').dataset.lab === 'diff' && document.querySelector('#diffView .diff .dc') && / ended /.test(document.querySelector('#diffView .concl').textContent)", 10000);
  check("diff: two attempts side by side, with where they diverged and how each ended", diffed);
  await js("document.querySelector('#labSeg button[data-lab=\"probe\"]').click()");
  await js("document.querySelector('#probeBtn').click()");
  check("probe: the bare model's answer sits next to the agent's", await until("document.querySelector('#probeSlider .cmp')", 20000));
  const moved = await js(`(() => { const b = document.querySelector('#probeSlider .cmp'); if (!b) return 'none'; const r = b.getBoundingClientRect();
      const ev = (t, x) => b.dispatchEvent(new PointerEvent(t, {clientX: x, clientY: r.top + 40, pointerId: 1, bubbles: true}));
      ev('pointerdown', r.left + r.width * .5); ev('pointermove', r.left + r.width * .25); ev('pointerup', r.left + r.width * .25); return b.style.getPropertyValue('--x'); })()`);
  check("compare slider follows the drag", parseFloat(moved) < 40, "x=" + moved);
  const smallLab = await js(SMALL); check("no small text on Lab", !smallLab.length, smallLab.join(" | "));
  await noCJK("lab");

  // 8. upload with the gooey gold field
  await js("document.querySelector('.navbtn[data-page=\"upload\"]').click()");
  const f = join(work, "hello.txt"); writeFileSync(f, "picoCTF{ui_check}\n");
  const doc = await send("DOM.getDocument"); const { nodeId } = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#upFiles" });
  await send("DOM.setFileInputFiles", { nodeId, files: [f] });
  check("attachment listed", await until("document.querySelectorAll('#upFileList .file').length === 1", 2000));
  await js(`(() => { const t = document.querySelector('#upTitle'); t.value = 'ui check'; document.querySelector('#goldPill').click(); })()`);
  await js(`(() => { const i = document.querySelector('#upGold'); i.value = 'not a flag'; i.dispatchEvent(new Event('input', {bubbles: true})); document.querySelector('#goldOk').click(); })()`);
  check("malformed gold flag shakes", await js("document.querySelector('#goldGoo').classList.contains('shake')"));
  await js(`(() => { const i = document.querySelector('#upGold'); i.value = 'picoCTF{ui_check}'; i.dispatchEvent(new Event('input', {bubbles: true})); document.querySelector('#goldOk').click(); })()`);
  check("valid gold flag is accepted", await js("document.querySelector('#goldGoo').classList.contains('set')"));
  await js("document.querySelector('#doUpload').click()");
  check("upload creates a challenge", await until("!document.querySelector('#upDone').hidden", 8000));
  await js("document.querySelector('#goSolveNew').click()");
  check("'Solve it' opens it on Solve", await until("document.querySelector('.page.on').dataset.page === 'solve' && /custom|ui/.test(document.querySelector('#sgId').textContent)", 3000));

  // 9. ask: Enter sends, typing dots, threads
  await js("document.querySelector('.navbtn[data-page=\"chat\"]').click()");
  await until("document.activeElement === document.querySelector('#chatInput')", 2000);
  await js(`(() => { const t = document.querySelector('#chatInput'); t.value = 'how do I start?'; t.dispatchEvent(new Event('input', {bubbles: true})); })()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  check("Enter sends; my bubble on the right", await until("document.querySelector('#chatLog .bub.me')", 2000));
  check("assistant reply lands on the left", await until("document.querySelector('#chatLog .bub.bot .md') && !document.querySelector('#chatLog .dots')", 15000));
  check("sent and answered: solid bubble, no receipt left", await until("!document.querySelector('#chatLog .rcpt') && !document.querySelector('#chatLog .bub.me.hold') && getComputedStyle(document.querySelector('#chatLog .bub.me')).borderTopStyle === 'solid'", 3000));
  check("thread appears in the list", await until("document.querySelectorAll('#threadList .thread').length >= 1", 5000));
  check("Send stays at the right edge of the composer", await js("(() => { const b = document.querySelector('#sendChat').getBoundingClientRect(), c = document.querySelector('#composer .cbar').getBoundingClientRect(); return Math.abs(b.right - c.right) < 2; })()"));
  await js("document.querySelector('#newChat').click()");
  await js("document.querySelector('#threadList .thread').click()");
  check("opening a thread restores it", await until("document.querySelectorAll('#chatLog .bub').length >= 2", 5000));
  // Qwen still starting: the message is held, then goes to TACC (this server has no session, so TACC says "not connected")
  await js("document.querySelector('#newChat').click()");
  await js("QW = {phase: 'starting'}");
  await js(`(() => { const t = document.querySelector('#chatInput'); t.value = 'wait for qwen'; t.dispatchEvent(new Event('input', {bubbles: true})); })()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await sleep(1500);
  const held = await js("!!document.querySelector('#chatLog .bub.bot .dots') && !document.querySelector('#chatLog .bub.bot .md')");
  const heldLook = await js("(() => { const b = document.querySelector('#chatLog .bub.me'), r = document.querySelector('#chatLog .rcpt'); return !!(b && b.classList.contains('hold') && getComputedStyle(b).borderTopStyle === 'dashed' && r && /On hold · waiting for Qwen 27B on TACC to start/.test(r.textContent)); })()");
  await js("QW = {phase: 'ready', remaining: 600}");
  const routed = await until("/not connected/.test((document.querySelector('#chatLog .bub.bot .bad') || {}).textContent || '')", 5000);
  check("Ask holds the message while Qwen starts, then sends it to TACC", held && routed, held ? (routed ? "" : "not routed to TACC") : "answered before Qwen was ready");
  const notSent = await until("document.querySelector('#chatLog .bub.me.fail') && /Not sent/.test(document.querySelector('#chatLog .rcpt.fail').textContent)", 3000);
  check("on hold: dashed bubble and receipt while waiting; Not sent when it never went out", heldLook && notSent, JSON.stringify({heldLook, notSent}));
  await js("QW = {phase: 'idle'}");
  // a failed turn is kept in the thread: reopening it shows why there was no reply
  const failedSid = await js("chatId");
  await js("document.querySelector('#newChat').click()");
  await until(`document.querySelector('#threadList .thread[data-sid="${failedSid}"]')`, 3000);
  await js(`document.querySelector('#threadList .thread[data-sid="${failedSid}"]').click()`);
  check("reopened thread shows why a turn got no reply", await until("/Not sent: .*not connected/.test((document.querySelector('#chatLog .bub.bot .bad') || {}).textContent || '') && document.querySelector('#chatLog .bub.me.fail') && /Not sent/.test((document.querySelector('#chatLog .rcpt.fail') || {}).textContent || '')", 3000));
  // collapsing the thread list leaves an expand control where it was; the Threads chip shows the state
  await js("document.querySelector('#newChat').click()"); await js("document.querySelector('#threadsHide').click()");
  const railShown = await until("document.querySelector('#chatWrap').classList.contains('collapsed') && document.querySelector('#threadsShow').offsetParent && document.querySelector('#threadsBtn').getAttribute('aria-pressed') === 'false'", 2000);
  await js("document.querySelector('#threadsShow').click()");
  const railBack = await until("!document.querySelector('#chatWrap').classList.contains('collapsed') && document.querySelector('#threadList').offsetParent && document.querySelector('#threadsBtn').getAttribute('aria-pressed') === 'true'", 2000);
  check("collapsed threads keep an expand control that restores them", railShown && railBack, railShown ? (railBack ? "" : "did not expand") : "no expand control after collapsing");
  check("wide screens: no Threads button under the composer (the sidebar has its own toggle)", await js("getComputedStyle(document.querySelector('#threadsBtn')).display === 'none'"));
  // typing dots: folded by default; a tap opens the live panel (stage, elapsed); the choice is remembered; the reply replaces it
  const typeMsg = async text => { await js(`(() => { const t = document.querySelector('#chatInput'); t.focus(); t.value = ${JSON.stringify(text)}; t.dispatchEvent(new Event('input', {bubbles: true})); })()`); await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }); };
  await js("localStorage.removeItem('fp.chat.live')"); await js("document.querySelector('#newChat').click()"); await js("QW = {phase: 'starting'}");
  await typeMsg("watch the reply");
  const folded = await until("document.querySelector('#chatLog .bub.bot .typing[aria-expanded=\"false\"]') && document.querySelector('#chatLog .bub.bot .peek').hidden", 2000);
  await js("document.querySelector('#chatLog .bub.bot .typing').click()");
  const opened = await until("!document.querySelector('#chatLog .bub.bot .peek').hidden && /Waiting for Qwen/.test(document.querySelector('#chatLog .bub.bot .lstat').textContent) && localStorage.getItem('fp.chat.live') === '1'", 2000);
  const ticking = await until("/\\d+ s/.test(document.querySelector('#chatLog .bub.bot .lstat .el').textContent)", 2500);
  await js("QW = {phase: 'ready', remaining: 600}");
  const settled = await until("!document.querySelector('#chatLog .bub.bot .typing') && !document.querySelector('#chatLog .bub.bot .lstat') && /not connected/.test(document.querySelector('#chatLog .bub.bot').textContent)", 5000);
  await js("QW = {phase: 'idle'}"); await typeMsg("how do I start?");
  const streamed = await until("document.querySelectorAll('#chatLog .bub.bot').length === 2 && document.querySelector('#chatLog .bub.bot:last-child .peek.done .md') && !document.querySelector('#chatLog .typing')", 8000);
  check("typing dots open a live panel; the reply replaces it", folded && opened && ticking && settled && streamed, JSON.stringify({folded, opened, ticking, settled, streamed}));
  await js("localStorage.removeItem('fp.chat.live')");
  // the open panel follows the TACC stages and the text as it streams (synthetic events through a stubbed EventSource)
  await js("localStorage.setItem('fp.chat.live', '1')"); await js("document.querySelector('#newChat').click()");
  await js(`(() => { window.__ES = window.EventSource; window.__seen = []; window.__me = [];
    const evs = [{type: 'chat_stage', stage: 'uploading'}, {type: 'chat_stage', stage: 'queued', job: '30120', reason: 'QOSMaxJobsPerUserLimit'},
      {type: 'chat_stage', stage: 'running', job: '30120', node: '10-0-3-11'}, {type: 'chat_delta', text: 'Replay '},
      {type: 'chat_delta', text: 'runs solve.py again.'}, {type: 'chat_done', answer: 'Replay runs solve.py again.', turns: 1}];
    window.EventSource = class { constructor(){ let i = 0; const next = () => { if (i >= evs.length) return; this.onmessage && this.onmessage({data: JSON.stringify(evs[i++])});
        requestAnimationFrame(() => { const b = [...document.querySelectorAll('#chatLog .bub.bot')].pop(), ls = b && b.querySelector('.lstat');
          const me = [...document.querySelectorAll('#chatLog .bub.me')].pop(), nx = me && me.nextElementSibling, rc = nx && nx.classList.contains('rcpt') ? nx.textContent : '-';
          window.__seen.push((ls ? ls.textContent : '') + ' | ' + ((b && b.querySelector('.md')) ? b.querySelector('.md').textContent : ''));
          window.__me.push((me && me.classList.contains('hold') ? 'dashed' : 'solid') + ' | ' + rc); setTimeout(next, 60); }); };
      setTimeout(next, 60); } close(){} };
    QW = {phase: 'ready', remaining: 600}; })()`);
  await typeMsg("what does replay check?");
  const seen = (await until("window.__seen.length === 6", 6000)) ? await js("window.__seen") : await js("window.__seen");
  await js("window.EventSource = window.__ES; QW = {phase: 'idle'}; localStorage.removeItem('fp.chat.live')");
  const want = [/^Uploading to TACC/, /^Queued on TACC · job 30120 · your two-job limit is in use/, /^Running on 10-0-3-11 · job 30120/, /^Writing.*\| Replay $/, /^Writing.*\| Replay runs solve\.py again\.$/, /^ \| Replay runs solve\.py again\.$/];
  check("open panel follows TACC stages and streams the reply", seen.length === 6 && want.every((re, i) => re.test(seen[i])), JSON.stringify(seen));
  const meSeen = await js("window.__me");
  const meWant = [/^dashed \| On hold · uploading to TACC$/, /^solid \| On hold · queued on TACC · job 30120 · your two-job limit is in use$/, /^solid \| On hold · running on 10-0-3-11/, /^solid \| On hold · replying$/, /^solid \| On hold · replying$/, /^solid \| -$/];
  check("my bubble: dashed until the job is queued, then solid; the hold receipt lasts until the reply", meSeen.length === 6 && meWant.every((re, i) => re.test(meSeen[i])), JSON.stringify(meSeen));
  // flags the tutor states come back checked: a wrong one triggers a retry that clears the draft; the final reply marks each flag
  await js("document.querySelector('#newChat').click()");
  await js(`(() => { window.__ES = window.EventSource; window.__chk = [];
    const evs = [{type: 'chat_delta', text: 'It is picoCTF{made_up}.'}, {type: 'chat_check', flags: [{flag: 'picoCTF{made_up}', status: 'wrong'}], retry: true},
      {type: 'chat_delta', text: 'The file says picoCTF{real_42}.'},
      {type: 'chat_done', answer: 'The file says picoCTF{real_42}; not picoCTF{made_up}.', turns: 1,
       flag_checks: [{flag: 'picoCTF{real_42}', status: 'correct'}, {flag: 'picoCTF{made_up}', status: 'wrong'}]}];
    window.EventSource = class { constructor(){ let i = 0; const next = () => { if (i >= evs.length) return; this.onmessage && this.onmessage({data: JSON.stringify(evs[i++])});
        requestAnimationFrame(() => { const b = [...document.querySelectorAll('#chatLog .bub.bot')].pop(); window.__chk.push(b ? ((b.querySelector('.lstat') || {}).textContent || '') + ' | ' + ((b.querySelector('.md') || {}).textContent || '') : ''); setTimeout(next, 60); }); };
      setTimeout(next, 60); } close(){} };
    QW = {phase: 'idle'}; })()`);
  await typeMsg("what is the flag?");
  await until("window.__chk.length === 4", 6000);
  const chk = await js("window.__chk"), marks = await js("[...document.querySelectorAll('#chatLog .bub.bot:last-child .fchk')].map(e => e.className + '=' + e.textContent + '|' + ((e.nextElementSibling && e.nextElementSibling.classList.contains('fbadge')) ? e.nextElementSibling.textContent : ''))");
  await js("window.EventSource = window.__ES");
  check("tutor flags: a wrong one is withdrawn and retried; the reply marks correct and wrong flags",
    chk.length === 4 && /\| It is picoCTF\{made_up\}/.test(chk[0]) && /did not check out.* \| $/i.test(chk[1]) && /\| The file says picoCTF\{real_42\}\.$/.test(chk[2])
      && JSON.stringify(marks) === JSON.stringify(["fchk ok=picoCTF{real_42}|✓ correct flag", "fchk bad=picoCTF{made_up}|✗ wrong flag"]),
    JSON.stringify({chk, marks}));
  await js("document.querySelector('#newChat').click()");
  await js(`(() => { window.__ES = window.EventSource;
    window.EventSource = class { constructor(){ setTimeout(() => this.onmessage && this.onmessage({data: JSON.stringify({type: 'chat_done', answer: 'Done.', turns: 1})}), 700); } close(){} }; })()`);
  await typeMsg("answer while I look at Runs");
  const ringOn = await js("document.querySelector('.navbtn[data-page=\"chat\"]').classList.contains('working') && document.querySelector('#brand').classList.contains('working')");
  await js("document.querySelector('.navbtn[data-page=\"history\"]').click()");
  const dotOn = await until("document.querySelector('.navbtn[data-page=\"chat\"]').classList.contains('note') && !document.querySelector('.navbtn.working') && !document.querySelector('#brand').classList.contains('working')", 3000);
  await js("window.EventSource = window.__ES; document.querySelector('.navbtn[data-page=\"chat\"]').click()");
  const dotOff = await until("!document.querySelector('.navbtn[data-page=\"chat\"]').classList.contains('note')", 1000);
  check("rail: a reply running behind another page rings its item, leaves a dot when done, opening Ask clears it", ringOn && dotOn && dotOff, JSON.stringify({ringOn, dotOn, dotOff}));
  // who answers is chosen per conversation: a menu on the chip; a message held for Qwen can go to another model at once
  await js("document.querySelector('#newChat').click(); localStorage.removeItem('fp.ask.pick'); localStorage.removeItem('fp.ask.threads'); useThreadPick(newPick())");
  await sleep(300);                                   // let the page's own focus-the-input frame pass first
  await js("document.querySelector('#qwenChipAsk').click()");
  const menu = await until("document.querySelector('#modelMenu') && document.querySelector('#qwenChipAsk').getAttribute('aria-expanded') === 'true'", 2000);
  const mrows = await js("[...document.querySelectorAll('#modelMenu button .nm')].map(x => x.textContent)");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  const menuClosed = await until("!document.querySelector('#modelMenu') && document.activeElement === document.querySelector('#qwenChipAsk')", 1500);
  await js("QW = {phase: 'starting'}");
  await typeMsg("held, then switched");
  const heldNow = await until("document.querySelector('#chatLog .rcpt .now')", 2000);
  const nowText = heldNow ? await js("document.querySelector('#chatLog .rcpt .now').textContent") : "";
  if (heldNow) await js("document.querySelector('#chatLog .rcpt .now').click()");
  const released = await until("document.querySelector('#chatLog .bub.bot .md') && /Demo model/.test((document.querySelector('#chatLog .bub.bot .who') || {}).textContent || '') && /Demo model/.test(document.querySelector('#qwenChipAsk').textContent)", 8000);
  await js("QW = {phase: 'idle'}");
  const sidA = await js("chatId");
  await js("document.querySelector('#newChat').click()"); await sleep(300);
  await js(`(() => { MODELS = ['mock', 'gemini-flash-lite-latest']; window.__F = window.fetch; window.__sent = [];
    window.fetch = (u, o) => { if (u === '/api/chat' && o && o.method === 'POST'){ window.__sent.push(JSON.parse(o.body)); return Promise.resolve(new Response(JSON.stringify({session_id: 'bbbbbbbbbbbb'}), {headers: {'Content-Type': 'application/json'}})); } return window.__F(u, o); };
    window.__ES = window.EventSource; window.EventSource = class { constructor(){ setTimeout(() => this.onmessage && this.onmessage({data: JSON.stringify({type: 'chat_done', answer: 'Hi.', turns: 1, model: 'gemini-flash-lite-latest'})}), 50); } close(){} }; })()`);
  await js("document.querySelector('#modelMenu') || document.querySelector('#qwenChipAsk').click()"); await until("document.querySelector('#modelMenu')", 1500);
  await js("([...document.querySelectorAll('#modelMenu button')].find(b => /Gemini Flash-Lite/.test(b.textContent)) || {click(){}}).click()");
  const chipG = await js("document.querySelector('#qwenChipAsk .tn').textContent");
  await typeMsg("hello gemini");
  await until("window.__sent.length === 1 && document.querySelector('#chatLog .bub.bot .who')", 3000);
  const sentModel = await js("(window.__sent[0] || {}).model");
  await js("window.fetch = window.__F; window.EventSource = window.__ES");
  const hasA = !!sidA && await until(`document.querySelector('#threadList .thread[data-sid="${sidA}"]')`, 3000);
  if (hasA) await js(`document.querySelector('#threadList .thread[data-sid="${sidA}"]').click()`);
  const chipA = hasA && await until("/Demo model/.test(document.querySelector('#qwenChipAsk .tn').textContent)", 3000);
  const chipB = await js("(useThreadPick(pickForThread('bbbbbbbbbbbb')), document.querySelector('#qwenChipAsk .tn').textContent)");
  await js("localStorage.removeItem('fp.ask.pick'); localStorage.removeItem('fp.ask.threads'); MODELS = ['mock']; useThreadPick(newPick())");
  check("Ask: each conversation keeps its own model; a held message can go to another model at once",
    menu && mrows.includes("Demo model") && mrows.includes("Qwen 27B on TACC") && menuClosed && /^Use Demo model now$/.test(nowText) && released
      && chipG === "Gemini Flash-Lite" && sentModel === "gemini-flash-lite-latest" && chipA && chipB === "Gemini Flash-Lite",
    JSON.stringify({menu, mrows, menuClosed, nowText, released, chipG, sentModel, chipA, chipB}));
  check("Ask composer: one focus frame (no outline inside it)", await js("(() => { const t = document.querySelector('#chatInput'); t.focus(); return getComputedStyle(t).outlineStyle === 'none'; })()"));
  await noCJK("ask");
  const smallAsk = await js(SMALL); check("no small text on Ask", !smallAsk.length, smallAsk.join(" | "));

  // the logo goes home: Solve's landing even when a challenge is open
  await js("document.querySelector('.navbtn[data-page=\"solve\"]').click()");
  const hadWork = await js("!document.querySelector('#work').hidden");
  await js("document.querySelector('#brand').click()");
  check("logo returns to the landing", hadWork && await until("document.querySelector('.page.on').dataset.page === 'solve' && !document.querySelector('#landing').hidden && document.activeElement !== document.querySelector('#brand')", 2000), "work view open before: " + hadWork);

  // 10. narrow viewport + reduced motion
  await vp(390, 844); await nav(base + "/#/history"); await sleep(300);
  check("narrow: no horizontal overflow", await js("document.documentElement.scrollWidth <= innerWidth + 1"));
  await js("localStorage.setItem('fp.threads.hidden', '1')"); await nav(base + "/#/chat"); await sleep(200);
  const askW = await js("Math.round(document.querySelector('#chatCol').getBoundingClientRect().width)");
  const phoneThreads = await js("(() => { const c = document.querySelector('#threadsBtn'); if (getComputedStyle(c).display === 'none') return 'hidden'; const a1 = c.querySelector('.ar').textContent; c.click(); const open = document.querySelector('#chatWrap').classList.contains('showthreads'); const a2 = c.querySelector('.ar').textContent; c.click(); return a1 + a2 + (open ? '|open' : '|closed') + '|' + c.querySelector('.n').textContent; })()");
  check("phones: the composer's Threads button opens the list (no sidebar there)", /^›‹\|open\|[1-9]\d*$/.test(phoneThreads), phoneThreads);
  check("narrow: a remembered collapse does not squeeze Ask", askW >= 300 && await js("document.documentElement.scrollWidth <= innerWidth + 1"), askW + " px");
  await js("localStorage.removeItem('fp.threads.hidden')");
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] }); await vp(1280, 800);
  await nav(base + "/#/solve"); await js("document.querySelector('.navbtn[data-page=\"lab\"]').click()"); await sleep(200);
  check("reduced motion: page switch still works", await js("document.querySelector('.page.on').dataset.page === 'lab'"));
} catch (e) {
  check("ui_check aborted", false, String(e && e.message || e));
} finally {
  check("no page errors", !pageErrors.length, pageErrors.slice(0, 3).join(" | "));
  try { ws.close(); } catch {}
  chrome.kill("SIGKILL"); srv.kill("SIGTERM");
  await sleep(200); try { rmSync(work, { recursive: true, force: true }); } catch {}
  const bad = results.filter(r => !r.ok);
  console.log(`\nui_check: ${results.length - bad.length}/${results.length} passed`);
  process.exit(bad.length ? 1 : 0);
}
