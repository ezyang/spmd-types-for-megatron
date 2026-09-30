(function () {
  var meta = function (n) { var e = document.querySelector('meta[name="tour-' + n + '"]'); return e ? e.content : ''; };
  var repo = meta('repo'), head = meta('head'), compare = meta('compare');

  function sha256hex(s) {
    if (!(window.crypto && crypto.subtle)) return Promise.resolve(null);
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)).then(function (b) {
      return Array.prototype.map.call(new Uint8Array(b), function (x) { return x.toString(16).padStart(2, '0'); }).join('');
    });
  }

  function parse(text) {
    var rows = [], o = 0, n = 0;
    text = text.replace(/^\n/, '').replace(/\n$/, '');
    text.split('\n').forEach(function (line) {
      var m = /^(@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@) ?(.*)$/.exec(line);
      if (m) {
        o = +m[2]; n = +m[3];
        rows.push({ sep: true, range: m[1], text: m[4] });
        return;
      }
      if (line.charAt(0) === '\\') return;
      var tag = line.charAt(0) || ' ', body = line.slice(1);
      if (tag !== '+' && tag !== '-') { tag = ' '; body = line.slice(line.charAt(0) === ' ' ? 1 : 0); }
      var row = { tag: tag, text: body, old: null, new: null };
      if (tag !== '+') row.old = o++;
      if (tag !== '-') row.new = n++;
      rows.push(row);
    });
    return rows;
  }

  function lineSet(spec) {
    if (!spec) return null;
    var set = {};
    spec.split(',').forEach(function (part) {
      var m = /^\s*(\d+)(?:\s*-\s*(\d+))?\s*$/.exec(part);
      if (!m) return;
      var a = +m[1], b = m[2] ? +m[2] : a;
      for (var i = a; i <= b; i++) set[i] = true;
    });
    return set;
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function render(pre, box, grouped, mode) {
    var file = pre.getAttribute('data-file') || '', symbol = pre.getAttribute('data-symbol') || '';
    var isPython = /\.py$/i.test(file);
    var only = lineSet(pre.getAttribute('data-lines'));
    var collapse = pre.hasAttribute('data-collapse');
    var fold = pre.hasAttribute('data-fold');
    var rows = parse(pre.textContent);

    // Deleted rows sit at the position of the next new-side line.
    var nextNew = null;
    for (var i = rows.length - 1; i >= 0; i--) {
      if (rows[i].sep) continue;
      if (rows[i].new != null) nextNew = rows[i].new;
      rows[i].at = rows[i].new != null ? rows[i].new : nextNew;
    }
    var kept = [];
    rows.forEach(function (r, idx) {
      if (r.sep) { kept.push(r); return; }
      if (mode !== 'diff' && r.tag === '-') return;
      if (collapse && r.tag === ' ') return;
      if (only && !(r.at != null && only[r.at])) return;
      r.idx = idx;
      kept.push(r);
    });

    // data-fold: context more than FOLD lines from any change starts hidden,
    // behind a row that expands it.  Mark the far rows now; runs are built
    // while emitting.
    if (fold) {
      var changed = [];
      kept.forEach(function (r, k) { if (!r.sep && r.tag !== ' ') changed.push(k); });
      kept.forEach(function (r, k) {
        if (r.sep || r.tag !== ' ') return;
        r.far = changed.every(function (c) { return Math.abs(c - k) > FOLD; });
      });
    }

    // A run of adjacent same-file hunks shares one box and one file header;
    // each hunk then gets its own symbol row.  A lone hunk keeps the symbol
    // in the header.
    var hunk = el('div', 'tour-hunk');
    hunk.id = file + '::' + symbol;
    if (!box) {
      box = el('div', 'tour');
      var headRow = el('div', 'tour-head');
      var fileLink = el('a', null, file);
      fileLink.href = compare;
      headRow.appendChild(fileLink);
      var blob = el('a', 'sym', 'view file');
      blob.href = 'https://github.com/' + repo + '/blob/' + head + '/' + file;
      headRow.appendChild(blob);
      box.appendChild(headRow);
      sha256hex(file).then(function (h) { if (h) fileLink.href = compare + '#diff-' + h; });
      pre.insertAdjacentElement('afterend', box);
    }

    var table = el('table'), tbody = el('tbody');
    // Within a box, a hunk that picks up exactly where the previous one ended
    // (per-symbol units of a brand-new file) is contiguous: no header row.
    var prev = box.tourLast || null, pendingSep = null, foldRun = null, foldRows = [];
    kept.forEach(function (r) {
      if (r.sep) { pendingSep = r; return; }
      var gap = prev && !((r.old != null && prev.old != null && r.old === prev.old + 1) || (r.new != null && prev.new != null && r.new === prev.new + 1) || (r.at != null && r.at === prev.at) || (prev.tag === '-' && r.new === prev.at));
      if (!prev || gap || (pendingSep && !prev.fromBox)) {
        // GitHub-style hunk header: tinted gutter, then "@@ -a,b +c,d @@ context".
        var sep = el('tr', 'sep');
        var gutter = mode === 'diff' ? 2 : 1;
        for (var g = 0; g < gutter; g++) sep.appendChild(el('td', 'ln'));
        var label = pendingSep ? pendingSep.range + (pendingSep.text ? ' ' + pendingSep.text : '') : '\u22ef';
        var td = el('td', 'code', label);
        td.colSpan = mode === 'added' ? 1 : 2;
        sep.appendChild(td);
        tbody.appendChild(sep);
      }
      pendingSep = null;
      var tr = el('tr');
      tr.className = r.tag === '+' ? 'add' : r.tag === '-' ? 'del' : 'ctx';
      if (r.far) {
        if (!foldRun) {
          foldRun = [];
          var foldTr = el('tr', 'fold'), foldTd = el('td', 'code');
          foldTd.colSpan = (mode === 'diff' ? 2 : 1) + (mode === 'added' ? 1 : 2);
          foldTr.appendChild(foldTd);
          tbody.appendChild(foldTr);
          (function (run, ftr, ftd) {
            ftr.addEventListener('click', function () {
              var open = ftr.classList.toggle('open');
              run.forEach(function (t) { t.style.display = open ? '' : 'none'; });
              ftd.textContent = (open ? '\u25be collapse ' : '\u25b8 expand ') + run.length + ' lines';
            });
            ftr.tourLabel = function () { ftd.textContent = '\u25b8 expand ' + run.length + ' lines'; };
          })(foldRun, foldTr, foldTd);
          foldTr.tourRun = foldRun;
          foldRows.push(foldTr);
        }
        tr.style.display = 'none';
        foldRun.push(tr);
      } else {
        foldRun = null;
      }
      if (mode === 'diff') tr.appendChild(el('td', 'ln old', r.old != null ? String(r.old) : ''));
      var tdNew = el('td', 'ln new');
      if (r.new != null) {
        var a = el('a', null, String(r.new));
        a.href = 'https://github.com/' + repo + '/blob/' + head + '/' + file + '#L' + r.new;
        tdNew.appendChild(a);
      }
      tr.appendChild(tdNew);
      if (mode !== 'added') tr.appendChild(el('td', 'mk', r.tag === ' ' ? '' : r.tag));
      var code = el('td', 'code');
      if (isPython) {
        var pythonCode = el('code', 'language-python', r.text);
        code.appendChild(pythonCode);
        window.hljs.highlightElement(pythonCode);
      } else {
        code.textContent = r.text;
      }
      tr.appendChild(code);
      var d = r.tag === '+' && DEF_RE.exec(r.text);
      if (d) (defs[d[1]] = defs[d[1]] || []).push(code);
      tbody.appendChild(tr);
      prev = r;
    });
    foldRows.forEach(function (f) { f.tourLabel(); });
    if (prev && !prev.fromBox) box.tourLast = { old: prev.old, new: prev.new, at: prev.at, tag: prev.tag, fromBox: true };
    table.appendChild(tbody);
    hunk.appendChild(table);
    box.appendChild(hunk);
    return box;
  }

  // Cross-links: every added ``def NAME`` on this page is an anchor, and every
  // exact-token occurrence of NAME elsewhere links to it.  Deliberately dumb:
  // no scoping, no resolution; a name added more than once on the page is
  // ambiguous and left alone.
  var FOLD = 3;
  var DEF_RE = /^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)/;
  var IDENT_RE = /[A-Za-z_]\w*/g;
  var defs = {};

  function crosslink() {
    var names = {};
    Object.keys(defs).forEach(function (n) {
      if (defs[n].length !== 1) return;
      var td = defs[n][0], m = DEF_RE.exec(td.textContent), at = m[0].length - n.length;
      var highlightedName = td.querySelector('.hljs-title.function_');
      if (highlightedName && highlightedName.textContent === n) {
        highlightedName.classList.add('def');
        highlightedName.id = 'def-' + n;
      } else {
        var span = el('span', 'def', n);
        span.id = 'def-' + n;
        td.textContent = '';
        td.appendChild(document.createTextNode(m[0].slice(0, at)));
        td.appendChild(span);
        td.appendChild(document.createTextNode(m.input.slice(m[0].length)));
      }
      names[n] = '#def-' + n;
    });
    if (!Object.keys(names).length) return;

    function linkText(node) {
      var parent = node.parentNode;
      if (parent.closest && parent.closest('.hljs-string, .hljs-comment, .def')) return;
      var text = node.nodeValue, frag = null, last = 0, m;
      IDENT_RE.lastIndex = 0;
      while ((m = IDENT_RE.exec(text))) {
        var n = m[0];
        if (!names[n]) continue;
        // ``def NAME`` is a definition site (the anchor itself, or a deleted
        // or duplicate def), not a reference.
        if (/\bdef\s+$/.test(text.slice(0, m.index))) continue;
        frag = frag || document.createDocumentFragment();
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var a = el('a', 'ref', n);
        a.href = names[n];
        frag.appendChild(a);
        last = m.index + n.length;
      }
      if (!frag) return;
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }

    function linkCode(node) {
      Array.prototype.slice.call(node.childNodes).forEach(function (child) {
        if (child.nodeType === 3) {
          linkText(child);
        } else if (child.nodeType === 1 && child.tagName !== 'A'
          && !child.classList.contains('hljs-string')
          && !child.classList.contains('hljs-comment')
          && !child.classList.contains('def')) {
          linkCode(child);
        }
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll('.tour tr:not(.sep) td.code'), function (td) {
      linkCode(td);
    });
    // Prose: <code>NAME</code> or <code>NAME()</code>, unless already a link.
    Array.prototype.forEach.call(document.querySelectorAll('code'), function (c) {
      if (c.closest('a, pre, .tour')) return;
      var m = /^([A-Za-z_]\w*)(\(\))?$/.exec(c.textContent);
      if (!m || !names[m[1]]) return;
      var a = el('a', 'ref');
      a.href = names[m[1]];
      c.parentNode.insertBefore(a, c);
      a.appendChild(c);
    });
  }

  // Two hunks are adjacent when they share a parent and only whitespace and
  // comments sit between them. Do not group across section/container boundaries.
  function adjacent(a, b) {
    if (a.parentNode !== b.parentNode) return false;
    if (a.getAttribute('data-file') !== b.getAttribute('data-file')) return false;
    for (var n = a.nextSibling; n && n !== b; n = n.nextSibling) {
      if (n.nodeType === 1 || (n.nodeType === 3 && n.textContent.trim())) return false;
    }
    return n === b;
  }

  function renderAll() {
    var pres = Array.prototype.slice.call(document.querySelectorAll('pre.hunk'));
    for (var i = 0; i < pres.length;) {
      var j = i;
      while (j + 1 < pres.length && adjacent(pres[j], pres[j + 1])) j++;
      var box = null, grouped = j > i;
      // Grouped hunks share one box, so they must share one gutter layout: if
      // any member is a diff hunk, render the whole group in diff mode.
      var mode = 'added';
      for (var k = i; k <= j; k++) if ((pres[k].getAttribute('data-render') || 'diff') === 'diff') mode = 'diff';
      for (var k = i; k <= j; k++) box = render(pres[k], box, grouped, mode);
      i = j + 1;
    }
  }

  // <pre class="source">: unchanged source written by sync as "@@ N" runs.
  // Rendered like a hunk, without diff colouring, "⋯" between runs.
  function renderSource(pre) {
    var file = pre.getAttribute('data-file') || '', symbol = pre.getAttribute('data-symbol') || '';
    var slug = pre.getAttribute('data-repo') || repo, rev = pre.getAttribute('data-rev') || head;
    var blob = 'https://github.com/' + slug + '/blob/' + rev + '/' + file;
    var box = el('div', 'tour source'), headRow = el('div', 'tour-head');
    var fileLink = el('a', null, (slug === repo ? '' : slug.split('/').pop() + ': ') + file);
    fileLink.href = blob;
    headRow.appendChild(fileLink);
    headRow.appendChild(el('span', 'sym', symbol));
    box.appendChild(headRow);
    var scroll = el('div', 'tour-scroll'), table = el('table'), tbody = el('tbody'), n = 0;
    pre.textContent.replace(/^\n/, '').replace(/\n$/, '').split('\n').forEach(function (line) {
      var m = /^@@ (\d+)$/.exec(line);
      if (m) {
        if (n) {
          var sep = el('tr', 'sep');
          sep.appendChild(el('td', 'ln'));
          sep.appendChild(el('td', 'code', '⋯'));
          tbody.appendChild(sep);
        }
        n = +m[1];
        return;
      }
      var tr = el('tr', 'ctx'), ln = el('td', 'ln'), a = el('a', null, String(n));
      a.href = blob + '#L' + n++;
      ln.appendChild(a);
      tr.appendChild(ln);
      var code = el('td', 'code'), c = el('code', 'language-python', line);
      code.appendChild(c);
      window.hljs.highlightElement(c);
      tr.appendChild(code);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    scroll.appendChild(table);
    box.appendChild(scroll);
    pre.insertAdjacentElement('afterend', box);
  }

  // <div class="scroll-box" data-show="N">: the box of hunks inside shows the
  // first N hunks and scrolls for the rest, file header pinned.
  function scrollBoxes() {
    Array.prototype.forEach.call(document.querySelectorAll('.scroll-box'), function (wrap) {
      var box = wrap.querySelector('.tour'), n = +wrap.getAttribute('data-show') || 5;
      var hunks = box && box.querySelectorAll('.tour-hunk');
      if (!hunks || hunks.length <= n) return;
      box.classList.add('scrolling');
      // .scrolling makes the box position: relative, so offsetTop is box-relative.
      box.style.maxHeight = hunks[n].offsetTop + 'px';
    });
  }

  // <div class="pair">: rule hunk on the left, source on the right, the
  // rule sized to fit.  The pair
  // is widened past the text column toward the window edge, and the source
  // scrolls inside the height of the left box, or of the reference rule box
  // (_linear_with_frozen_weight) when the left one is shorter.
  var PAIR_REF = 'megatron/core/spmd/spmd_rules.py::_linear_with_frozen_weight';
  var PAIR_MAX = 1400;
  var PAIR_LEFT = 0.6;

  function layoutPairs() {
    var pairs = document.querySelectorAll('.pair');
    // Center each pair on the text column, clamped to the space right of the
    // TOC sidebar (when shown) and the window edge.
    var toc = document.querySelector('nav.toc');
    var tocRect = toc && toc.getBoundingClientRect();
    var lo = (tocRect && tocRect.width ? tocRect.right : 0) + 16;
    var hi = document.documentElement.clientWidth - 16;
    Array.prototype.forEach.call(pairs, function (pair) {
      pair.style.width = pair.style.marginLeft = '';
      var r = pair.getBoundingClientRect();
      var w = Math.max(r.width, Math.min(hi - lo, PAIR_MAX));
      var x = Math.min(Math.max(r.left + r.width / 2 - w / 2, lo), hi - w);
      pair.style.width = w + 'px';
      pair.style.marginLeft = Math.min(0, x - r.left) + 'px';
    });
    // One left width for every pair: the widest rule, so it need not scroll,
    // up to PAIR_LEFT of the pair.  The source takes the rest and scrolls.
    var lefts = [];
    Array.prototype.forEach.call(pairs, function (pair) {
      var box = pair.querySelector(':scope > .tour:not(.source)');
      if (!box) return;
      pair.style.gridTemplateColumns = '';
      box.style.width = 'max-content';
      lefts.push(box.offsetWidth + 2);
      box.style.width = '';
    });
    var leftW = Math.max.apply(null, lefts.concat(0));
    Array.prototype.forEach.call(pairs, function (pair) {
      var w = Math.min(leftW, pair.offsetWidth * PAIR_LEFT);
      pair.style.gridTemplateColumns = w + 'px minmax(0, 1fr)';
    });
    var ref = document.getElementById(PAIR_REF);
    var refBox = ref && ref.closest('.tour');
    Array.prototype.forEach.call(pairs, function (pair) {
      var boxes = pair.querySelectorAll(':scope > .tour');
      var src = pair.querySelector(':scope > .tour.source');
      if (boxes.length < 2 || !src) return;
      var target = Math.max(boxes[0].offsetHeight, refBox ? refBox.offsetHeight : 0);
      var scroll = src.querySelector('.tour-scroll');
      scroll.style.maxHeight = (target - (src.offsetHeight - scroll.offsetHeight) - 2) + 'px';
    });
  }

  function renderSources() {
    Array.prototype.forEach.call(document.querySelectorAll('pre.source'), renderSource);
    layoutPairs();
    window.addEventListener('resize', layoutPairs);
  }

  function slug(s) {
    return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  function toc() {
    // Headings and hunks inside the collapsed appendix stay out of the sidebar.
    var heads = Array.prototype.filter.call(
      document.querySelectorAll('h2, h3, h4'),
      function (h) { return !h.closest('details.appendix'); }
    );
    if (!heads.length) return;
    var nav = el('nav', 'toc'), list = el('ul'), items = [];
    var h1 = document.querySelector('h1');
    if (h1) {
      var topA = el('a', 'toc-title', h1.textContent);
      topA.href = '#';
      nav.appendChild(topA);
    }

    Array.prototype.forEach.call(heads, function (h) {
      // h2 links to its section; a subsection heading is its own target.
      var target = (h.tagName === 'H2' && h.closest('section[id]')) || h;
      if (!target.id) {
        var id = slug(h.textContent), n = 1;
        while (document.getElementById(id)) id = slug(h.textContent) + '-' + (++n);
        target.id = id;
      }
      var li = el('li', 'toc-' + h.tagName.toLowerCase()), a = el('a', null, h.textContent);
      a.href = '#' + target.id;
      li.appendChild(a);
      list.appendChild(li);
      items.push({ target: target, li: li });
    });

    // A hunk belongs to the nearest h3/h4 before it in its section, else the
    // section itself.
    function headingOf(pre) {
      for (var n = pre.previousElementSibling; n; n = n.previousElementSibling) {
        if ((n.tagName === 'H3' || n.tagName === 'H4') && n.id) return n;
      }
      return pre.closest('section[id]');
    }

    // Per file: the first hunk that appears under each heading. Appendix hunks
    // stay out of the sidebar.
    var sectionsOf = {};
    Array.prototype.forEach.call(document.querySelectorAll('pre.hunk'), function (pre) {
      if (pre.closest('details.appendix')) return;
      var file = pre.getAttribute('data-file');
      var sec = headingOf(pre);
      if (!file || !sec) return;
      var s = sectionsOf[file] = sectionsOf[file] || {};
      s[sec.id] = s[sec.id] || pre;
    });

    items.forEach(function (it) {
      var files = Object.keys(sectionsOf).filter(function (f) { return sectionsOf[f][it.target.id]; }).sort();
      if (!files.length) return;
      var ul = el('ul', 'toc-files');
      files.forEach(function (f) {
        var li = el('li'), a = el('a', 'toc-file');
        var parts = f.split('/');
        a.appendChild(el('span', 'dir', parts.length > 1 ? parts[parts.length - 2] + '/' : ''));
        a.appendChild(el('span', 'base', parts[parts.length - 1]));
        a.title = f;
        var first = sectionsOf[f][it.target.id];
        a.href = '#' + first.getAttribute('data-file') + '::' + first.getAttribute('data-symbol');
        li.appendChild(a);
        ul.appendChild(li);
      });
      it.li.appendChild(ul);
    });
    nav.appendChild(list);
    document.body.appendChild(nav);

    var current = null;
    function update() {
      var y = window.scrollY + 40, pick = null;
      items.forEach(function (it) { if (it.target.offsetTop <= y) pick = it; });
      if (pick === current) return;
      if (current) current.li.classList.remove('active');
      current = pick;
      if (current) current.li.classList.add('active');
    }
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    update();
  }

  // <details class="appendix">: collapsed by default; its <summary> carries a
  // diffstat of the hunks inside, and any link into it opens it first.
  function appendix() {
    Array.prototype.forEach.call(document.querySelectorAll('details.appendix'), function (det) {
      var summary = det.querySelector('summary') || det.insertBefore(el('summary'), det.firstChild);
      var files = {}, order = [], hunks = 0, adds = 0, dels = 0;
      Array.prototype.forEach.call(det.querySelectorAll('pre.hunk'), function (pre) {
        var file = pre.getAttribute('data-file') || '';
        if (!files[file]) { files[file] = { add: 0, del: 0, hunks: 0 }; order.push(file); }
        files[file].hunks++; hunks++;
        parse(pre.textContent).forEach(function (r) {
          if (r.sep) return;
          if (r.tag === '+') { files[file].add++; adds++; }
          if (r.tag === '-') { files[file].del++; dels++; }
        });
      });
      summary.textContent = '';
      summary.appendChild(el('span', 'appendix-title', hunks + ' more hunk' + (hunks === 1 ? '' : 's') + ' in ' + order.length + ' file' + (order.length === 1 ? '' : 's')));
      summary.appendChild(el('span', 'add', '+' + adds));
      summary.appendChild(el('span', 'del', '\u2212' + dels));
      var stat = el('table', 'diffstat'), tb = el('tbody'), max = 1;
      order.forEach(function (f) { max = Math.max(max, files[f].add + files[f].del); });
      order.sort().forEach(function (f) {
        var st = files[f], tr = el('tr');
        var a = el('a', 'file', f);
        var first = det.querySelector('pre.hunk[data-file="' + f.replace(/"/g, '\\"') + '"]');
        a.href = '#' + f + '::' + first.getAttribute('data-symbol');
        tr.appendChild(el('td', 'file')).appendChild(a);
        tr.appendChild(el('td', 'n', st.hunks));
        tr.appendChild(el('td', 'n add', '+' + st.add));
        tr.appendChild(el('td', 'n del', '\u2212' + st.del));
        var bar = el('td', 'bar'), w = 40 * (st.add + st.del) / max;
        var ba = el('span', 'add'), bd = el('span', 'del');
        ba.style.width = (w * st.add / (st.add + st.del || 1)) + 'px';
        bd.style.width = (w * st.del / (st.add + st.del || 1)) + 'px';
        bar.appendChild(ba); bar.appendChild(bd);
        tr.appendChild(bar);
        tb.appendChild(tr);
      });
      stat.appendChild(tb);
      // Outside the <details>, so it is visible while the details are closed.
      det.insertAdjacentElement('afterend', stat);
    });

    function reveal() {
      if (!location.hash) return;
      var id;
      try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { id = location.hash.slice(1); }
      var target = document.getElementById(id);
      if (!target) return;
      var det = target.closest('details.appendix');
      if (det && !det.open) { det.open = true; target.scrollIntoView(); }
    }
    window.addEventListener('hashchange', reveal);
    reveal();
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderAll();
    renderSources();
    scrollBoxes();
    crosslink();
    toc();
    layoutPairs();
    appendix();
  });
})();
