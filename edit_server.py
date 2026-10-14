#!/usr/bin/env python3
"""Serve the tour with inline prose editing.

    python3 edit_server.py [--port 8001]

Serves this directory. Every *.html page gets a small editor script injected,
and each prose block (p, li, h1-h6, summary, td, th) outside a hunk is tagged with its
ordinal in the source file. Double-click a block to edit it in place;
Ctrl/Cmd+Enter or clicking away saves, Esc cancels. Alt+double-click edits the
raw HTML instead. Enter starts a new paragraph (or list item); pasting inserts
plain text. Saving rewrites only that element in the file on disk, and is
refused if the element changed on disk since the page loaded.
The page reloads itself when the file changes underneath it (e.g. tour.py sync).

Select any text and click "Comment" (or Ctrl/Cmd+Alt+M) to start a thread;
threads sit in a panel on the right, take replies, and can be resolved. They are
anchored by the quoted text and its surroundings, stored in PAGE.comments.json
next to the page, and never touch the HTML. --no-edit serves comments only, for
reviewers who should not edit the prose.

Standard library only; the files on disk stay plain HTML.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "summary", "td", "th"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr"}



def version(path: str) -> int:
    """Newest mtime of the page and the renderer, so either change reloads it."""
    here = os.path.dirname(path)
    return max(os.stat(p).st_mtime_ns for p in [path] + [os.path.join(here, a) for a in ("tour.js", "tour.css")]
               if os.path.exists(p))


class Blocks(HTMLParser):
    """Record (start_tag_start, start_tag_end, end_tag_start) for each prose block."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_offsets = [0]
        for m in re.finditer("\n", text):
            self.line_offsets.append(m.end())
        self.stack: list[tuple[str, int, int, bool]] = []
        self.spans: list[tuple[int, int, int]] = []
        self.in_pre = 0
        self.feed(text)
        self.close()

    def src_offset(self) -> int:
        line, col = self.getpos()
        return self.line_offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        start = self.src_offset()
        end = start + len(self.get_starttag_text())
        if tag == "pre":
            self.in_pre += 1
        self.stack.append((tag, start, end, tag in BLOCKS and not self.in_pre))

    def handle_endtag(self, tag):
        pos = self.src_offset()
        while self.stack:
            t, start, end, keep = self.stack.pop()
            if t == "pre":
                self.in_pre -= 1
            if keep:
                self.spans.append((start, end, pos))
            if t == tag:
                break


def spans(text: str) -> list[tuple[int, int, int]]:
    return sorted(Blocks(text).spans)


def page_path(name: str) -> str | None:
    name = os.path.basename(name)
    path = os.path.join(ROOT, name)
    return path if name.endswith(".html") and os.path.isfile(path) else None


def comments_path(page: str) -> str:
    return os.path.splitext(page)[0] + ".comments.json"


def load_comments(page: str) -> dict:
    try:
        with open(comments_path(page), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"version": 0, "threads": []}


COMMENTS_LOCK = threading.Lock()


def update_comments(page: str, body: dict) -> dict:
    """Apply one comment action to the page's thread file and return the new state."""
    def text(key: str, limit: int) -> str:
        value = str(body.get(key) or "").strip()
        if len(value) > limit:
            raise ValueError(f"{key} is too long")
        return value

    def comment() -> dict:
        content = text("body", 10000)
        if not content:
            raise ValueError("comment is empty")
        return {"id": uuid.uuid4().hex[:8], "name": text("name", 80),
                "body": content, "time": int(time.time() * 1000)}

    with COMMENTS_LOCK:
        data = load_comments(page)
        threads = {t["id"]: t for t in data["threads"]}
        action = body.get("action")
        if action == "new":
            quote = str(body.get("quote") or "")
            if not quote.strip():
                raise ValueError("nothing selected")
            data["threads"].append({"id": uuid.uuid4().hex[:8], "quote": quote,
                                    "prefix": str(body.get("prefix") or ""),
                                    "suffix": str(body.get("suffix") or ""),
                                    "resolved": False, "comments": [comment()]})
        elif body.get("thread") not in threads:
            raise LookupError("thread not found; reload")
        else:
            thread = threads[body["thread"]]
            if action == "reply":
                thread["comments"].append(comment())
            elif action == "resolve":
                thread["resolved"] = bool(body.get("resolved"))
            elif action == "delete":
                thread["comments"] = [c for c in thread["comments"] if c["id"] != body.get("comment")]
                if not thread["comments"]:
                    data["threads"].remove(thread)
            else:
                raise ValueError(f"unknown action {action!r}")
        data["version"] += 1
        tmp = comments_path(page) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, comments_path(page))
        return data


