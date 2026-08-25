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
  saveDraft(); // setting .value fires no change event
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

// Three codes across the width leave a 102 px cell, and an asset id can be
// 121 px wide — it would print across the neighbouring code. So the id is not
// a choice at three per row: the box goes off and stays off while it is on.
// One definition, used by the item cards and by the reprint page.
function applyThreeUp(threeBox, showIdBox, hint) {
  if (!threeBox || !showIdBox) return false;
  if (threeBox.checked) {
    if (showIdBox.checked) showIdBox.dataset.wasChecked = '1';
    showIdBox.checked = false;
  } else if (showIdBox.dataset.wasChecked) {
    showIdBox.checked = true; // put back what was there before
    delete showIdBox.dataset.wasChecked;
  }
  showIdBox.disabled = threeBox.checked;
  showIdBox.closest('.check').classList.toggle('check-disabled', threeBox.checked);
  if (hint) hint.hidden = !threeBox.checked;
  return threeBox.checked;
}

function updateQrCount(idx) {
  applyThreeUp(
    document.getElementById('qr3-' + idx),
    document.getElementById('showid-' + idx),
    document.getElementById('qr3-hint-' + idx)
  );
}

// Reprint page: one set of controls, and the preview has to follow both boxes.
function refreshLabelControls(assetId) {
  var showText = document.getElementById('label-show-text');
  var three = applyThreeUp(
    document.getElementById('label-qr3'), showText,
    document.getElementById('label-qr3-hint')
  );
  labelPreview('label-preview-img', assetId, showText.checked, three ? 3 : 0);
}

// Result card: the same two boxes as the reprint page, and a preview that has
// to follow both of them.
function refreshResultControls(idx, assetId) {
  var showId = document.getElementById('showid-' + idx);
  var three = applyThreeUp(
    document.getElementById('qr3-' + idx), showId,
    document.getElementById('qr3-hint-' + idx)
  );
  // 0 rather than 2: both routes resolve it the same way, so the preview shows
  // the label the print button would send.
  labelPreview('preview-' + idx, assetId, showId.checked, three ? 3 : 0);
}

function initQrCounts() {
  document.querySelectorAll('input[id^="qr3-"]').forEach(function (box) {
    updateQrCount(box.id.slice('qr3-'.length));
  });
}

document.addEventListener('DOMContentLoaded', initQrCounts);
document.addEventListener('htmx:load', initQrCounts);

// ---- unit price follows the quantity ---------------------------------------
// The scraped price and count multiply out to what the order actually charged.
// Correcting the count therefore re-splits that sum instead of re-pricing the
// item: a card that arrives as 1 x 87.03 and turns out to be 3 pieces becomes
// 3 x 29.01, not 3 x 87.03. Editing the price re-bases the sum, so a manual
// correction is not undone by the next quantity change.

function parsePrice(text) {
  // The field is rendered with a dot but German users type commas, and the
  // server accepts either.
  var value = parseFloat(String(text || '').replace(',', '.'));
  return isFinite(value) ? value : null;
}

function formatPrice(value, like) {
  var text = value.toFixed(2);
  return String(like).indexOf(',') >= 0 ? text.replace('.', ',') : text;
}

function showItemTotal(idx) {
  var hint = document.getElementById('total-' + idx);
  var price = document.getElementById('price-' + idx);
  if (!hint || !price) return;
  var total = price.dataset.total;
  hint.textContent = total === undefined ? ''
    : hint.dataset.label + ' ' + formatPrice(parseFloat(total), price.value) +
      ' ' + (hint.dataset.currency || '');
}

function rebaseItemTotal(idx) {
  var price = document.getElementById('price-' + idx);
  var qty = document.getElementById('qty-' + idx);
  if (!price || !qty) return;
  var unit = parsePrice(price.value);
  var count = parseInt(qty.value, 10);
  if (unit === null || !(count >= 1)) delete price.dataset.total;
  else price.dataset.total = String(unit * count);
  showItemTotal(idx);
}

