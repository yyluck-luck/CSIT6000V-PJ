// Click-everything smoke test: every visible control on every page and Lab tab is clicked once; each click must do
// something (DOM change, request, navigation or focus change) and must not throw. Its own server (mock model, bundled demo
// tasks, throw-away run directory) and headless Chrome over the DevTools protocol, like ui_check.mjs. No npm packages.
//   node scripts/click_all.mjs          # exit 0 = no errors and no dead controls (or SKIP without Chrome / Node < 22)
// Left out on purpose: anything that would start TACC jobs, delete, stop or disconnect, or reach the internet.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const [major] = process.versions.node.split(".").map(Number);
if (major < 22 || typeof WebSocket === "undefined") { console.log(`SKIP click_all: needs Node >= 22 (found ${process.versions.node})`); process.exit(0); }
const CHROME = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(p => p && existsSync(p));
if (!CHROME) { console.log("SKIP click_all: no Chrome/Chromium found (set CHROME=...)"); process.exit(0); }

const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
const work = mkdtempSync(join(tmpdir(), "fp-click-"));
const port = await freePort();
const env = { ...process.env, FLAGPILOT_RUNS: join(work, "runs"), FLAGPILOT_TASKS: join(ROOT, "demo-tasks"), FLAGPILOT_MEMORY: join(work, "mem"), PYTHONDONTWRITEBYTECODE: "1" };
const srv = spawn(process.env.PYTHON || "python3", ["-m", "flagpilot", "web", "--host", "127.0.0.1", "--port", String(port), "--model", "mock"], { cwd: ROOT, env, stdio: "ignore" });
const base = `http://127.0.0.1:${port}`;
for (let i = 0; i < 100; i++) { try { if ((await fetch(base + "/api/tasks")).ok) break; } catch {} await sleep(100); }
const cport = await freePort();
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${cport}`, `--user-data-dir=${join(work, "chrome")}`, "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--window-size=1440,900", "about:blank"], { stdio: "ignore" });
let wsUrl = null;
for (let i = 0; i < 100 && !wsUrl; i++) { try { const l = await (await fetch(`http://127.0.0.1:${cport}/json/list`)).json(); wsUrl = (l.find(t => t.type === "page") || {}).webSocketDebuggerUrl; } catch {} if (!wsUrl) await sleep(100); }
const ws = new WebSocket(wsUrl);
await new Promise(r => (ws.onopen = r));
let seq = 0; const pending = new Map(); let errs = [];
ws.onmessage = m => { const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
  if (d.method === "Runtime.exceptionThrown") errs.push((d.params.exceptionDetails.exception || {}).description || d.params.exceptionDetails.text);
  else if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errs.push("console.error: " + d.params.args.map(a => a.value ?? a.description).join(" "));
  else if (d.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: false }); };   // confirm() → Cancel
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, d => d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result)); ws.send(JSON.stringify({ id, method, params })); });
const js = async expr => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception || {}).description || expr); return r.result.value; };
const until = async (expr, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await js(`!!(${expr})`)) return true; } catch {} await sleep(40); } return false; };

// what never gets clicked (TACC jobs, deletes, stops, the internet) and how a control is named in the report
const SKIP = String.raw`#runTacc,#qwenChip,#taccStart,#taccStop,#impUrlGo,[data-import],.del,[data-stop],#ctlStop,#awStop,a[href^="http"],a[download],[data-close]`;
const SKIP_TEXT = /delete|remove|disconnect|stop|clear all|reset/i;
const PROBE = String.raw`(() => {
  window.__clk = {mut: 0, req: 0};
  if (!window.__clkObs){ window.__clkObs = new MutationObserver(l => { window.__clk.mut += l.length; });
    window.__clkObs.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
    const F = window.fetch; window.fetch = (...a) => { window.__clk.req++; return F(...a); };
    const ES = window.EventSource; window.EventSource = class extends ES { constructor(...a){ super(...a); window.__clk.req++; } }; }
})()`;
const LIST = (scope) => String.raw`(() => { const skip = ${JSON.stringify(SKIP)}, out = []; document.querySelectorAll('[data-clk]').forEach(e => e.removeAttribute('data-clk'));
  const els = [...document.querySelectorAll('${scope} button, ${scope} [role=button], ${scope} [role=link], ${scope} a[href], ${scope} label.chip, ${scope} summary, ${scope} .row[data-id], ${scope} .exp[data-id], ${scope} .thread[data-sid]')];
  for (const el of els){ const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || el.disabled || el.closest('[hidden],[inert]') || el.matches(skip) || el.closest(skip)) continue;
    if (el.matches('label.chip') && el.querySelector('input[type=radio]:checked')) continue;      // an already chosen option has nothing to do
    const name = (el.id ? '#' + el.id + ' ' : '') + ((el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40) || el.className);
    if (${SKIP_TEXT}.test(name)) continue;
    el.dataset.clk = out.length; out.push(name); }
  return out; })()`;

