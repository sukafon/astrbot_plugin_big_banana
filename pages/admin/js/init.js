// ===== 初始化事件监听和首次加载 =====
// 依赖 common.js 和 config.js，需要最后加载。

document.addEventListener('DOMContentLoaded', async function () {
  // 全局 Esc：关闭页面中已打开的弹窗。
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var overlay = document.getElementById('ccConfirmModal');
      if (overlay) overlay.classList.remove('show');
    }
  });

  // 等待 AstrBot 注入页面上下文，使首次渲染直接使用当前 WebUI 语言。
  var bridge = getPluginPageBridge();
  if (bridge && typeof bridge.ready === 'function') {
    try {
      await bridge.ready();
    } catch (e) {
      console.warn('AstrBot plugin page bridge initialization failed:', e);
    }
  }

  applyI18n();

  // 切换 WebUI 语言时原地刷新带 i18n 标记的文案，不重新加载配置。
  if (bridge && typeof bridge.onContext === 'function') {
    bridge.onContext(function () {
      applyI18n();
    });
  }

  // 首次加载配置数据和可用提供商。
  loadData();
});
