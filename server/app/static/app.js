// Theme toggle (theme is applied before paint by the inline script in base.html)
document.addEventListener('DOMContentLoaded', function () {
  var root = document.documentElement;
  var toggle = document.getElementById('theme-toggle');

  function setTheme(next) {
    root.dataset.theme = next;
    localStorage.setItem('o2h-theme', next);
    // Re-trigger the little sun/moon pop on the icon that just became visible.
    toggle.classList.remove('icon-anim');
    void toggle.offsetWidth; // reflow so the animation restarts
    toggle.classList.add('icon-anim');
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      // Modern browsers: circular reveal of the new theme from the toggle.
      if (document.startViewTransition && !reduce) {
        var rect = toggle.getBoundingClientRect();
        var x = rect.left + rect.width / 2;
        var y = rect.top + rect.height / 2;
        var endRadius = Math.hypot(
          Math.max(x, window.innerWidth - x),
          Math.max(y, window.innerHeight - y)
        );
        var vt = document.startViewTransition(function () { setTheme(next); });
        vt.ready.then(function () {
          root.animate(
            {
              clipPath: [
                'circle(0px at ' + x + 'px ' + y + 'px)',
                'circle(' + endRadius + 'px at ' + x + 'px ' + y + 'px)'
              ]
            },
            { duration: 900, easing: 'ease-in-out', pseudoElement: '::view-transition-new(root)' }
          );
        });
        return;
      }

      // Fallback: brief color cross-fade of all surfaces.
      if (!reduce) {
        root.classList.add('theme-transition');
        window.setTimeout(function () { root.classList.remove('theme-transition'); }, 820);
      }
      setTheme(next);
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

// "Apply to all cards": copy this card's location to every other card. A
// location created inline only reaches the card it was created from (POST
// /locations re-renders that one select), so cards that do not know the option
// yet get it added — otherwise they would silently keep their old location.
function applyLocationToAllCards(idx, button) {
  var source = document.getElementById('loc-select-' + idx);
  if (!source) return;
  var picked = source.options[source.selectedIndex];
  if (!picked) return;
  document.querySelectorAll('select[id^="loc-select-"]').forEach(function (sel) {
    if (sel === source) return;
    var known = Array.prototype.some.call(sel.options, function (o) {
      return o.value === picked.value;
    });
    if (!known) sel.add(new Option(picked.text, picked.value));
    sel.value = picked.value;
  });
  if (!button || button.dataset.original) return;
  // The cards it changed are usually off screen, so say that it happened.
  button.dataset.original = button.textContent;
  button.textContent = button.dataset.done;
  window.setTimeout(function () {
    button.textContent = button.dataset.original;
    delete button.dataset.original;
  }, 1600);
}

// The button is pointless with a single card, and cards can be removed.
function updateApplyAllButtons() {
  var many = document.querySelectorAll('select[id^="loc-select-"]').length > 1;
  document.querySelectorAll('.apply-all').forEach(function (btn) {
    btn.classList.toggle('hidden', !many);
  });
}

document.addEventListener('DOMContentLoaded', updateApplyAllButtons);
// A card swapped in by htmx (a result card, or one re-rendered with an error)
// changes how many location selects are left.
document.addEventListener('htmx:load', updateApplyAllButtons);

// Remove an item card on the edit page; /create skips missing indexes.
function removeItemCard(idx) {
  var card = document.getElementById('item-card-' + idx);
  if (card) card.remove();
  updateApplyAllButtons();
}

// Keep a label preview in sync with its "print asset ID" checkbox, so the
// picture always shows what the printer would produce.
function labelPreview(imgId, assetId, showText) {
  var img = document.getElementById(imgId);
  if (img) img.src = '/label/' + assetId + '.png?text=' + (showText ? 1 : 0);
}
