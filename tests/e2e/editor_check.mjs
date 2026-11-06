// 在无头 Chrome 中检查运行中的 admin：登录、CSP 违规、官方按钮已移除、删除平台后字段保留。
// 只修改浏览器里的编辑器内容，不调用保存接口。
//
// 用法：node tests/e2e/editor_check.mjs <admin-url> <token-file> [screenshot.png]
// 依赖：Node 22+（全局 WebSocket），本机安装 Google Chrome（可用 CHROME 环境变量指定路径）。
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [adminUrl = 'http://127.0.0.1:8081/', tokenFile, screenshot] = process.argv.slice(2);
if (!tokenFile) {
  console.error('usage: node tests/e2e/editor_check.mjs <admin-url> <token-file> [screenshot.png]');
  process.exit(2);
}
const token = readFileSync(tokenFile, 'utf8').trim();
const chromePath = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), 'tr-admin-e2e-'));
const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--user-data-dir=${profile}`,
  `--remote-debugging-port=${port}`, '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const check = (name, ok, detail = '') => results.push({ name, ok, detail });
let ws;

async function cleanup() {
  try { ws?.close(); } catch {}
  const exited = new Promise((resolve) => chrome.once('exit', resolve));
  chrome.kill();
  await Promise.race([exited, sleep(5000)]);
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
const watchdog = setTimeout(async () => { console.error('timeout'); await cleanup(); process.exit(2); }, 60000);

try {
  let targets;
  for (let i = 0; i < 50 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(200); }
  }
  ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve) => ws.addEventListener('open', resolve));
  let nextId = 0;
  const pending = new Map();
  const consoleErrors = [];
  const dialogs = [];
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.method === 'Page.javascriptDialogOpening') {
      dialogs.push(msg.params.message);
      ws.send(JSON.stringify({ id: 100000 + dialogs.length, method: 'Page.handleJavaScriptDialog', params: { accept: true } }));
    }
    if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') consoleErrors.push(msg.params.entry.text);
    if (msg.method === 'Runtime.exceptionThrown') consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    if (pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params = {}) => new Promise((resolve) => {
    const id = ++nextId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.result?.value;

  await send('Page.enable');
  await send('Log.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: adminUrl });
  await sleep(1500);
  await evaluate(`(() => { document.getElementById('token').value = ${JSON.stringify(token)}; document.querySelector('form').submit(); })()`);
  await sleep(6000);

  check('logged in, editor loaded', await evaluate(`!!document.getElementById('yaml-editor')`));
  check('server config loaded', await evaluate(`document.getElementById('tr-server-summary')?.textContent.includes('服务器已连接')`));
  check('official load/copy buttons removed', await evaluate(`!document.querySelector('button[onclick^="openLoadConfigModal"], button[onclick^="copyResult"]')`));
  check('logo kept visible', await evaluate(`(() => { const a = document.querySelector('body > nav a'); return !!a && a.offsetParent !== null; })()`));
  check('report link points to web server', await evaluate(`(() => { const a = document.getElementById('tr-report-link'); return a && !a.hidden && !a.getAttribute('href').startsWith('/') ? a.href : false; })()`));
  check('run button hidden when disabled', await evaluate(`fetch('/api/state').then(r => r.json()).then(s => s.features.run || document.getElementById('tr-run-now').hidden)`));
  check('no external resources loaded', await evaluate(`performance.getEntriesByType('resource').every(e => e.name.startsWith(location.origin))`));
  check('font awesome icons render', await evaluate(`document.fonts.check('900 16px "Font Awesome 6 Free"')`));

  const before = await evaluate(`document.getElementById('yaml-editor').value`);
  const platformsBefore = await evaluate(`document.querySelectorAll('#platforms-list .platform-item').length`);
  const deleted = await evaluate(`(() => { const b = document.querySelector('#platforms-list button[onclick^="removePlatform"]'); if (!b) return false; b.click(); return true; })()`);
  await sleep(800);
  const after = await evaluate(`document.getElementById('yaml-editor').value`);
  const count = (text, re) => (text.match(re) || []).length;
  const domainsBefore = count(before, /expected_domain:/g);
  const domainsAfter = count(after, /expected_domain:/g);
  check('platform deleted in editor', deleted && dialogs.length === 1, dialogs.join(' | '));
  check('expected_domain kept for remaining platforms', domainsBefore === 0 || domainsAfter === domainsBefore - 1, `${domainsBefore} -> ${domainsAfter}`);
  check('YAML still parses', await evaluate(`(() => { try { jsyaml.load(document.getElementById('yaml-editor').value); return true; } catch (e) { return e.message; } })()`) === true);
  check('editor platform count updated', await evaluate(`document.querySelectorAll('#platforms-list .platform-item').length`) === platformsBefore - 1);

  if (screenshot) {
    await send('Page.navigate', { url: adminUrl });
    await sleep(4000);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(screenshot, Buffer.from(shot.result.data, 'base64'));
  }
  check('no console errors or CSP violations', consoleErrors.length === 0, consoleErrors.slice(0, 5).join(' | '));
} finally {
  clearTimeout(watchdog);
  await cleanup();
}

for (const { name, ok, detail } of results) console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail && (!ok || name.includes('expected_domain')) ? `  (${detail})` : ''}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