def tagged(text: str, edit: bool = True) -> str:
    out, last = [], 0
    for i, (start, end, _) in enumerate(spans(text)):
        close = end - 2 if text[end - 2] == "/" else end - 1
        out.append(text[last:close])
        out.append(f' data-src-id="{i}"')
        last = close
    out.append(text[last:])
    html = "".join(out)
    inject = ('<script src="/__edit.js" defer></script>\n' if edit else "") + \
        '<script src="/__comments.js" defer></script>\n'
    i = html.lower().rfind("</head>")
    return html[:i] + inject + html[i:] if i >= 0 else inject + html


EDIT_JS = r"""
(function () {
  var page = location.pathname.split('/').pop() || 'index.html';
  var mtime = null, editing = null;
  window.__inlineEditing = true;
  var css = document.createElement('style');
  css.textContent =
    '[data-src-id]:hover{outline:1px dashed #bbb;outline-offset:2px;cursor:text}' +
    '[data-src-id][contenteditable]{outline:2px solid #4a90e2!important;outline-offset:2px;background:#f7fbff}' +
    'textarea.__raw{width:100%;min-height:8em;font:13px/1.4 monospace;box-sizing:border-box}' +
    '#__toast{position:fixed;bottom:12px;right:12px;padding:6px 10px;border-radius:4px;' +
    'font:13px sans-serif;color:#fff;background:#333;opacity:0;transition:opacity .3s;z-index:9999}';
  document.head.appendChild(css);
  var toast = document.createElement('div'); toast.id = '__toast';
  document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(toast); });
  function say(msg, bad) {
    toast.textContent = msg; toast.style.background = bad ? '#c0392b' : '#27ae60';
    toast.style.opacity = 1; clearTimeout(say.t);
    say.t = setTimeout(function () { toast.style.opacity = 0; }, bad ? 6000 : 1500);
  }
  function api(method, body) {
    return fetch('/__src?page=' + encodeURIComponent(page) + (body ? '' : '&id=' + editing.id), {
      method: method, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json().then(function (j) { j.status = r.status; return j; }); });
  }

  document.addEventListener('dblclick', function (e) {
    if (editing) return;
    var node = e.target.closest('[data-src-id]');
    if (!node || e.target.closest('pre, .tour')) return;
    e.preventDefault();
    var raw = e.altKey;
    editing = {node: node, id: node.getAttribute('data-src-id'), rendered: node.innerHTML, raw: raw};
    api('GET').then(function (j) {
      if (j.status !== 200) { say(j.error, true); editing = null; return; }
      editing.orig = j.inner;
      if (raw) {
        var ta = document.createElement('textarea'); ta.className = '__raw'; ta.value = j.inner;
        node.innerHTML = ''; node.appendChild(ta); node.setAttribute('contenteditable', 'false');
        ta.addEventListener('blur', function () { finish(true); });
        ta.addEventListener('keydown', keys); ta.focus();
      } else {
        node.innerHTML = j.inner; node.setAttribute('contenteditable', 'true');
        document.execCommand('defaultParagraphSeparator', false, 'div');
        node.addEventListener('blur', onBlur); node.addEventListener('keydown', keys);
        node.addEventListener('paste', onPaste); node.focus();
      }
    });
  });
  function onBlur() { finish(true); }
  function onPaste(e) {
    e.preventDefault();
    document.execCommand('insertText', false, e.clipboardData.getData('text/plain'));
  }
  // contenteditable writes Enter as <div>s, pastes styles, and uses &nbsp;. Turn the
  // edited element back into plain HTML: one string per block the user typed.
  function blocks(node) {
    var out = [], cur = document.createElement('div');
    function flush() {
      var html = clean(cur);
      if (html) out.push(html);
      cur = document.createElement('div');
    }
    Array.prototype.slice.call(node.childNodes).forEach(function (c) {
      if (c.nodeType === 1 && (c.tagName === 'DIV' || c.tagName === 'P')) {
        flush(); cur.innerHTML = c.innerHTML; flush();
      } else cur.appendChild(c.cloneNode(true));
    });
    flush();
    return out.length ? out : [''];
  }
  function clean(el) {
    Array.prototype.forEach.call(el.querySelectorAll('[style]'), function (e) { e.removeAttribute('style'); });
    Array.prototype.forEach.call(el.querySelectorAll('span:not([class]):not([id])'), function (e) {
      e.replaceWith.apply(e, Array.prototype.slice.call(e.childNodes));
    });
    Array.prototype.forEach.call(el.querySelectorAll('a[href]'), function (a) {
      var h = a.getAttribute('href');
      if (h.indexOf(location.origin + '/') === 0) a.setAttribute('href', h.slice(location.origin.length).replace(/^\/(index\.html)?(?=#)/, ''));
    });
    return el.innerHTML.replace(/\u00a0|&nbsp;/g, ' ').replace(/(\s|<br>)+$/, '').replace(/^(\s|<br>)+/, '');
  }
  function keys(e) {
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
  }
  function finish(save) {
    var ed = editing; if (!ed || ed.done) return; ed.done = true;
    var node = ed.node;
    node.removeEventListener('blur', onBlur); node.removeEventListener('keydown', keys);
    node.removeEventListener('paste', onPaste);
    var parts = ed.raw ? [node.querySelector('textarea.__raw').value] : blocks(node);
    var inner = parts.join('');
    node.removeAttribute('contenteditable');
    function restore() {
      node.innerHTML = ed.rendered; editing = null;
      if (window.__comments) window.__comments.render();
    }
    if (!save || (parts.length === 1 && inner === ed.orig)) { restore(); return; }
    api('POST', {id: ed.id, orig: ed.orig, blocks: parts}).then(function (j) {
      if (j.status !== 200) { say(j.error, true); restore(); return; }
      if (parts.length > 1) { sessionStorage.setItem('__scroll', String(scrollY)); location.reload(); return; }
      mtime = j.mtime; node.innerHTML = inner; editing = null; say('saved');
      if (window.__comments) window.__comments.render();
    }, function (err) { say('save failed: ' + err, true); restore(); });
  }

  // Reload when the file changes on disk from something other than our own saves.
  setInterval(function () {
    if (editing) return;
    fetch('/__mtime?page=' + encodeURIComponent(page)).then(function (r) { return r.json(); }).then(function (j) {
      if (mtime === null) mtime = j.mtime;
      else if (j.mtime !== mtime) {
        sessionStorage.setItem('__scroll', String(scrollY)); location.reload();
      }
    }, function () {});
  }, 1500);
  window.addEventListener('load', function () {
    var y = sessionStorage.getItem('__scroll');
    if (y !== null) { sessionStorage.removeItem('__scroll'); scrollTo(0, +y); }
  });
})();
"""

