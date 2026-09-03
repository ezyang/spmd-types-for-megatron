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
    var rows = [], o = 0, n = 0, first = true;
    text = text.replace(/^\n/, '').replace(/\n$/, '');
    text.split('\n').forEach(function (line) {
      var m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/.exec(line);
      if (m) {
        o = +m[1]; n = +m[2];
        if (!first) rows.push({ sep: true, text: m[3] });
        first = false;
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

  function render(pre) {
    var file = pre.getAttribute('data-file') || '', symbol = pre.getAttribute('data-symbol') || '';
    var mode = pre.getAttribute('data-render') || 'diff';
    var only = lineSet(pre.getAttribute('data-lines'));
    var collapse = pre.hasAttribute('data-collapse');
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

    var box = el('div', 'tour');
    box.id = file + '::' + symbol;
    var headRow = el('div', 'tour-head');
    var fileLink = el('a', null, file);
    fileLink.href = compare;
    headRow.appendChild(fileLink);
    headRow.appendChild(el('span', 'sym', symbol));
    var blob = el('a', 'sym', 'view file');
    blob.href = 'https://github.com/' + repo + '/blob/' + head + '/' + file;
    headRow.appendChild(blob);
    box.appendChild(headRow);
    sha256hex(file).then(function (h) { if (h) fileLink.href = compare + '#diff-' + h; });

    var table = el('table'), tbody = el('tbody');
    var prev = null, pendingSep = null;
    kept.forEach(function (r) {
      if (r.sep) { pendingSep = r; return; }
      var gap = prev && !((r.old != null && prev.old != null && r.old === prev.old + 1) || (r.new != null && prev.new != null && r.new === prev.new + 1) || (r.at != null && r.at === prev.at) || (prev.tag === '-' && r.new === prev.at));
      if (pendingSep || gap) {
        var sep = el('tr', 'sep'), td = el('td', null, pendingSep && pendingSep.text ? '\u22ef ' + pendingSep.text : '\u22ef');
        td.colSpan = 4;
        sep.appendChild(td);
        tbody.appendChild(sep);
        pendingSep = null;
      }
      var tr = el('tr');
      if (mode !== 'added') tr.className = r.tag === '+' ? 'add' : r.tag === '-' ? 'del' : 'ctx';
      if (mode === 'diff') tr.appendChild(el('td', 'ln old', r.old != null ? String(r.old) : ''));
      var tdNew = el('td', 'ln new');
      if (r.new != null) {
        var a = el('a', null, String(r.new));
        a.href = 'https://github.com/' + repo + '/blob/' + head + '/' + file + '#L' + r.new;
        tdNew.appendChild(a);
      }
      tr.appendChild(tdNew);
      if (mode !== 'added') tr.appendChild(el('td', 'mk', r.tag === ' ' ? '' : r.tag));
      tr.appendChild(el('td', 'code', r.text));
      tbody.appendChild(tr);
      prev = r;
    });
    table.appendChild(tbody);
    box.appendChild(table);
    pre.insertAdjacentElement('afterend', box);
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('pre.hunk'), render);
  });
})();
