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

// An order usually goes to one and the same place, so picking a location on any
// card applies it to all the others. A location that was just created is only
// swapped into the card it was created from (POST /locations re-renders that one
// select), so the other cards get the option added before it is selected —
// otherwise they would silently keep their old location.
function applyLocationToAllCards(source) {
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
}

document.addEventListener('change', function (event) {
  var el = event.target;
  if (el && el.matches && el.matches('select[id^="loc-select-"]')) {
    applyLocationToAllCards(el);
  }
});

// htmx:load fires on content htmx just put into the DOM — here the select that
// POST /locations swapped in with the new location already selected.
document.addEventListener('htmx:load', function (event) {
  var el = event.target;
  if (el && el.matches && el.matches('select[id^="loc-select-"]')) {
    applyLocationToAllCards(el);
  }
});

// Remove an item card on the edit page; /create skips missing indexes.
function removeItemCard(idx) {
  var card = document.getElementById('item-card-' + idx);
  if (card) card.remove();
}

// Keep a label preview in sync with its "print asset ID" checkbox, so the
// picture always shows what the printer would produce.
function labelPreview(imgId, assetId, showText) {
  var img = document.getElementById(imgId);
  if (img) img.src = '/label/' + assetId + '.png?text=' + (showText ? 1 : 0);
}
