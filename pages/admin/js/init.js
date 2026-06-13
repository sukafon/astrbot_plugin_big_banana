// ===== Initialization: Event Listeners Binding and First Page Load =====
// This script relies on common.js and config.js. It must be loaded last.

document.addEventListener('DOMContentLoaded', function () {
  // Global Esc: Close overlay modals if any exists in the page structure
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var overlay = document.getElementById('ccConfirmModal');
      if (overlay) overlay.classList.remove('show');
    }
  });

  // First page load: Fetch configuration data and active providers
  loadData();
});
