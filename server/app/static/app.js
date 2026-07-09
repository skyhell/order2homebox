// Theme toggle (theme is applied before paint by the inline script in base.html)
document.addEventListener('DOMContentLoaded', function () {
  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem('o2h-theme', next);
    });
  }

  // Show a loading state on full-page form submits (fetch/create can take a while)
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('button[type="submit"][data-loading-text]');
      if (btn) {
        btn.disabled = true;
        btn.textContent = btn.dataset.loadingText;
      }
    });
  });
});

// Remove an item card on the edit page; /create skips missing indexes.
function removeItemCard(idx) {
  var card = document.getElementById('item-card-' + idx);
  if (card) card.remove();
}