function repriceItem(idx) {
  var price = document.getElementById('price-' + idx);
  var qty = document.getElementById('qty-' + idx);
  if (!price || !qty || price.dataset.total === undefined) return;
  var count = parseInt(qty.value, 10);
  if (!(count >= 1)) return; // cleared or mid-typing: leave the price alone
  price.value = formatPrice(parseFloat(price.dataset.total) / count, price.value);
  showItemTotal(idx);
}

function initItemPrices() {
  document.querySelectorAll('input[id^="price-"]').forEach(function (price) {
    // Only cards that do not have a sum yet — re-deriving it from an already
    // divided price would let rounding creep in on every htmx swap.
    if (price.dataset.total === undefined) {
      rebaseItemTotal(price.id.slice('price-'.length));
    }
  });
}

document.addEventListener('DOMContentLoaded', initItemPrices);
document.addEventListener('htmx:load', initItemPrices);

// Remove an item card on the edit page; /create skips missing indexes.
function removeItemCard(idx) {
  var card = document.getElementById('item-card-' + idx);
  if (card) card.remove();
  updateApplyAllButtons();
  saveDraft(); // removing a card fires no input event of its own
}

// ---- the edit page survives a page change ----------------------------------
// The page only ever existed as the answer to POST /fetch, so switching to
// another page threw the fetched order away. The form is sent to /draft while
// typing and once more on the way out; GET /edit builds the page from it.
function saveDraft() {
  var form = document.getElementById('create-form');
  if (!form) return;
  window.clearTimeout(saveDraft.timer);
  saveDraft.timer = window.setTimeout(function () {
    fetch('/draft', { method: 'POST', body: new FormData(form) });
  }, 700);
}

document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('create-form');
  if (!form) return;
  // Straight away, not debounced: the state has to be there even if the very
  // next click leaves the page.
  fetch('/draft', { method: 'POST', body: new FormData(form) });
  form.addEventListener('input', saveDraft);
  form.addEventListener('change', saveDraft);
  window.addEventListener('pagehide', function () {
    // A normal request would be cancelled as the page goes away; a beacon
    // survives it and catches whatever the 700 ms above have not sent yet.
    navigator.sendBeacon('/draft', new FormData(form));
  });
});

// Keep a label preview in sync with its "print asset ID" checkbox, so the
// picture always shows what the printer would produce.
function labelPreview(imgId, assetId, showText, count) {
  var img = document.getElementById(imgId);
  if (!img) return;
  img.src = '/label/' + assetId + '.png?text=' + (showText ? 1 : 0) +
            (count ? '&count=' + count : '');
}

// The two nested checkboxes as one value — must match labels.HEIGHT_* on the
// server, which is the single definition of what these mean.
function textHeightMode() {
  var keep = document.getElementById('text-keep-height');
  var force = document.getElementById('text-force-height');
  if (!keep || !keep.checked) return 'grow';
  return force && force.checked ? 'force' : 'keep';
}

// Set while the label on screen is no longer the one the status line talks
// about — see textLabelPreview() and the print listeners below.
var textLabelDirty = false;

// Text-label tool: the preview is the real rendering, fetched from the server,
// so what you see is exactly the PNG the printer gets. Debounced — every
// keystroke would otherwise render a label.
function textLabelPreview() {
  var img = document.getElementById('text-preview-img');
  var empty = document.getElementById('text-preview-empty');
  if (!img) return;

  // The status line belongs to the label it came from. Once the text or the
  // height changes, it stands above something that is no longer there.
  textLabelDirty = true;
  var status = document.getElementById('text-print-status');
  if (status) status.innerHTML = '';

  var line1 = (document.getElementById('text-line1').value || '').trim();
  var line2 = (document.getElementById('text-line2').value || '').trim();
  var any = line1 || line2;
  img.hidden = !any;
  if (empty) empty.hidden = !!any;

  // The "even if it gets unreadable" box refines the one above it, so it only
  // makes sense while that one is ticked.
  var keep = document.getElementById('text-keep-height');
  var row = document.getElementById('force-height-row');
  if (row) row.classList.toggle('hidden', !(keep && keep.checked));

  window.clearTimeout(textLabelPreview.timer);
  if (!any) return;
  textLabelPreview.timer = window.setTimeout(function () {
    img.src = '/text.png?line1=' + encodeURIComponent(line1) +
              '&line2=' + encodeURIComponent(line2) +
              '&height=' + textHeightMode();
  }, 250);
}