COMMENTS_JS = r"""
(function () {
  var page = location.pathname.split('/').pop() || 'index.html';
  var data = {version: -1, threads: []}, showResolved = false, active = null, draft = null, stale = false;
  var panel, list, fab, nameInput, timer;
  var css = document.createElement('style');
  css.textContent = [
    'mark.__c{background:#fff3b0;color:inherit;cursor:pointer;border-bottom:2px solid #f2c94c}',
    'mark.__c.__active{background:#ffd966}',
    'mark.__c.__draft{background:#cfe3ff;border-bottom-color:#4a90e2}',
    '#__cpanel{position:fixed;top:1em;right:1em;bottom:4em;width:21em;overflow-y:auto;z-index:9000;box-sizing:border-box;' +
      'font:13px/1.4 Helvetica,Arial,sans-serif;color:#222;background:#fafafa;border:1px solid #ddd;' +
      'border-radius:6px;padding:8px;box-shadow:0 2px 8px rgba(0,0,0,.08)}',
    '#__cpanel[hidden],#__cpop[hidden]{display:none}',
    '.__chead{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}',
    '.__chead b{flex:1}',
    'input.__cname{width:100%;box-sizing:border-box;padding:3px 5px;font:inherit}',
    '.__card{background:#fff;border:1px solid #e3e3e3;border-radius:6px;padding:8px;margin-bottom:8px;cursor:pointer}',
    '.__card.__active{border-color:#f2c94c;box-shadow:0 0 0 2px #fff3b0}',
    '.__card.__resolved{opacity:.6}',
    '.__cquote{color:#777;border-left:3px solid #f2c94c;padding-left:6px;margin-bottom:6px;font-style:italic;' +
      'max-height:4.2em;overflow:hidden;white-space:pre-wrap}',
    '.__card.__orphan .__cquote{border-left-color:#c0392b}',
    '.__cmsg{margin:6px 0}',
    '.__cmeta{color:#888;font-size:11px}',
    '.__cmeta b{color:#222;font-size:13px;margin-right:6px}',
    '.__cbody{white-space:pre-wrap;word-wrap:break-word}',
    '.__cdel{float:right;border:0;background:none;color:#aaa;cursor:pointer;font-size:15px;line-height:1;padding:0 2px}',
    '.__cdel:hover{color:#c0392b}',
    '.__card textarea{width:100%;box-sizing:border-box;min-height:2.6em;font:inherit;padding:4px;resize:vertical;margin-top:4px}',
    '.__cbtns{display:flex;gap:6px;justify-content:flex-end;margin-top:4px}',
    '.__cbtns button,#__cpop,#__cfab{font:12px Helvetica,Arial,sans-serif;border:1px solid #ccc;background:#fff;' +
      'border-radius:4px;padding:3px 9px;cursor:pointer;color:#222}',
    '.__cbtns button.__primary{background:#4a90e2;border-color:#4a90e2;color:#fff}',
    '#__cpop{position:absolute;z-index:9001;box-shadow:0 2px 6px rgba(0,0,0,.2)}',
    '#__cfab{position:fixed;right:1em;bottom:1em;z-index:9001;padding:6px 12px;font-size:13px}',
    '.__cempty{color:#888;padding:4px}'
  ].join('\n');
  document.head.appendChild(css);

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function ui(node) { return node.closest('#__cpanel, #__cpop, #__cfab, #__toast'); }
  function each(sel, fn) { Array.prototype.forEach.call(document.querySelectorAll(sel), fn); }

  // The page's text as one string over its text nodes. A thread stores its quote
  // with some text either side, and is found again by searching this string, so
  // it survives edits elsewhere and re-rendering.
  function flatten() {
    var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var nodes = [], starts = [], parts = [], len = 0, n;
    while ((n = walk.nextNode())) {
      var p = n.parentElement;
      if (!p || p.closest('script, style, nav.toc') || ui(p)) continue;
      nodes.push(n); starts.push(len); parts.push(n.data); len += n.data.length;
    }
    return {nodes: nodes, starts: starts, text: parts.join('')};
  }
  function anchorOf(range) {
    var f = flatten(), first = -1, last = -1;
    for (var i = 0; i < f.nodes.length; i++) {
      if (range.intersectsNode(f.nodes[i])) { if (first < 0) first = i; last = i; }
    }
    if (first < 0) return null;
    var s = f.starts[first] + (f.nodes[first] === range.startContainer ? range.startOffset : 0);
    var e = f.starts[last] + (f.nodes[last] === range.endContainer ? range.endOffset : f.nodes[last].data.length);
    while (s < e && /\s/.test(f.text[s])) s++;
    while (e > s && /\s/.test(f.text[e - 1])) e--;
    if (s >= e) return null;
    return {quote: f.text.slice(s, e), prefix: f.text.slice(Math.max(0, s - 40), s), suffix: f.text.slice(e, e + 40)};
  }
  function common(a, b, fromEnd) {
    var n = 0;
    while (n < a.length && n < b.length &&
           (fromEnd ? a[a.length - 1 - n] === b[b.length - 1 - n] : a[n] === b[n])) n++;
    return n;
  }
  // The occurrence of the quote whose surroundings match best, or -1.
  function locate(f, t) {
    var best = -1, score = -1, i = f.text.indexOf(t.quote);
    while (i >= 0) {
      var end = i + t.quote.length;
      var sc = common(f.text.slice(Math.max(0, i - t.prefix.length), i), t.prefix, true) +
               common(f.text.slice(end, end + t.suffix.length), t.suffix, false);
      if (sc > score) { best = i; score = sc; }
      i = f.text.indexOf(t.quote, i + 1);
    }
    return best;
  }
  function wrap(f, start, end, cls, id) {
    for (var i = 0; i < f.nodes.length; i++) {
      var node = f.nodes[i], ns = f.starts[i], ne = ns + node.data.length;
      if (ne <= start) continue;
      if (ns >= end) break;
      var a = Math.max(start, ns) - ns, b = Math.min(end, ne) - ns;
      // Whitespace between table cells or list items cannot hold a <mark>.
      if (!/\S/.test(node.data.slice(a, b))) continue;
      if (b < node.data.length) node.splitText(b);
      var target = a > 0 ? node.splitText(a) : node;
      var m = el('mark', cls);
      m.setAttribute('data-thread', id);
      target.parentNode.insertBefore(m, target);
      m.appendChild(target);
    }
  }
  function unwrap() {
    each('mark.__c', function (m) {
      var p = m.parentNode;
      if (!p) return;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
      p.normalize();
    });
  }

  // Re-rendering rebuilds the panel, which would drop a half-typed comment.
  function busy() {
    return document.querySelector('[contenteditable="true"], textarea.__raw') ||
      Array.prototype.some.call(panel.querySelectorAll('textarea'), function (t) {
        return t.value.trim() || t === document.activeElement;
      });
  }
  function render() {
    if (!panel) return;
    stale = false;
    unwrap();
    var f = flatten();
    data.threads.forEach(function (t) {
      t.pos = locate(f, t);
      if (t.pos >= 0 && (!t.resolved || showResolved)) {
        wrap(f, t.pos, t.pos + t.quote.length, '__c' + (t.id === active ? ' __active' : ''), t.id);
        f = flatten();
      }
    });
    if (draft) {
      var i = locate(f, draft.anchor);
      if (i >= 0) wrap(f, i, i + draft.anchor.quote.length, '__c __draft', '');
    }
    var shown = data.threads.filter(function (t) { return !t.resolved || showResolved; });
    shown.sort(function (a, b) { return (a.pos < 0) - (b.pos < 0) || a.pos - b.pos; });
    fab.textContent = 'Comments (' + data.threads.filter(function (t) { return !t.resolved; }).length + ')';
    list.textContent = '';
    if (draft) list.appendChild(draftCard());
    if (!shown.length && !draft) list.appendChild(el('div', '__cempty', 'Select text in the page and click "Comment".'));
    shown.forEach(function (t) { list.appendChild(card(t)); });
  }

  function when(ms) {
    return new Date(ms).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  }
  function name() {
    var v = nameInput.value.trim();
    localStorage.setItem('__comment_name', v);
    return v;
  }
  function keys(ta, ok, cancel) {
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ok.click(); }
      else if (e.key === 'Escape' && cancel) { e.preventDefault(); cancel.click(); }
    });
  }
  function buttons(c, ta, labels) {
    var row = el('div', '__cbtns'), out = labels.map(function (l, i) {
      var b = el('button', i === labels.length - 1 ? '__primary' : null, l);
      row.appendChild(b);
      return b;
    });
    c.appendChild(ta); c.appendChild(row);
    return out;
  }
  function card(t) {
    var c = el('div', '__card' + (t.resolved ? ' __resolved' : '') + (t.pos < 0 ? ' __orphan' : '') +
               (t.id === active ? ' __active' : ''));
    c.setAttribute('data-thread', t.id);
    c.appendChild(el('div', '__cquote', (t.pos < 0 ? '(no longer in the page) ' : '') + '“' + t.quote + '”'));
    t.comments.forEach(function (m) {
      var row = el('div', '__cmsg'), meta = el('div', '__cmeta'), del = el('button', '__cdel', '×');
      del.title = 'Delete comment';
      del.onclick = function () {
        if (confirm('Delete this comment?')) send({action: 'delete', thread: t.id, comment: m.id});
      };
      meta.appendChild(del);
      if (m.name) meta.appendChild(el('b', null, m.name));
      meta.appendChild(document.createTextNode(when(m.time)));
      row.appendChild(meta);
      row.appendChild(el('div', '__cbody', m.body));
      c.appendChild(row);
    });
    var ta = el('textarea');
    ta.placeholder = 'Reply…';
    var b = buttons(c, ta, [t.resolved ? 'Reopen' : 'Resolve', 'Reply']);
    b[0].onclick = function () { send({action: 'resolve', thread: t.id, resolved: !t.resolved}); };
    b[1].onclick = function () {
      if (ta.value.trim()) send({action: 'reply', thread: t.id, name: name(), body: ta.value});
    };
    keys(ta, b[1]);
    c.onclick = function (e) { if (!e.target.closest('textarea, button')) focus(t.id, true); };
    return c;
  }
  function draftCard() {
    var c = el('div', '__card __active');
    c.appendChild(el('div', '__cquote', '“' + draft.anchor.quote + '”'));
    var ta = el('textarea');
    ta.placeholder = 'Add a comment…';
    ta.value = draft.text;
    ta.oninput = function () { draft.text = ta.value; };
    var b = buttons(c, ta, ['Cancel', 'Comment']);
    b[0].onclick = function () { draft = null; render(); };
    b[1].onclick = function () {
      if (!ta.value.trim()) return;
      var a = draft.anchor;
      send({action: 'new', quote: a.quote, prefix: a.prefix, suffix: a.suffix, name: name(), body: ta.value},
           function (j) { draft = null; active = j.threads[j.threads.length - 1].id; });
    };
    keys(ta, b[1], b[0]);
    if (draft.fresh) { draft.fresh = false; setTimeout(function () { ta.focus(); }, 0); }
    return c;
  }
  function focus(id, scrollPage) {
    active = id;
    each('mark.__c', function (m) { m.classList.toggle('__active', m.getAttribute('data-thread') === id); });
    Array.prototype.forEach.call(list.children, function (c) {
      c.classList.toggle('__active', c.getAttribute('data-thread') === id);
    });
    if (scrollPage) {
      var m = document.querySelector('mark.__c[data-thread="' + id + '"]');
      if (!m) return;
      for (var d = m.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
      m.scrollIntoView({block: 'center', behavior: 'smooth'});
    } else {
      panel.hidden = false;
      var c = list.querySelector('.__card[data-thread="' + id + '"]');
      if (c) c.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
  }

  function url() { return '/__comments?page=' + encodeURIComponent(page); }
  function send(body, done) {
    fetch(url(), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) {
        return r.json().then(function (j) { if (!r.ok) throw new Error(j.error); return j; });
      })
      .then(function (j) { data = j; if (done) done(j); render(); },
            function (err) { alert('Comment not saved: ' + err.message); });
  }
  function load() {
    if (busy()) return;
    fetch(url()).then(function (r) { return r.json(); }).then(function (j) {
      if (j.version === data.version && !stale) return;
      data = j;
      if (!busy()) render(); else stale = true;
    }, function () {});
  }

  // Selecting text shows a "Comment" button next to the selection.
  var pop = el('button', null, 'Comment');
  pop.id = '__cpop'; pop.hidden = true;
  pop.addEventListener('mousedown', function (e) { e.preventDefault(); });
  pop.addEventListener('click', startDraft);
  function selection() {
    var sel = getSelection();
    if (!sel.rangeCount || sel.isCollapsed) return null;
    var r = sel.getRangeAt(0), c = r.commonAncestorContainer;
    c = c.nodeType === 1 ? c : c.parentElement;
    if (!c || ui(c) || c.closest('[contenteditable], textarea, input, nav.toc')) return null;
    return r.toString().trim() ? r : null;
  }
  function startDraft() {
    var r = selection();
    pop.hidden = true;
    var a = r && anchorOf(r);
    if (!a) return;
    getSelection().removeAllRanges();
    draft = {anchor: a, text: '', fresh: true};
    active = null;
    panel.hidden = false;
    render();
    panel.scrollTop = 0;
  }
  document.addEventListener('mousedown', function (e) { if (e.target !== pop) pop.hidden = true; });
  document.addEventListener('mouseup', function (e) {
    if (ui(e.target)) return;
    clearTimeout(timer);
    // Wait out a double-click, which the inline editor uses to start editing.
    timer = setTimeout(function () {
      var r = selection();
      if (!r) return;
      var rects = r.getClientRects(), last = rects[rects.length - 1] || r.getBoundingClientRect();
      pop.style.left = (last.right + scrollX + 4) + 'px';
      pop.style.top = (last.bottom + scrollY + 4) + 'px';
      pop.hidden = false;
    }, 300);
  });
  document.addEventListener('dblclick', function () {
    if (window.__inlineEditing) { clearTimeout(timer); pop.hidden = true; }
  });
  document.addEventListener('keydown', function (e) {
    if (e.code === 'KeyM' && e.altKey && (e.ctrlKey || e.metaKey)) { e.preventDefault(); startDraft(); }
  });
  document.addEventListener('click', function (e) {
    var m = e.target.closest && e.target.closest('mark.__c[data-thread]');
    if (!m || !m.getAttribute('data-thread')) return;
    // A plain click on highlighted link text opens the thread; Ctrl/Cmd-click still follows the link.
    if (m.closest('a[href]') && !(e.ctrlKey || e.metaKey)) e.preventDefault();
    focus(m.getAttribute('data-thread'), false);
  });

  // Registered after tour.js, so this runs once the renderer has built the page.
  document.addEventListener('DOMContentLoaded', function () {
    panel = el('div'); panel.id = '__cpanel';
    var head = el('div', '__chead'), label = el('label'), resolved = el('input');
    head.appendChild(el('b', null, 'Comments'));
    resolved.type = 'checkbox';
    resolved.onchange = function () { showResolved = resolved.checked; render(); };
    label.appendChild(resolved);
    label.appendChild(document.createTextNode(' show resolved'));
    head.appendChild(label);
    nameInput = el('input', '__cname');
    nameInput.placeholder = 'Your name (optional)';
    nameInput.value = localStorage.getItem('__comment_name') || '';
    nameInput.onchange = name;
    head.appendChild(nameInput);
    list = el('div');
    panel.appendChild(head);
    panel.appendChild(list);
    panel.hidden = innerWidth < 1400;
    fab = el('button', null, 'Comments');
    fab.id = '__cfab';
    fab.onclick = function () { panel.hidden = !panel.hidden; };
    document.body.appendChild(panel);
    document.body.appendChild(fab);
    document.body.appendChild(pop);
    window.__comments = {render: function () { if (busy()) stale = true; else render(); }};
    load();
    setInterval(load, 4000);
  });
})();
"""


