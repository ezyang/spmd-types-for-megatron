#!/usr/bin/env python3
"""Serve the tour with inline prose editing.

    python3 edit_server.py [--port 8001]

Serves this directory. Every *.html page gets a small editor script injected,
and each prose block (p, li, h1-h6, summary) outside a hunk is tagged with its
ordinal in the source file. Double-click a block to edit it in place;
Ctrl/Cmd+Enter or clicking away saves, Esc cancels. Alt+double-click edits the
raw HTML instead. Enter starts a new paragraph (or list item); pasting inserts
plain text. Saving rewrites only that element in the file on disk, and is
refused if the element changed on disk since the page loaded.
The page reloads itself when the file changes underneath it (e.g. tour.py sync).

Standard library only; the files on disk stay plain HTML.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "summary"}
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


def tagged(text: str) -> str:
    out, last = [], 0
    for i, (start, end, _) in enumerate(spans(text)):
        close = end - 2 if text[end - 2] == "/" else end - 1
        out.append(text[last:close])
        out.append(f' data-src-id="{i}"')
        last = close
    out.append(text[last:])
    html = "".join(out)
    inject = '<script src="/__edit.js" defer></script>\n'
    i = html.lower().rfind("</head>")
    return html[:i] + inject + html[i:] if i >= 0 else inject + html


EDIT_JS = r"""
(function () {
  var page = location.pathname.split('/').pop() || 'index.html';
  var mtime = null, editing = null;
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
    function restore() { node.innerHTML = ed.rendered; editing = null; }
    if (!save || (parts.length === 1 && inner === ed.orig)) { restore(); return; }
    api('POST', {id: ed.id, orig: ed.orig, blocks: parts}).then(function (j) {
      if (j.status !== 200) { say(j.error, true); restore(); return; }
      if (parts.length > 1) { sessionStorage.setItem('__scroll', String(scrollY)); location.reload(); return; }
      mtime = j.mtime; node.innerHTML = inner; editing = null; say('saved');
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


class Handler(SimpleHTTPRequestHandler):
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
                return self.send(200, tagged(f.read()), "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/__src":
            return self.json(404, {"error": "not found"})
        path = page_path(self.query().get("page", ""))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not path:
            return self.json(404, {"error": "no such page"})
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
        if "/__mtime" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args()
    print(f"serving {ROOT} on http://{a.bind}:{a.port}/")
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