// A chip from the history puts its text back into the two inputs.
function useTextLabel(chip) {
  var line1 = document.getElementById('text-line1');
  var line2 = document.getElementById('text-line2');
  if (!line1 || !line2) return;
  line1.value = chip.dataset.line1 || '';
  line2.value = chip.dataset.line2 || '';
  textLabelPreview();
  line1.focus();
}

document.addEventListener('DOMContentLoaded', function () {
  var line1 = document.getElementById('text-line1');
  if (!line1) return;
  // The inputs are not in a form (see text_label.html), so Enter would do
  // nothing at all — on a tool used one label after another it should print.
  ['text-line1', 'text-line2', 'text-copies'].forEach(function (id) {
    var field = document.getElementById(id);
    if (!field) return;
    field.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      document.getElementById('text-print-button').click();
    });
  });

  document.getElementById('text-print-button')
    .addEventListener('htmx:beforeRequest', function () { textLabelDirty = false; });
  var status = document.getElementById('text-print-status');
  status.addEventListener('htmx:afterSwap', function () {
    // Kept typing while the printer was working: the success belongs to the
    // label before. Errors stay — they are about the job, not about the text.
    if (textLabelDirty && status.querySelector('.ok-text')) status.innerHTML = '';
  });

  textLabelPreview(); // a value the browser restored must show up in the preview
});

// ---- what was typed into a field before -----------------------------------
// The same order numbers, asset ids and label lines are entered over and over.
// The list hangs off the field itself, newest first; a click fills the field.
function closeFieldHistories(except) {
  document.querySelectorAll('.history-list').forEach(function (list) {
    if (list === except) return;
    list.classList.add('hidden');
    var toggle = list.parentNode.querySelector('.history-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  });
}

function toggleFieldHistory(button) {
  var list = button.parentNode.querySelector('.history-list');
  if (!list) return;
  var open = list.classList.contains('hidden');
  closeFieldHistories(list);
  list.classList.toggle('hidden', !open);
  button.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function useHistoryEntry(entry) {
  var field = document.getElementById(entry.dataset.target);
  if (!field) return;
  field.value = entry.dataset.value || '';
  // An order number means nothing without its shop, so the pair travels.
  if (entry.dataset.shop) {
    var radio = document.getElementById('shop-' + entry.dataset.shop);
    if (radio) radio.checked = true;
  }
  closeFieldHistories();
  field.focus();
  // Assigning value fires nothing — the text page hangs its preview on oninput.
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

document.addEventListener('click', function (event) {
  if (!event.target.closest('.field-history')) closeFieldHistories();
});
document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') closeFieldHistories();
});

// ---- text boxes that grow with their content ------------------------------
// Marketplace item names run to 200 characters. In a one-line field that means
// scrolling inside the line to read the end, so name and description wrap and
// the box follows the text. How far it may grow is capped in app.css.
function autoGrow(el) {
  el.style.height = 'auto';
  // box-sizing is border-box (app.css) — scrollHeight leaves the border out
  el.style.height = (el.scrollHeight + el.offsetHeight - el.clientHeight) + 'px';
}

function initAutoGrow() {
  document.querySelectorAll('textarea.autogrow').forEach(autoGrow);
}

document.addEventListener('DOMContentLoaded', initAutoGrow);
// A card re-rendered by htmx (an error, a result) brings fresh boxes with it.
document.addEventListener('htmx:load', initAutoGrow);
// Delegated, so a swapped-in card needs no listener of its own.
document.addEventListener('input', function (event) {
  var el = event.target;
  if (el.classList && el.classList.contains('autogrow')) autoGrow(el);
});