class Handler(SimpleHTTPRequestHandler):
    edit = True

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send(self, code: int, body: str | bytes, ctype: str):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def json(self, code: int, obj):
        self.send(code, json.dumps(obj), "application/json")

    def query(self) -> dict[str, str]:
        from urllib.parse import parse_qs, urlsplit
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/__edit.js":
            return self.send(200, EDIT_JS, "application/javascript")
        if route == "/__comments.js":
            return self.send(200, COMMENTS_JS, "application/javascript")
        if route == "/__comments":
            path = page_path(self.query().get("page", ""))
            if not path:
                return self.json(404, {"error": "no such page"})
            return self.json(200, load_comments(path))
        if route == "/__mtime":
            path = page_path(self.query().get("page", ""))
            return self.json(200, {"mtime": version(path) if path else 0})
        if route == "/__src":
            q = self.query()
            path = page_path(q.get("page", ""))
            if not path:
                return self.json(404, {"error": "no such page"})
            text = open(path, encoding="utf-8").read()
            sp = spans(text)
            i = int(q.get("id", -1))
            if not 0 <= i < len(sp):
                return self.json(409, {"error": "page is stale; reload"})
            _, end, close = sp[i]
            return self.json(200, {"inner": text[end:close]})
        path = page_path(route.lstrip("/") or "index.html")
        if path:
            with open(path, encoding="utf-8") as f:
                return self.send(200, tagged(f.read(), self.edit), "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        if route not in ("/__src", "/__comments"):
            return self.json(404, {"error": "not found"})
        path = page_path(self.query().get("page", ""))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not path:
            return self.json(404, {"error": "no such page"})
        if route == "/__comments":
            try:
                return self.json(200, update_comments(path, body))
            except LookupError as e:
                return self.json(409, {"error": str(e)})
            except ValueError as e:
                return self.json(400, {"error": str(e)})
        if not self.edit:
            return self.json(403, {"error": "editing is disabled on this server"})
        text = open(path, encoding="utf-8").read()
        sp = spans(text)
        i = int(body["id"])
        if not 0 <= i < len(sp) or text[sp[i][1]:sp[i][2]] != body["orig"]:
            return self.json(409, {"error": "element changed on disk; reload the page"})
        start, end, close = sp[i]
        parts = body["blocks"]
        tag = re.match(r"<(\w+)", text[start:end]).group(1).lower()
        if len(parts) == 1 or tag not in ("p", "li"):
            new = text[:end] + " ".join(parts) + text[close:]
        else:
            after = text.index(">", close) + 1
            siblings = [f"<{tag}>{part}</{tag}>" for part in parts[1:]]
            sep = "\n\n" if tag == "p" else "\n"
            new = text[:end] + parts[0] + text[close:after] + "".join(sep + x for x in siblings) + text[after:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, path)
        self.json(200, {"mtime": version(path)})

    def log_message(self, fmt, *args):
        line = str(args[0] if args else "")
        if "/__mtime" not in line and "GET /__comments?" not in line:
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--no-edit", action="store_true", help="serve comments only, without inline editing")
    a = ap.parse_args()
    Handler.edit = not a.no_edit
    print(f"serving {ROOT} on http://{a.bind}:{a.port}/")
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
