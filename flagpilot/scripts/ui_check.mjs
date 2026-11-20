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

await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable"); await vp(1440, 900);
await send("Browser.grantPermissions", { permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"], origin: base }).catch(() => {});
try {
  // 1. landing
  await nav(base + "/#/solve");
  check("landing renders", await until("document.querySelectorAll('#tasks .row').length > 0 && !document.querySelector('#landing').hidden"));
  const w0 = await js("document.querySelector('#flip').textContent");
  check("layout text flip cycles", await until(`document.querySelector('#flip').textContent.trim() !== ${JSON.stringify(w0.trim())}`, 4000));
  await noCJK("landing");

  // 2. page switch latency: the new page must be committed inside the click handler
  const lat = await js(`(async () => { const out = []; for (const p of ['history','lab','upload','chat','solve','history','solve']) {
      await new Promise(r => setTimeout(r, 120)); const t0 = performance.now(); document.querySelector('.navbtn[data-page="' + p + '"]').click();
      const ms = performance.now() - t0; out.push([p, ms, document.querySelector('.page[data-page="' + p + '"]').classList.contains('on')]); } return out; })()`);
  const worst = Math.max(...lat.map(x => x[1]));
  check("page switch commits in the click frame", lat.every(x => x[2]) && worst < 50, `worst handler ${worst.toFixed(1)} ms; ` + lat.map(x => `${x[0]} ${x[1].toFixed(1)}`).join(", "));

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
  await noCJK("solve");

  // 6. runs + replay
  await js("document.querySelector('.navbtn[data-page=\"history\"]').click()");
  check("runs list and stats render", await until("document.querySelectorAll('#histList .row').length >= 1 && /\\d/.test(document.querySelector('#hs-n').textContent)", 5000));
  await js("document.querySelector('#histList .row').click()");
  check("replay from Runs reaches a verdict", await until("document.querySelector('.page.on').dataset.page === 'solve' && document.querySelector('#verdict .verdict:not(.run)')", 10000));

  // 7. lab sweep + compare slider
  await js("document.querySelector('.navbtn[data-page=\"lab\"]').click()");
  await js("document.querySelector('#sweep').click()");
  check("four-tier sweep finishes", await until("document.querySelector('#cmpSlider .cmp')", 40000));
  const moved = await js(`(() => { const b = document.querySelector('#cmpSlider .cmp'), r = b.getBoundingClientRect();
      const ev = (t, x) => b.dispatchEvent(new PointerEvent(t, {clientX: x, clientY: r.top + 40, pointerId: 1, bubbles: true}));
      ev('pointerdown', r.left + r.width * .5); ev('pointermove', r.left + r.width * .25); ev('pointerup', r.left + r.width * .25); return b.style.getPropertyValue('--x'); })()`);
  check("compare slider follows the drag", parseFloat(moved) < 40, "x=" + moved);
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
  check("thread appears in the list", await until("document.querySelectorAll('#threadList .thread').length >= 1", 5000));
  await js("document.querySelector('#newChat').click()");
  await js("document.querySelector('#threadList .thread').click()");
  check("opening a thread restores it", await until("document.querySelectorAll('#chatLog .bub').length >= 2", 5000));
  await noCJK("ask");

  // 10. narrow viewport + reduced motion
  await vp(390, 844); await nav(base + "/#/history"); await sleep(300);
  check("narrow: no horizontal overflow", await js("document.documentElement.scrollWidth <= innerWidth + 1"));
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