const report = [], seen = new Set();
async function reset(state){
  await js(`(() => { try { if (!document.querySelector('#cmdk').hidden) cmdkClose(); } catch(_){}
    try { closeModelMenu(); } catch(_){} try { if (!document.querySelector('#sheetWrap').hidden) sheetShut(); } catch(_){}
    try { if (inspOpen()) toggleInsp(false); } catch(_){} document.querySelectorAll('.burst').forEach(b => b.remove()); })()`);
  await state();
  await sleep(150);
}
async function sweep(label, scope, state){
  await reset(state);
  const names = await js(LIST(scope));
  for (let i = 0; i < names.length; i++){
    const key = label + ' · ' + names[i]; if (seen.has(key)) continue; seen.add(key);
    await reset(state);
    const here = await js(LIST(scope));                          // the page may have re-rendered: find it again by name
    const j = here.indexOf(names[i]); if (j < 0) continue;
    errs = [];
    await js(PROBE);
    const before = await js(`[location.href, (document.activeElement || {}).id || '', document.querySelector('.page.on').dataset.page]`);
    await js(`(() => { const el = document.querySelector('[data-clk="${j}"]'); el.scrollIntoView({block: 'center'}); el.click(); })()`);
    await sleep(350);
    const after = await js(`[location.href, (document.activeElement || {}).id || '', document.querySelector('.page.on').dataset.page, window.__clk.mut, window.__clk.req]`);
    const did = after[3] > 0 || after[4] > 0 || after[0] !== before[0] || after[1] !== before[1] || after[2] !== before[2];
    const res = errs.length ? "ERROR" : did ? "ok" : "DEAD";
    report.push({ where: label, control: names[i], res, detail: errs.slice(0, 2).join(" | ") });
    if (res !== "ok") console.log(`${res}  ${label} · ${names[i]}${errs.length ? "  — " + errs[0] : ""}`);
  }
}

try {
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: base + "/#/solve" }); await until("document.readyState === 'complete' && TASKS.length", 10000); await sleep(800);
  const task = await js("TASKS[0].name");
  const page = (p, extra = "") => async () => { await js(`(() => { if (document.querySelector('.page.on').dataset.page !== '${p}') go('${p}'); ${extra} })()`); };
  await sweep("rail", "#rail", page("history"));
  await sweep("landing", "#landing", async () => { await js("(() => { go('solve'); if (sel) { run = null; pick(null); } })()"); });
  await sweep("solve", "#work", async () => { await js(`(() => { go('solve'); if (sel !== ${JSON.stringify(task)}) pick(taskByName(${JSON.stringify(task)})); })()`); });
  await sweep("runs", "#pHistory", page("history"));
  for (const tab of ["results", "compare", "batch", "diff", "probe"]) await sweep("lab/" + tab, `#pLab`, page("lab", `if (labTab !== '${tab}') labShow('${tab}');`));
  await sweep("upload", "#pUpload", page("upload"));
  await sweep("ask", "#pChat", page("chat"));
} catch (e) {
  report.push({ where: "click_all", control: "aborted", res: "ERROR", detail: String(e && e.message || e) });
  console.log("ERROR click_all aborted — " + (e && e.message || e));
} finally {
  try { ws.close(); } catch {}
  chrome.kill("SIGKILL"); srv.kill("SIGTERM");
  await sleep(200); try { rmSync(work, { recursive: true, force: true }); } catch {}
  const bad = report.filter(r => r.res !== "ok");
  console.log(`\nclick_all: ${report.length - bad.length}/${report.length} controls responded without errors`);
  process.exit(bad.length ? 1 : 0);
}
