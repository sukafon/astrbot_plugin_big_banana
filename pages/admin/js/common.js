// ===== 共享全局状态 =====
var toastTimer = null;
var config = {};
var providers = [];

// ===== AstrBot 插件页面国际化 =====
// 页面也支持脱离 Dashboard 直接打开；此时回退到 HTML/JS 中的中文文案。
function getPluginPageBridge() {
  return window.AstrBotPluginPage || null;
}

function tr(key, fallback) {
  var bridge = getPluginPageBridge();
  if (!bridge || typeof bridge.t !== 'function') return fallback || '';
  var translated = bridge.t(key, fallback || '');
  return typeof translated === 'string' ? translated : (fallback || '');
}

function rememberFallback(el, datasetKey, value) {
  if (el.dataset[datasetKey] === undefined) el.dataset[datasetKey] = value || '';
  return el.dataset[datasetKey];
}

// 翻译带 data-i18n 属性的静态及动态 DOM，并可在语言切换时原地刷新。
function applyI18n(root) {
  root = root || document;

  root.querySelectorAll('[data-i18n]').forEach(function (el) {
    var fallback = rememberFallback(el, 'i18nFallback', el.textContent);
    el.textContent = tr(el.dataset.i18n, fallback);
  });

  root.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
    var fallback = rememberFallback(el, 'i18nPlaceholderFallback', el.getAttribute('placeholder'));
    el.setAttribute('placeholder', tr(el.dataset.i18nPlaceholder, fallback));
  });

  root.querySelectorAll('[data-i18n-title]').forEach(function (el) {
    var fallback = rememberFallback(el, 'i18nTitleFallback', el.getAttribute('title'));
    el.setAttribute('title', tr(el.dataset.i18nTitle, fallback));
  });

  root.querySelectorAll('[data-i18n-alt]').forEach(function (el) {
    var fallback = rememberFallback(el, 'i18nAltFallback', el.getAttribute('alt'));
    el.setAttribute('alt', tr(el.dataset.i18nAlt, fallback));
  });
}

// ===== 跟随系统明暗主题 =====
(function () {
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  // 根据系统主题同步页面的明暗主题属性。
  function syncTheme(isDark) {
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  }
  syncTheme(mediaQuery.matches);
  mediaQuery.addEventListener('change', function(e) { syncTheme(e.matches); });
})();

// ===== 轻量提示通知 =====
// 显示轻量提示并在指定时间后自动隐藏。
function showToast(msg, dur) {
  dur = dur || 2500;
  var toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () {
    toast.classList.remove('show');
  }, dur);
}

// ===== 标签页切换 =====
// 切换标签按钮和内容面板的激活状态。
function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(function (btn) { btn.classList.remove('active'); });
  document.querySelectorAll('.tab-panel').forEach(function (panel) { panel.classList.remove('active'); });
  
  var activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(function (btn) {
    return btn.getAttribute('onclick').includes("'" + tabName + "'");
  });
  if (activeBtn) activeBtn.classList.add('active');
  
  var activePanel = document.getElementById('tab-' + tabName);
  if (activePanel) activePanel.classList.add('active');
}

// ===== 滑块数值同步 =====
// 根据滑块值刷新旁侧显示文本。
function updateSliderValue(el, isFloat, unit) {
  var indicator = document.getElementById('val-' + el.id);
  if (!indicator) return;
  var val = el.value;
  if (el.id.indexOf('probability') >= 0) {
    indicator.textContent = val + '%';
  } else if (el.id.indexOf('seconds') >= 0 || el.id.indexOf('delay') >= 0 || el.id === 'timeout') {
    indicator.textContent = val + 's';
  } else {
    indicator.textContent = val + (unit || '');
  }
}

// 初始化页面内所有滑块的显示值。
function initSliders() {
  document.querySelectorAll('.slider-input').forEach(function (slider) {
    updateSliderValue(slider);
  });
}

// ===== 响应解析辅助函数 =====
// 统一提取后端 API 响应中的有效数据。
function parseResponse(response) {
  return response && response.status === 'ok' && response.data !== undefined ? response.data : response;
}
