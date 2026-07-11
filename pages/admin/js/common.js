// ===== Shared Global States =====
var toastTimer = null;
var config = {};
var providers = [];

// ===== Theme: Follow System Dark / Light preferences =====
(function () {
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  function syncTheme(isDark) {
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  }
  syncTheme(mediaQuery.matches);
  mediaQuery.addEventListener('change', function(e) { syncTheme(e.matches); });
})();

// ===== Light Toast Notifications =====
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

// ===== Tab Navigation Switching =====
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

// ===== Slider Value Synchronization =====
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

function initSliders() {
  document.querySelectorAll('.slider-input').forEach(function (slider) {
    updateSliderValue(slider);
  });
}

// ===== Response Parsing helper =====
function parseResponse(response) {
  return response && response.status === 'ok' && response.data !== undefined ? response.data : response;
}
