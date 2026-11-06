(() => {
  const EDITORS = {
    config: { id: 'yaml-editor', apiKey: 'config', label: 'config.yaml' },
    frequency: { id: 'frequency-editor', apiKey: 'frequency', label: 'frequency_words.txt' },
    timeline: { id: 'timeline-editor', apiKey: 'timeline', label: 'timeline.yaml' },
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function toast(message, type = 'info') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type);
      return;
    }
    console.log(`[${type}] ${message}`);
  }

  async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { success: false, error: text || response.statusText };
    }
    if (!response.ok || payload.success === false) {
      throw new Error(payload.error || payload.message || response.statusText);
    }
    return payload;
  }

  function currentEditorKey() {
    const active = document.querySelector('.tab-button.active');
    if (active?.id === 'tab-frequency') return 'frequency';
    if (active?.id === 'tab-timeline') return 'timeline';
    return 'config';
  }

  function setEditorValue(key, value) {
    const spec = EDITORS[key];
    const editor = byId(spec.id);
    if (!editor) return;
    editor.value = value || '';
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    if (typeof window.updateBackdrop === 'function') {
      const backdrop = key === 'config' ? 'yaml-backdrop' : `${key}-backdrop`;
      window.updateBackdrop(spec.id, backdrop);
    }
  }

  function getEditorValue(key) {
    return byId(EDITORS[key].id)?.value || '';
  }

  async function loadServerConfig() {
    setBusy(true, '加载中...');
    try {
      const state = await api('/state');
      setEditorValue('config', state.texts?.config || '');
      setEditorValue('frequency', state.texts?.frequency || '');
      setEditorValue('timeline', state.texts?.timeline || '');
      updateStatus(state);
      applyServerFeatures(state);
      toast('已加载服务器配置', 'success');
    } catch (error) {
      toast(`加载失败：${error.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function saveKey(key) {
    const spec = EDITORS[key];
    await api(`/file/${spec.apiKey}`, {
      method: 'POST',
      body: JSON.stringify({ content: getEditorValue(key) }),
    });
    return spec.label;
  }

  async function saveCurrentToServer() {
    const key = currentEditorKey();
    setBusy(true, '保存中...');
    try {
      const label = await saveKey(key);
      toast(`已保存 ${label} 到服务器`, 'success');
      refreshStatusQuietly();
    } catch (error) {
      toast(`保存失败：${error.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function saveAllToServer() {
    setBusy(true, '保存全部...');
    try {
      const saved = [];
      for (const key of Object.keys(EDITORS)) {
        saved.push(await saveKey(key));
      }
      toast(`已保存：${saved.join('、')}`, 'success');
      refreshStatusQuietly();
    } catch (error) {
      toast(`保存失败：${error.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    setBusy(true, '启动中...');
    try {
      const result = await api('/run', { method: 'POST', body: '{}' });
      toast(result.started ? '已触发立即运行' : '已有任务正在运行', result.started ? 'success' : 'info');
      setTimeout(refreshRunState, 1200);
    } catch (error) {
      toast(`运行失败：${error.message}`, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatusQuietly() {
    try {
      updateStatus(await api('/state'));
    } catch (error) {
      console.warn(error);
    }
  }

  async function refreshRunState() {
    try {
      const state = await api('/run');
      const target = byId('tr-server-run-state');
      if (!target) return;
      target.hidden = !state.enabled && !state.finished_at;
      target.textContent = state.running
        ? '手动运行中...'
        : state.finished_at
          ? `上次完成：${state.finished_at} / exit ${state.exit_code}`
          : '暂无手动运行';
      if (state.running) setTimeout(refreshRunState, 5000);
    } catch (error) {
      console.warn(error);
    }
  }

  function updateStatus(state) {
    const summary = byId('tr-server-summary');
    if (!summary) return;
    const reportTime = state.report?.mtime ? new Date(state.report.mtime * 1000).toLocaleString() : '暂无';
    summary.innerHTML = `
      <span class="tr-dot"></span>
      <span>服务器已连接</span>
      <span class="tr-sep"></span>
      <span>最新报告：${reportTime}</span>
      <span class="tr-sep"></span>
      <span>${(state.files || []).filter((file) => file.exists).length} 个配置文件</span>
    `;
  }

  function applyServerFeatures(state) {
    const runButton = byId('tr-run-now');
    if (runButton) runButton.hidden = !state.features?.run;
    const reportLink = byId('tr-report-link');
    if (reportLink && state.service?.web) {
      reportLink.href = state.service.web;
      reportLink.hidden = false;
    }
  }

  async function logout() {
    await fetch('/logout', { method: 'POST' });
    window.location.reload();
  }

  // 官方编辑器重写 platforms.sources 时只输出 id 和 name，会丢掉 expected_domain（域名校验）、enabled 等字段。
  // 在原函数写回后，把每个平台原有的标量字段补回对应条目。
  const PLATFORM_WRITTEN_KEYS = new Set(['id', 'name']);

  function yamlScalar(value) {
    if (typeof value === 'string') return JSON.stringify(value);
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return null;
  }

  function installPlatformFieldPreservation() {
    if (window.__trPlatformFieldPreservationInstalled) return;
    const original = window.updatePlatformsInYaml;
    if (typeof original !== 'function') return;
    window.__trPlatformFieldPreservationInstalled = true;
    window.updatePlatformsInYaml = function preservePlatformFields(platforms) {
      original.call(this, platforms);
      restorePlatformFields(platforms);
    };
  }

  function restorePlatformFields(platforms) {
    const editor = byId('yaml-editor');
    if (!editor) return;
    const extras = new Map();
    platforms.forEach((platform) => {
      const fields = Object.entries(platform || {})
        .filter(([key]) => !PLATFORM_WRITTEN_KEYS.has(key))
        .map(([key, value]) => [key, yamlScalar(value)])
        .filter(([, value]) => value !== null);
      if (fields.length) extras.set(String(platform.id), fields);
    });
    if (!extras.size) return;

    const lines = editor.value.split('\n');
    const output = [];
    let inPlatforms = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      output.push(line);
      if (/^platforms:/.test(line)) {
        inPlatforms = true;
        continue;
      }
      if (/^[A-Za-z_]+:/.test(line)) inPlatforms = false;
      if (!inPlatforms) continue;

      const match = line.match(/^(\s*)- id: "(.*)"\s*$/);
      if (!match || !extras.has(match[2])) continue;
      const fieldIndent = `${match[1]}  `;
      const existing = new Set();
      while (i + 1 < lines.length && lines[i + 1].startsWith(fieldIndent) && !lines[i + 1].trim().startsWith('-')) {
        i += 1;
        output.push(lines[i]);
        existing.add(lines[i].trim().split(':')[0]);
      }
      extras.get(match[2])
        .filter(([key]) => !existing.has(key))
        .forEach(([key, value]) => output.push(`${fieldIndent}${key}: ${value}`));
      extras.delete(match[2]);
    }

    const updated = output.join('\n');
    if (updated === editor.value) return;
    editor.value = updated;
    try {
      currentYaml = updated; // eslint-disable-line no-undef -- script.js 顶层 let，编辑器各功能从这里读取 YAML
    } catch (error) {
      console.warn('无法同步 currentYaml', error);
    }
    if (typeof window.updateBackdrop === 'function') window.updateBackdrop('yaml-editor', 'yaml-backdrop');
    if (typeof window.debounceSaveConfig === 'function') window.debounceSaveConfig();
  }

  function setBusy(isBusy, label = '') {
    document.querySelectorAll('[data-tr-server-action]').forEach((button) => {
      button.disabled = isBusy;
      if (isBusy) {
        button.dataset.originalText = button.innerHTML;
      } else if (button.dataset.originalText) {
        button.innerHTML = button.dataset.originalText;
        delete button.dataset.originalText;
      }
    });
    if (isBusy) {
      const current = document.querySelector('[data-tr-server-action].tr-primary');
      if (current) current.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-1"></i>${label}`;
    }
  }

  function injectServerBar() {
    const nav = document.querySelector('body > nav .h-14');
    if (!nav || byId('tr-server-actions')) return;

    const privacyHint = nav.querySelector('.hidden.lg\\:flex, .lg\\:flex');
    if (privacyHint) {
      privacyHint.innerHTML = '<i class="fa-solid fa-server mr-1.5 text-indigo-500"></i><span>服务器增强模式：配置会保存到当前 TrendRadar 部署</span>';
    }

    const actions = document.createElement('div');
    actions.id = 'tr-server-actions';
    actions.className = 'tr-server-actions';
    actions.innerHTML = `
      <div id="tr-server-summary" class="tr-server-summary"><span class="tr-dot tr-muted"></span><span>等待连接服务器...</span></div>
      <button data-tr-server-action class="tr-btn" id="tr-load-server"><i class="fa-solid fa-rotate mr-1"></i>加载服务器配置</button>
      <button data-tr-server-action class="tr-btn tr-primary" id="tr-save-current"><i class="fa-solid fa-floppy-disk mr-1"></i>保存当前</button>
      <button data-tr-server-action class="tr-btn" id="tr-save-all"><i class="fa-solid fa-layer-group mr-1"></i>保存全部</button>
      <button data-tr-server-action class="tr-btn" id="tr-run-now" hidden><i class="fa-solid fa-play mr-1"></i>立即运行</button>
      <a class="tr-btn" id="tr-report-link" target="_blank" rel="noopener noreferrer" hidden><i class="fa-solid fa-chart-line mr-1"></i>最新报告</a>
      <button class="tr-btn" id="tr-logout" title="退出登录"><i class="fa-solid fa-right-from-bracket mr-1"></i>退出</button>
    `;
    nav.appendChild(actions);

    byId('tr-load-server').addEventListener('click', loadServerConfig);
    byId('tr-save-current').addEventListener('click', saveCurrentToServer);
    byId('tr-save-all').addEventListener('click', saveAllToServer);
    byId('tr-run-now').addEventListener('click', runNow);
    byId('tr-logout').addEventListener('click', logout);

    const runState = document.createElement('div');
    runState.id = 'tr-server-run-state';
    runState.className = 'tr-run-state';
    document.body.appendChild(runState);
  }

  function installLinkedModuleNavigation() {
    if (window.__trLinkedModuleNavigationInstalled) return;
    const originalScrollToModuleInEditor = window.scrollToModuleInEditor;
    if (typeof originalScrollToModuleInEditor !== 'function') return;

    window.__trLinkedModuleNavigationInstalled = true;
    window.scrollToModuleInEditor = function linkedScrollToModule(modKey) {
      originalScrollToModuleInEditor.call(this, modKey);
      scrollVisualModuleIntoView(modKey);
    };
  }

  function scrollVisualModuleIntoView(modKey) {
    const panel = byId('config-panel');
    const card = byId(`module-${modKey}`);
    if (!panel || !card) return;

    if (typeof window.switchTab === 'function') {
      window.switchTab('config');
    }

    const panelRect = panel.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const targetTop = panel.scrollTop + (cardRect.top - panelRect.top) - 16;
    panel.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });

    card.classList.remove('tr-module-focus');
    void card.offsetWidth;
    card.classList.add('tr-module-focus');
    window.setTimeout(() => card.classList.remove('tr-module-focus'), 1400);
  }

  let sourceEditingUnlocked = false;

  function installSourceEditorLock() {
    if (window.__trSourceEditorLockInstalled) return;
    window.__trSourceEditorLockInstalled = true;

    injectSourceLockButton();
    Object.values(EDITORS).forEach((spec) => {
      const editor = byId(spec.id);
      if (!editor) return;
      editor.dataset.trSourceEditor = '1';
      editor.addEventListener('beforeinput', blockSourceEditWhenLocked, true);
      editor.addEventListener('paste', blockSourceEditWhenLocked, true);
      editor.addEventListener('drop', blockSourceEditWhenLocked, true);
    });
    setSourceEditorsLocked(true);
  }

  function injectSourceLockButton() {
    if (byId('tr-source-lock')) return;
    const tabBar = byId('tab-config')?.parentElement;
    if (!tabBar) return;

    const button = document.createElement('button');
    button.id = 'tr-source-lock';
    button.type = 'button';
    button.className = 'tr-source-lock locked';
    button.title = '锁定时只能通过右侧可视化界面修改配置；解锁后才能直接编辑左侧源文件';
    button.addEventListener('click', () => {
      const willLock = sourceEditingUnlocked;
      setSourceEditorsLocked(willLock);
      toast(willLock ? '源文件编辑已锁定' : '源文件编辑已解锁，请谨慎直接修改 YAML', willLock ? 'info' : 'warning');
    });

    const spacer = tabBar.querySelector('.flex-grow');
    if (spacer) tabBar.insertBefore(button, spacer);
    else tabBar.appendChild(button);
  }

  function setSourceEditorsLocked(locked) {
    sourceEditingUnlocked = !locked;
    Object.values(EDITORS).forEach((spec) => {
      const editor = byId(spec.id);
      if (!editor) return;
      editor.readOnly = locked;
      editor.setAttribute('aria-readonly', locked ? 'true' : 'false');
      editor.classList.toggle('tr-source-readonly', locked);
      editor.classList.toggle('tr-source-unlocked', !locked);
      editor.title = locked ? '源文件编辑已锁定；请使用右侧可视化界面修改，或点击上方按钮解锁' : '';
    });
    updateSourceLockButton();
  }

  function updateSourceLockButton() {
    const button = byId('tr-source-lock');
    if (!button) return;
    button.classList.toggle('locked', !sourceEditingUnlocked);
    button.classList.toggle('unlocked', sourceEditingUnlocked);
    button.innerHTML = sourceEditingUnlocked
      ? '<i class="fa-solid fa-lock-open mr-1"></i>锁定源文件'
      : '<i class="fa-solid fa-lock mr-1"></i>解锁源文件';
  }

  function blockSourceEditWhenLocked(event) {
    if (sourceEditingUnlocked || !event.isTrusted) return;
    event.preventDefault();
    toast('源文件编辑已锁定，请先点击“解锁源文件”；右侧可视化配置仍可直接修改。', 'info');
  }

  const SELECT_OPTION_LABELS = {
    current: '当前快照',
    daily: '每日汇总',
    incremental: '增量更新',
    keyword: '关键词',
    platform: '平台',
    ai: 'AI 智能筛选',
    follow_report: '跟随报告模式',
    error_on_overlap: '时间重叠时报错（推荐）',
    last_wins: '后定义优先',
    always_on: '全天运行',
    workday: '工作日',
    weekday: '工作日',
    weekend: '周末',
    morning_evening: '早晚两次',
    custom: '自定义',
    inherit: '继承',
    default: '默认配置',
    all_day: '全天',
    weekday_morning: '工作日上午',
    weekday_evening: '工作日晚上',
    weekend_morning: '周末上午',
    weekend_evening: '周末晚上',
  };

  const LABEL_REPLACEMENTS = [
    ['API Key', 'API 密钥'],
    ['API Base URL (可选)', 'API 接口地址（可选）'],
    ['最大生成 Token 数', '最大生成令牌数'],
    ['AI 分析模式', 'AI 分析模式'],
    ['filter.method=ai', '筛选方法 = AI 智能筛选'],
    ['method=keyword', '筛选方法 = 关键词'],
    ['method=ai', '筛选方法 = AI 智能筛选'],
    ['frequency_words.txt', '关键词文件 frequency_words.txt'],
    ['ai_interests.txt', 'AI 兴趣文件 ai_interests.txt'],
    ['filter_method', '筛选方法'],
    ['frequency_file', '关键词文件'],
    ['interests_file', 'AI 兴趣文件'],
    ['Day Plans', '日计划'],
    ['Week Map', '周映射'],
    ['Overlap', '冲突策略'],
  ];

  function installChineseVisualLabels() {
    if (window.__trChineseVisualLabelsInstalled) return;
    window.__trChineseVisualLabelsInstalled = true;

    let localizing = false;
    let scheduled = false;
    const scheduleLocalization = () => {
      if (localizing || scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        localizing = true;
        try {
          localizeVisualLabels(document);
        } finally {
          localizing = false;
        }
      });
    };

    localizeVisualLabels(document);
    const observer = new MutationObserver((mutations) => {
      if (localizing) return;
      if (mutations.some(m => m.addedNodes.length && !isLocalizationOnlyMutation(m))) {
        scheduleLocalization();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function isLocalizationOnlyMutation(mutation) {
    const target = mutation.target;
    return target?.nodeType === Node.ELEMENT_NODE && target.matches?.('option[data-tr-localized-value]');
  }

  function localizeVisualLabels(root) {
    localizeSelectOptions(root);
    localizeStaticText(root);
  }

  function localizeSelectOptions(root) {
    root.querySelectorAll('select option').forEach((option) => {
      const value = option.value || option.textContent.trim();
      const normalized = value || option.textContent.trim();
      const label = SELECT_OPTION_LABELS[normalized];
      if (!label) return;
      if (option.dataset.trLocalizedValue === normalized && option.textContent.trim() === label) return;
      if (option.dataset.trOriginalLabel === undefined) {
        option.dataset.trOriginalLabel = option.textContent.trim();
      }
      option.dataset.trLocalizedValue = normalized;
      if (option.textContent.trim() !== label) option.textContent = label;
      option.title = normalized;
    });
  }

  function localizeStaticText(root) {
    root.querySelectorAll('#config-panel label, #config-panel .text-xs, #timeline-panel label, #timeline-panel .text-xs, #timeline-panel .tl-section-title').forEach((node) => {
      if (node.dataset.trLocalizedText === '1') return;
      if (node.children.length && !node.matches('label')) return;
      let text = node.textContent;
      let changed = false;
      for (const [from, to] of LABEL_REPLACEMENTS) {
        if (text.includes(from)) {
          text = text.split(from).join(to);
          changed = true;
        }
      }
      if (!changed) return;
      node.textContent = text;
      node.dataset.trLocalizedText = '1';
    });
  }

  function injectStyles() {
    if (byId('tr-server-style')) return;
    const style = document.createElement('style');
    style.id = 'tr-server-style';
    style.textContent = `
      #tr-server-actions { display:flex; align-items:center; gap:8px; margin-left:12px; flex-wrap:wrap; justify-content:flex-end; }
      .tr-server-summary { display:flex; align-items:center; gap:6px; color:#475569; background:#f8fafc; border:1px solid #e2e8f0; border-radius:999px; padding:5px 10px; font-size:11px; white-space:nowrap; }
      .tr-dot { width:7px; height:7px; border-radius:999px; background:#16a34a; display:inline-block; box-shadow:0 0 0 3px rgba(22,163,74,.12); }
      .tr-dot.tr-muted { background:#94a3b8; box-shadow:none; }
      .tr-sep { width:1px; height:12px; background:#cbd5e1; display:inline-block; }
      .tr-btn { border:1px solid #dbe3ef; background:#fff; color:#334155; border-radius:8px; padding:7px 10px; font-size:12px; font-weight:700; text-decoration:none; transition:.15s ease; white-space:nowrap; }
      .tr-btn:hover { border-color:#a5b4fc; color:#3730a3; background:#eef2ff; }
      .tr-btn.tr-primary { background:#4f46e5; color:#fff; border-color:#4f46e5; box-shadow:0 6px 16px rgba(79,70,229,.2); }
      .tr-btn.tr-primary:hover { background:#4338ca; color:#fff; }
      .tr-btn:disabled { opacity:.6; cursor:not-allowed; }
      button[onclick^="openLoadConfigModal"], button[onclick^="copyResult"] { display:none !important; }
      #tr-server-actions [hidden], .tr-run-state[hidden] { display:none !important; }
      .tr-run-state { position:fixed; right:18px; bottom:18px; z-index:50; background:#0f172a; color:#e2e8f0; border-radius:12px; padding:8px 11px; font-size:12px; box-shadow:0 12px 28px rgba(15,23,42,.25); pointer-events:none; }
      .tr-source-lock { border:1px solid #3f3f46; background:#18181b; color:#d4d4d8; border-radius:7px; padding:5px 9px; margin-left:8px; font-size:11px; font-weight:700; line-height:1; transition:.15s ease; white-space:nowrap; }
      .tr-source-lock:hover { background:#27272a; color:#fff; border-color:#6366f1; }
      .tr-source-lock.unlocked { background:#7f1d1d; color:#fff; border-color:#ef4444; }
      .highlight-textarea.tr-source-readonly { cursor:default; caret-color:transparent; }
      .highlight-textarea.tr-source-readonly:focus { box-shadow:inset 0 0 0 1px rgba(99,102,241,.35); }
      .highlight-textarea.tr-source-unlocked { caret-color:#e5e7eb; }
      .support-sidebar-wrap { display:none !important; }
      #support-sidebar { display:none !important; }
      .module-card.tr-module-focus { outline:2px solid #4f46e5; outline-offset:2px; box-shadow:0 0 0 5px rgba(79,70,229,.14); transition:outline-color .15s ease, box-shadow .15s ease; }
      @media (max-width: 1280px) { .tr-server-summary { display:none; } #tr-server-actions { gap:6px; } .tr-btn { padding:6px 8px; } }
      @media (max-width: 980px) { body > nav .h-14 { height:auto; min-height:3.5rem; padding-top:8px; padding-bottom:8px; align-items:flex-start; } #tr-server-actions { width:100%; justify-content:flex-start; margin-left:0; } }
    `;
    document.head.appendChild(style);
  }

  document.addEventListener('DOMContentLoaded', () => {
    injectStyles();
    injectServerBar();
    installSourceEditorLock();
    installLinkedModuleNavigation();
    setTimeout(installLinkedModuleNavigation, 500);
    installPlatformFieldPreservation();
    installChineseVisualLabels();
    setTimeout(loadServerConfig, 250);
    refreshRunState();
  });
})();
