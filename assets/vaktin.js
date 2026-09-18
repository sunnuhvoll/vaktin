// Vaktin — site behaviour. Loaded with `defer`, so the document is parsed
// and the inline window.VAKTIN_REGIONS data (reports page) is already set.
(function() {
  'use strict';

  // ── Iframe breakout: logo click opens standalone site ──
  var logo = document.querySelector('.brand[target="_top"]');
  if (logo) {
    logo.addEventListener('click', function(e) {
      e.preventDefault();
      var url = logo.href;
      // Try multiple methods to break out of iframe
      try { window.top.location.href = url; } catch (err) {
        window.open(url, '_blank');
      }
    });
  }

  // ── Embedded in the Google Sites iframe on logverndarsjodur.org ──
  // The sandbox allows popups but not top navigation, and most destinations
  // forbid framing, so an off-site link without a target would replace the
  // embed with a blocked frame. Open those in a new tab instead.
  var embedded;
  try { embedded = window.self !== window.top; } catch (err) { embedded = true; }
  if (embedded) {
    document.querySelectorAll('a[href]').forEach(function(a) {
      if (a.hostname && a.hostname !== location.hostname && !a.getAttribute('target')) {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      }
    });
  }

  // ── Theme toggle: auto → dark → light → auto ──
  (function() {
    var modes = ['auto', 'dark', 'light'];
    var toggle = document.getElementById('theme-toggle');
    if (!toggle) return;

    function read() {
      try {
        var stored = localStorage.getItem('vaktin-theme') || 'auto';
        return modes.indexOf(stored) < 0 ? 'auto' : stored;
      } catch (e) { return 'auto'; }
    }

    // Mirrors --paper in vaktin.css and the two <meta name="theme-color"> tags.
    var CHROME = { light: '#edece4', dark: '#151813' };
    var metas = document.querySelectorAll('meta[name="theme-color"]');

    function apply(mode) {
      if (mode === 'auto') document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', mode);
      toggle.dataset.mode = mode;
      // A forced theme also overrides the OS-bound browser-chrome tint.
      metas.forEach(function(m, i) {
        m.setAttribute('content', mode === 'auto' ? (i === 0 ? CHROME.light : CHROME.dark) : CHROME[mode]);
      });
    }

    // Track the mode in memory; localStorage is only best-effort persistence.
    // It throws inside the embed when third-party storage is blocked.
    var current = read();
    apply(current);

    toggle.addEventListener('click', function() {
      current = modes[(modes.indexOf(current) + 1) % modes.length];
      try { localStorage.setItem('vaktin-theme', current); } catch (e) {}
      apply(current);
    });
  })();

  // ── Scroll to top ──
  var scrollBtn = document.querySelector('.scroll-top-btn');
  if (scrollBtn) {
    window.addEventListener('scroll', function() {
      scrollBtn.classList.toggle('is-visible', window.scrollY > 400);
    }, { passive: true });
    scrollBtn.addEventListener('click', function() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  // ── Tabs: on very narrow screens the strip scrolls; show the current tab ──
  var currentTab = document.querySelector('.tabs a[aria-current="page"]');
  if (currentTab) {
    var strip = currentTab.parentNode;
    if (strip.scrollWidth > strip.clientWidth) {
      strip.scrollLeft = currentTab.offsetLeft + currentTab.offsetWidth - strip.clientWidth + 16;
    }
  }

  // ── Normalise pages generated before the redesign ──
  // Older pages carry emoji in severity headings and deadlines, and wrap the
  // group count in parentheses. They are rewritten on the next pipeline run;
  // until then, tidy them here so old and new pages look the same.
  document.querySelectorAll('.severity-section > h2').forEach(function(h2) {
    Array.from(h2.childNodes).forEach(function(node) {
      if (node.nodeType !== 3) return;
      node.textContent = node.textContent.replace(/[🔴🟡🔵]\s*/u, '').replace(/[()]/g, '');
    });
  });
  document.querySelectorAll('.issue-item > .deadline').forEach(function(p) {
    var first = p.firstChild;
    if (first && first.nodeType === 3) first.textContent = first.textContent.replace(/⏰\s*/u, '');
  });

  // ── Deadlines: say how far away they are, dim the ones that have passed ──
  (function() {
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    document.querySelectorAll('.issue-item > .deadline').forEach(function(p) {
      var iso = p.dataset.deadline;
      if (!iso) {
        // Older markup: the deadline is plain text after the label.
        var m = p.textContent.match(/(\d{4}-\d{2}-\d{2})\s*$/);
        if (!m || p.textContent.match(/\d{4}-\d{2}-\d{2}/g).length !== 1) return;
        iso = m[1];
      }
      var date = new Date(iso + 'T00:00:00');
      // Browsers roll impossible days over (2026-04-31 → 1 May); only label real calendar dates.
      if (Number.isNaN(date.getTime()) || date.getDate() !== +iso.slice(8, 10)) return;
      var days = Math.round((date - today) / 86400000);
      var rel;
      if (days < 0) { rel = 'liðinn'; p.classList.add('is-past'); }
      else if (days === 0) rel = 'í dag';
      else if (days === 1) rel = 'á morgun';
      else if (days <= 60) rel = 'eftir ' + days + ' daga';
      if (!rel) return;
      var span = document.createElement('span');
      span.className = 'deadline-rel';
      span.textContent = '· ' + rel;
      p.appendChild(span);
    });
  })();

  // ── Tables: horizontal scroll on narrow screens, status dots ──
  var STATUS_CLASS = { 'Virkt': 'status--ok', 'Tómt': 'status--warn', 'Vantar scraper': 'status--bad' };
  document.querySelectorAll('.prose table').forEach(function(table) {
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap';
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);
    // kramdown renders a "| | |" header row as <th>&nbsp;</th>, so CSS :empty can never match it.
    if (table.tHead && !table.tHead.textContent.trim()) table.classList.add('no-head');
    table.querySelectorAll('td').forEach(function(td) {
      var cls = STATUS_CLASS[td.textContent.trim()];
      if (!cls || td.children.length) return;
      var span = document.createElement('span');
      span.className = 'status ' + cls;
      span.textContent = td.textContent.trim();
      td.textContent = '';
      td.appendChild(span);
    });
  });

  // ── Filter bar ──
  var target = document.getElementById('filter-target');
  if (!target) return;

  var R = window.VAKTIN_REGIONS;
  var L = window.VAKTIN_REGION_LABELS;
  var items = Array.from(document.querySelectorAll('.issue-item'));
  if (!R || !L || !items.length) {
    target.hidden = true; // release the space vaktin.css reserves for the panel
    return;
  }

  var regionOrder = [
    'hofudborgarsvaedid', 'sudurnes', 'vesturland', 'vestfirdir',
    'nordurland', 'austurland', 'sudurland', 'landsvitt'
  ];
  var activeRegions = {};
  items.forEach(function(el) {
    var r = el.dataset.region;
    activeRegions[r] = (activeRegions[r] || 0) + 1;
  });

  var state = { region: 'all', time: 'all', category: 'all', sources: new Set() };

  // Collapsible panel: open on desktop, closed on phones where the chips
  // would otherwise push the list several screens down.
  var bar = document.createElement('details');
  bar.className = 'filter-bar panel';
  bar.open = window.matchMedia('(min-width: 721px)').matches;

  var head = document.createElement('summary');
  head.className = 'panel-head';
  head.innerHTML =
    '<span class="panel-title eyebrow">Síur <span class="filter-active-count" hidden></span></span>' +
    '<span class="filter-status"><span class="filter-shown"></span>' +
    '<button type="button" class="filter-reset" hidden>Hreinsa síur</button></span>';
  bar.appendChild(head);

  var body = document.createElement('div');
  body.className = 'filter-body';
  bar.appendChild(body);

  var activeCountEl = head.querySelector('.filter-active-count');
  var shownEl = head.querySelector('.filter-shown');
  var resetBtn = head.querySelector('.filter-reset');

  function makeGroup(labelText) {
    var group = document.createElement('div');
    group.className = 'filter-group';
    var label = document.createElement('span');
    label.className = 'filter-label';
    label.textContent = labelText;
    var chips = document.createElement('div');
    chips.className = 'filter-chips';
    group.appendChild(label);
    group.appendChild(chips);
    body.appendChild(group);
    return chips;
  }

  function makeBtn(group, type, id, text, count) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-btn';
    btn.dataset.filterType = type;
    btn.dataset.filterValue = id;
    btn.dataset.label = text;
    btn.textContent = text;
    if (count != null) {
      var c = document.createElement('span');
      c.className = 'btn-count';
      c.textContent = count;
      btn.appendChild(c);
    }
    btn.addEventListener('click', function(e) {
      if (type === 'region') state.region = id;
      if (type === 'time') state.time = id;
      if (type === 'category') {
        if (id === 'all') {
          state.category = 'all';
        } else if (e.altKey || e.metaKey || e.shiftKey) {
          state.category = state.category === 'hide:' + id ? 'all' : 'hide:' + id;
        } else {
          state.category = state.category === 'only:' + id ? 'all' : 'only:' + id;
        }
      }
      applyFilters();
    });
    if (type === 'category' && id !== 'all') {
      // Keyboard path for "hide": synthesized clicks don't carry modifiers in every browser.
      btn.addEventListener('keydown', function(e) {
        if (!e.shiftKey || e.repeat || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault(); // suppresses the synthesized click, so no double toggle
        state.category = state.category === 'hide:' + id ? 'all' : 'hide:' + id;
        applyFilters();
      });
    }
    group.appendChild(btn);
    return btn;
  }

  var regionGroup = makeGroup('Svæði');
  var timeGroup = makeGroup('Nýtt innan');
  var categoryGroup = makeGroup('Flokkur');
  var sourceGroup = makeGroup('Heimild');

  makeBtn(regionGroup, 'region', 'all', 'Öll svæði', items.length);
  regionOrder.forEach(function(rid) {
    if (!activeRegions[rid]) return;
    var name = L[rid] || rid;
    if (rid === 'landsvitt') name = 'Allt landið';
    makeBtn(regionGroup, 'region', rid, name, activeRegions[rid]);
  });

  makeBtn(timeGroup, 'time', 'all', 'Allt', items.length);
  makeBtn(timeGroup, 'time', '1', '1 dagur');
  makeBtn(timeGroup, 'time', '2', '2 dagar');
  makeBtn(timeGroup, 'time', '3', '3 dagar');
  makeBtn(timeGroup, 'time', '7', '7 dagar');

  var activeCategories = {};
  items.forEach(function(el) {
    var cats = (el.dataset.category || '').split(';');
    cats.forEach(function(cat) {
      cat = cat.trim();
      if (cat) activeCategories[cat] = (activeCategories[cat] || 0) + 1;
    });
  });
  makeBtn(categoryGroup, 'category', 'all', 'Allir flokkar');
  Object.keys(activeCategories).sort(function(a, b) { return a.localeCompare(b, 'is'); }).forEach(function(cat) {
    var label = cat.charAt(0).toUpperCase() + cat.slice(1);
    makeBtn(categoryGroup, 'category', cat, label, activeCategories[cat]);
  });
  var hint = document.createElement('p');
  hint.className = 'filter-hint';
  hint.textContent = 'Smelltu á flokk til að sjá hann einan.';
  var hintDesktop = document.createElement('span');
  hintDesktop.className = 'hint-desktop';
  hintDesktop.textContent = ' Alt-, ⌘- eða Shift-smellur (Shift+Enter á lyklaborði) felur flokkinn.';
  hint.appendChild(hintDesktop);
  categoryGroup.parentNode.appendChild(hint);

  // ── Heimild multi-select dropdown ──
  var activeSources = {};
  items.forEach(function(el) {
    var s = el.dataset.source;
    if (s && s !== 'undefined' && s !== 'null') {
      activeSources[s] = (activeSources[s] || 0) + 1;
    }
  });

  var sourceWrap = document.createElement('div');
  sourceWrap.className = 'source-wrap';
  var sourceBtn = document.createElement('button');
  sourceBtn.type = 'button';
  sourceBtn.className = 'source-btn';
  sourceBtn.setAttribute('aria-expanded', 'false');
  sourceBtn.innerHTML = '<span class="source-btn-label">Allar heimildir</span> <span aria-hidden="true">▾</span>';

  var sourceDropdown = document.createElement('div');
  sourceDropdown.className = 'source-dropdown';
  sourceDropdown.hidden = true;
  sourceDropdown.innerHTML =
    '<input type="search" class="source-search" placeholder="Leita að heimild…" aria-label="Leita að heimild">' +
    '<div class="source-tools">' +
      '<button type="button" class="filter-reset source-clear">Hreinsa val</button>' +
      '<span class="source-total"></span>' +
    '</div>' +
    '<div class="source-list"></div>';

  var sourceList = sourceDropdown.querySelector('.source-list');
  var sourceSearch = sourceDropdown.querySelector('.source-search');
  var sourceClear = sourceDropdown.querySelector('.source-clear');
  var sourceNames = Object.keys(activeSources).sort();
  sourceDropdown.querySelector('.source-total').textContent = sourceNames.length + ' heimildir';

  sourceNames.forEach(function(src) {
    var row = document.createElement('label');
    row.className = 'source-row';
    row.dataset.source = src;
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'source-cb';
    cb.value = src;
    var name = document.createElement('span');
    name.className = 'source-row-name';
    name.textContent = src;
    var count = document.createElement('span');
    count.className = 'source-row-count';
    count.textContent = activeSources[src];
    row.appendChild(cb);
    row.appendChild(name);
    row.appendChild(count);
    sourceList.appendChild(row);

    cb.addEventListener('change', function() {
      if (cb.checked) state.sources.add(src);
      else state.sources.delete(src);
      updateSourceBtnLabel();
      applyFilters();
    });
  });

  function updateSourceBtnLabel() {
    var lbl = sourceBtn.querySelector('.source-btn-label');
    var arr = Array.from(state.sources);
    if (arr.length === 0) lbl.textContent = 'Allar heimildir';
    else if (arr.length === 1) lbl.textContent = arr[0];
    else lbl.textContent = arr.length + ' heimildir valdar';
    sourceBtn.classList.toggle('is-active', arr.length > 0);
  }

  function setDropdown(open) {
    sourceDropdown.hidden = !open;
    sourceBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    // Autofocus only where a hardware keyboard is the norm; on touch it would raise the keyboard over the list.
    if (open && window.matchMedia('(hover: hover) and (pointer: fine)').matches) sourceSearch.focus();
  }

  sourceBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    e.preventDefault();
    setDropdown(sourceDropdown.hidden);
  });
  document.addEventListener('click', function(e) {
    if (!sourceWrap.contains(e.target)) setDropdown(false);
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && !sourceDropdown.hidden) { setDropdown(false); sourceBtn.focus(); }
  });

  // Close when keyboard focus leaves the widget (Tab / Shift+Tab out).
  // relatedTarget is null on window blur and on Safari button clicks;
  // those cases stay with the document click handler above.
  sourceWrap.addEventListener('focusout', function(e) {
    if (!sourceDropdown.hidden && e.relatedTarget && !sourceWrap.contains(e.relatedTarget)) setDropdown(false);
  });

  sourceSearch.addEventListener('input', function() {
    var q = this.value.toLowerCase();
    sourceList.querySelectorAll('.source-row').forEach(function(row) {
      row.hidden = !!q && row.dataset.source.toLowerCase().indexOf(q) < 0;
    });
  });

  function clearSources() {
    state.sources.clear();
    sourceList.querySelectorAll('.source-cb').forEach(function(cb) { cb.checked = false; });
    updateSourceBtnLabel();
  }
  sourceClear.addEventListener('click', function() {
    clearSources();
    applyFilters();
  });

  sourceWrap.appendChild(sourceBtn);
  sourceWrap.appendChild(sourceDropdown);
  sourceGroup.appendChild(sourceWrap);

  resetBtn.addEventListener('click', function(e) {
    // The button sits inside <summary>; don't toggle the panel.
    e.preventDefault();
    e.stopPropagation();
    var hadFocus = document.activeElement === resetBtn;
    state.region = 'all';
    state.time = 'all';
    state.category = 'all';
    clearSources();
    applyFilters();
    // applyFilters() hides this button; don't let focus fall to <body>.
    if (hadFocus) head.focus({ preventScroll: true });
  });

  target.appendChild(bar);

  // ── Histogram: items by day and severity ──
  var SEVERITIES = ['critical', 'important', 'monitor'];
  var HIST_NAMES = { critical: 'Aðkallandi', important: 'Mikilvægt', monitor: 'Eftirlit' };
  var HIST_HEIGHT = 84;

  var histCard = document.createElement('div');
  histCard.className = 'hist panel';
  histCard.innerHTML =
    '<div class="panel-head">' +
      '<span class="panel-title eyebrow">Mál eftir dögum</span>' +
      '<span class="hist-legend">' +
        SEVERITIES.map(function(s) {
          return '<span data-severity="' + s + '"><i></i>' + HIST_NAMES[s] + '</span>';
        }).join('') +
      '</span>' +
    '</div>' +
    '<div class="hist-chart"></div>' +
    '<div class="hist-labels"></div>';
  var histChart = histCard.querySelector('.hist-chart');
  var histLabels = histCard.querySelector('.hist-labels');

  var histTooltip = document.createElement('div');
  histTooltip.className = 'hist-tooltip';
  document.body.appendChild(histTooltip);

  target.appendChild(histCard);

  var noResults = document.createElement('p');
  noResults.className = 'no-results';
  noResults.hidden = true;
  noResults.textContent = 'Engin mál passa við þessar síur.';
  target.appendChild(noResults);

  // Screen readers hear the new result count after each filter change.
  var liveEl = document.createElement('p');
  liveEl.className = 'sr-only';
  liveEl.setAttribute('role', 'status');
  target.appendChild(liveEl);
  var announce = false;

  function isoDay(d) {
    // Local calendar day — toISOString() would shift the date across UTC.
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
  }

  function severityOf(el) {
    var sec = el.closest('.severity-section');
    var sev = sec ? sec.dataset.severity : el.dataset.severity;
    return SEVERITIES.indexOf(sev) >= 0 ? sev : 'monitor';
  }

  // Keyboard focus has no pointer position, so anchor the tooltip above the column itself.
  function placeTipAbove(col) {
    var r = col.getBoundingClientRect();
    var x = Math.max(8, Math.min(r.left, window.innerWidth - histTooltip.offsetWidth - 8));
    histTooltip.style.left = x + 'px';
    histTooltip.style.top = Math.max(8, r.top - histTooltip.offsetHeight - 10) + 'px';
  }

  function updateHistogram() {
    // The columns are rebuilt below, so a visible tooltip would lose its owner.
    histTooltip.classList.remove('is-visible');
    var dateData = {};
    items.forEach(function(el) {
      var date = el.dataset.date;
      if (!date) return;
      if (!passesRegion(el, state.region)) return;
      if (!passesCategory(el, state.category)) return;
      if (!passesSource(el, state.sources)) return;
      if (!dateData[date]) dateData[date] = { critical: 0, important: 0, monitor: 0 };
      dateData[date][severityOf(el)]++;
    });

    var sortedDates = Object.keys(dateData).sort();
    // The rebuild destroys the column that was just activated; remember it so keyboard focus survives.
    var active = document.activeElement;
    var refocus = (active && histChart.contains(active)) ? active.dataset.histDate : null;
    histChart.innerHTML = '';
    histLabels.innerHTML = '';
    if (!sortedDates.length) {
      histChart.innerHTML = '<span class="hist-empty">Engin mál</span>';
      return;
    }

    // Fill date gaps so empty days are visible
    var start = new Date(sortedDates[0] + 'T00:00:00');
    var end = new Date(sortedDates[sortedDates.length - 1] + 'T00:00:00');
    var filled = [];
    for (var d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      var ds = isoDay(d);
      filled.push(ds);
      if (!dateData[ds]) dateData[ds] = { critical: 0, important: 0, monitor: 0 };
    }
    filled.reverse(); // newest first (left)

    var maxVal = 1;
    filled.forEach(function(ds) {
      var dd = dateData[ds];
      var t = dd.critical + dd.important + dd.monitor;
      if (t > maxVal) maxVal = t;
    });

    var selectedDate = (state.time.indexOf('date:') === 0) ? state.time.slice(5) : null;

    filled.forEach(function(ds, idx) {
      var dd = dateData[ds];
      var total = dd.critical + dd.important + dd.monitor;
      var parts = ds.split('-');
      var label = parseInt(parts[2], 10) + '.' + parseInt(parts[1], 10) + '.' + parts[0];

      var col = document.createElement('button');
      col.type = 'button';
      col.className = 'hist-col' + (total ? '' : ' is-empty');
      col.dataset.histDate = ds;
      col.setAttribute('aria-label', label + ' — ' + total + ' mál');
      if (!total) col.tabIndex = -1;
      if (selectedDate && selectedDate !== ds) col.classList.add('is-dim');
      if (selectedDate === ds) col.setAttribute('aria-pressed', 'true');

      col.addEventListener('click', function() {
        if (!total && state.time !== 'date:' + ds) return;
        state.time = (state.time === 'date:' + ds) ? 'all' : 'date:' + ds; // click again to clear
        applyFilters();
      });
      function showTip() {
        var lines = ['<strong>' + label + '</strong> — ' + total + ' mál'];
        SEVERITIES.forEach(function(s) {
          if (dd[s] > 0) lines.push(HIST_NAMES[s] + ': ' + dd[s]);
        });
        histTooltip.innerHTML = lines.join('<br>');
        histTooltip.classList.add('is-visible');
      }
      // Pointer events, not mouse events: a tap has no "leave", so on touch the tooltip would stick.
      col.addEventListener('pointerenter', function(e) {
        if (e.pointerType !== 'touch') showTip();
      });
      col.addEventListener('pointermove', function(e) {
        if (e.pointerType === 'touch') return;
        var x = Math.min(e.clientX + 12, window.innerWidth - histTooltip.offsetWidth - 8);
        histTooltip.style.left = x + 'px';
        histTooltip.style.top = (e.clientY - histTooltip.offsetHeight - 10) + 'px';
      });
      col.addEventListener('pointerleave', function() {
        histTooltip.classList.remove('is-visible');
      });
      col.addEventListener('focus', function() {
        if (!col.matches(':focus-visible')) return;
        showTip();
        placeTipAbove(col);
      });
      col.addEventListener('blur', function() {
        histTooltip.classList.remove('is-visible');
      });

      SEVERITIES.forEach(function(sev) {
        if (dd[sev] <= 0) return;
        var seg = document.createElement('span');
        seg.className = 'hist-seg';
        seg.dataset.severity = sev;
        seg.style.height = Math.max(2, (dd[sev] / maxVal) * HIST_HEIGHT) + 'px';
        col.appendChild(seg);
      });
      histChart.appendChild(col);

      var lbl = document.createElement('span');
      var day = new Date(ds + 'T00:00:00');
      // Always label the first bar (newest); label Fridays for older weeks (skip if too close to first)
      if (idx === 0 || (day.getDay() === 5 && idx > 2)) {
        lbl.textContent = day.getDate() + '.' + (day.getMonth() + 1) + '.';
      }
      histLabels.appendChild(lbl);
    });

    if (refocus) {
      var again = histChart.querySelector('[data-hist-date="' + refocus + '"]');
      if (again) again.focus({ preventScroll: true });
    }
  }

  function passesRegion(el, region) {
    if (region === 'all') return true;
    return el.dataset.region === region;
  }

  function passesTime(el, days) {
    if (days === 'all') return true;
    // Exact date filter from histogram click: "date:2026-04-08"
    if (days.indexOf('date:') === 0) {
      return el.dataset.date === days.slice(5);
    }
    var dateValue = el.dataset.date;
    if (!dateValue) return false;
    var itemDate = new Date(dateValue + 'T00:00:00');
    if (Number.isNaN(itemDate.getTime())) return false;
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var diffDays = Math.floor((today - itemDate) / 86400000);
    return diffDays >= 0 && diffDays <= Number(days);
  }

  function passesCategory(el, catState) {
    if (catState === 'all') return true;
    var cats = (el.dataset.category || '').split(';');
    if (catState.indexOf('only:') === 0) return cats.indexOf(catState.slice(5)) >= 0;
    if (catState.indexOf('hide:') === 0) return cats.indexOf(catState.slice(5)) < 0;
    return true;
  }

  function passesSource(el, selectedSources) {
    if (!selectedSources || selectedSources.size === 0) return true;
    return selectedSources.has(el.dataset.source);
  }

  function applyFilters() {
    // Pre-compute which items pass each filter dimension
    // so we can efficiently cross-count
    var passR = [], passT = [], passC = [], passS = [];
    items.forEach(function(el, i) {
      passR[i] = passesRegion(el, state.region);
      passT[i] = passesTime(el, state.time);
      passC[i] = passesCategory(el, state.category);
      passS[i] = passesSource(el, state.sources);
    });

    // Show/hide items and count visible
    var visible = 0;
    items.forEach(function(el, i) {
      var show = passR[i] && passT[i] && passC[i] && passS[i];
      el.classList.toggle('hidden', !show);
      if (show) visible++;
    });

    // Cross-filter counts: for each button, count items that pass
    // the OTHER dimensions AND match this button's value
    bar.querySelectorAll('.filter-btn').forEach(function(b) {
      var ft = b.dataset.filterType;
      var fv = b.dataset.filterValue;
      var isActive = false;
      var isExcluded = false;

      if (ft === 'region') isActive = fv === state.region;
      else if (ft === 'time') isActive = fv === state.time;
      else if (ft === 'category') {
        if (fv === 'all') isActive = state.category === 'all';
        else {
          isActive = state.category === 'only:' + fv;
          isExcluded = state.category === 'hide:' + fv;
        }
      }

      b.classList.toggle('is-active', isActive);
      b.classList.toggle('is-excluded', isExcluded);
      b.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      // "Hidden" is otherwise only red strike-through text.
      var exNote = b.querySelector('.sr-only');
      if (isExcluded && !exNote) {
        exNote = document.createElement('span');
        exNote.className = 'sr-only';
        exNote.textContent = '(falinn)';
        b.appendChild(exNote);
      } else if (!isExcluded && exNote) {
        exNote.remove();
      }

      // Update count based on other active filters
      var count = 0;
      items.forEach(function(el, i) {
        var match = false;
        if (ft === 'region') {
          // Keep time + category + source, test this region value
          match = passT[i] && passC[i] && passS[i] && passesRegion(el, fv);
        } else if (ft === 'time') {
          // Keep region + category + source, test this time value
          match = passR[i] && passC[i] && passS[i] && passesTime(el, fv);
        } else if (ft === 'category') {
          // Keep region + time + source, test this category value
          if (fv === 'all') {
            match = passR[i] && passT[i] && passS[i];
          } else {
            match = passR[i] && passT[i] && passS[i] && passesCategory(el, 'only:' + fv);
          }
        }
        if (match) count++;
      });

      var countSpan = b.querySelector('.btn-count');
      if (!countSpan) {
        countSpan = document.createElement('span');
        countSpan.className = 'btn-count';
        b.appendChild(countSpan);
      }
      countSpan.textContent = count;
    });

    var tc = document.getElementById('total-count');
    if (tc) tc.textContent = visible;

    document.querySelectorAll('.severity-section').forEach(function(sec) {
      var vis = 0;
      sec.querySelectorAll('.issue-item').forEach(function(el) {
        if (!el.classList.contains('hidden')) vis++;
      });
      sec.classList.toggle('hidden', vis === 0);
      var gc = sec.querySelector('.group-count');
      if (gc) gc.textContent = vis;
    });

    var activeCount = (state.region !== 'all' ? 1 : 0) + (state.time !== 'all' ? 1 : 0) +
      (state.category !== 'all' ? 1 : 0) + (state.sources.size ? 1 : 0);
    activeCountEl.hidden = activeCount === 0;
    activeCountEl.textContent = activeCount;
    resetBtn.hidden = activeCount === 0;
    shownEl.textContent = visible === items.length
      ? items.length + ' mál'
      : visible + ' af ' + items.length + ' málum';
    noResults.hidden = visible !== 0;
    if (announce) liveEl.textContent = visible === 0 ? noResults.textContent : shownEl.textContent;

    updateHistogram();
  }

  applyFilters();
  announce = true; // skip the announcement for the initial render
})();
