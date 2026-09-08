#!/usr/bin/env python3
"""tour: keep the diff hunks in a hand-written HTML walkthrough in sync with a branch.

Hunks are machine-owned, prose is human-owned.  The only bytes this tool ever
rewrites in index.html / scratch.html are:

  * the text and ``data-hash`` of ``<pre class="hunk">`` elements,
  * the ``content`` of the ``tour-*`` ``<meta>`` tags,
  * inserted ``<!-- SYNC:... -->`` marker comments,
  * an appended ``<section id="unplaced">``.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import html
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

POST = "index.html"  # primary document; new units land here
SCRATCH = "scratch.html"  # units parked here count as unplaced but pass check
ASSETS = ("tour.css", "tour.js")  # renderer; written by init if missing, next to tour.py


def documents() -> list[str]:
    """Every *.html in the working directory is a document; index.html first."""
    names = sorted(n for n in os.listdir(".") if n.endswith(".html"))
    return sorted(names, key=lambda n: (n != POST, n == SCRATCH, n))
CONTEXT = 3
MODULE = "<module>"

# --------------------------------------------------------------------------- git


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True
    )
    return proc.stdout.decode("utf-8", errors="replace")


def git_show(repo: str, rev: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def github_slug(repo: str) -> str:
    try:
        url = git(repo, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        return "unknown/unknown"
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else "unknown/unknown"


# ------------------------------------------------------------------- diff model


@dataclass
class Op:
    tag: str  # ' ', '+', '-'
    old: int | None
    new: int | None
    text: str
    symbol: str = ""


@dataclass
class Unit:
    file: str
    symbol: str
    text: str
    hash: str
    changed_lines: list[str]
    whole: bool  # pure addition of a whole symbol / file
    order: tuple

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.symbol)


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def file_ops(repo: str, base: str, head: str, path: str,
             old: list[str], new: list[str]) -> list[Op]:
    """Whole-file alignment of old vs new using git's own -U0 hunks."""
    raw = git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames",
              base, head, "--", path)
    hunks = []
    for line in raw.splitlines():
        m = HUNK_RE.match(line)
        if m:
            a, b, c, d = (int(x) if x is not None else None for x in m.groups())
            hunks.append((a, 1 if b is None else b, c, 1 if d is None else d))
    ops: list[Op] = []
    oi = ni = 1
    for a, b, c, d in hunks:
        a_start = a + 1 if b == 0 else a
        c_start = c + 1 if d == 0 else c
        while oi < a_start:
            ops.append(Op(" ", oi, ni, old[oi - 1]))
            oi += 1
            ni += 1
        assert ni == c_start, (path, a, b, c, d, oi, ni)
        for _ in range(b):
            ops.append(Op("-", oi, None, old[oi - 1]))
            oi += 1
        for _ in range(d):
            ops.append(Op("+", None, ni, new[ni - 1]))
            ni += 1
    while oi <= len(old):
        ops.append(Op(" ", oi, ni, old[oi - 1]))
        oi += 1
        ni += 1
    assert ni == len(new) + 1, (path, ni, len(new))
    return ops


def symbol_map(src: str) -> tuple[dict[int, str], dict[str, tuple[int, int]]]:
    """Line -> innermost enclosing def/class qualified name; symbol -> span."""
    tree = ast.parse(src)
    spans: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                spans.append((start, child.end_lineno or child.lineno, name))
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    line_to_sym: dict[int, str] = {}
    extents: dict[str, tuple[int, int]] = {}
    for start, end, name in sorted(spans, key=lambda s: (s[0], -s[1])):
        extents.setdefault(name, (start, end))
        for ln in range(start, end + 1):
            line_to_sym[ln] = name
    return line_to_sym, extents


def assign_symbols(ops: list[Op], old_map: dict[int, str], new_map: dict[int, str]) -> None:
    for op in ops:
        if op.tag == "+":
            op.symbol = new_map.get(op.new, MODULE)
        elif op.tag == "-":
            op.symbol = old_map.get(op.old, MODULE)
    # Blank changed lines attach to the next non-blank line in their run of
    # changes (blank lines precede a def), else to the previous one.
    i = 0
    while i < len(ops):
        if ops[i].tag == " ":
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j].tag != " ":
            j += 1
        run = ops[i:j]
        nonblank = [k for k, op in enumerate(run) if op.text.strip()]
        for k, op in enumerate(run):
            if op.text.strip():
                continue
            prev = [n for n in nonblank if n < k]
            nxt = [n for n in nonblank if n > k]
            if nxt:
                op.symbol = run[nxt[0]].symbol
            elif prev:
                op.symbol = run[prev[-1]].symbol
        i = j


def windows(ops: list[Op], mine: list[int], foreign: set[int]) -> list[tuple[int, int]]:
    """Merge 3-line context windows around ``mine``; stop at foreign change ops."""
    out: list[tuple[int, int]] = []
    for i in mine:
        lo = i
        while lo > 0 and i - lo < CONTEXT and (lo - 1) not in foreign and ops[lo - 1].tag == " ":
            lo -= 1
        hi = i
        while hi < len(ops) - 1 and hi - i < CONTEXT and (hi + 1) not in foreign and ops[hi + 1].tag == " ":
            hi += 1
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


PY_FUNCNAME = re.compile(r"^\s*(class|(async\s+)?def)\s")
ANY_FUNCNAME = re.compile(r"^[A-Za-z_$]")


def section_heading(ops: list[Op], lo: int, hi: int, old: list[str], new: list[str], is_py: bool,
                    old_map: dict[int, str] | None = None,
                    new_map: dict[int, str] | None = None) -> str:
    """Context for the ``@@`` line.  Python: the qualified symbol enclosing the
    first line of the section that has one (``Cls.method``), which names the
    class where git's funcname would only say ``def forward``, and names the
    def being added rather than the one above it when the section opens on a
    blank line.  Otherwise the nearest preceding def/class (or any unindented
    line), as git does."""
    pat = PY_FUNCNAME if is_py else ANY_FUNCNAME
    for op in ops[lo:hi + 1]:
        sym = (new_map or {}).get(op.new) if op.new is not None else (old_map or {}).get(op.old)
        if sym:
            return sym
    first = ops[lo]
    if first.new is not None:
        lines, upto = new, first.new - 1
    else:
        lines, upto = old, first.old - 1
    for ln in range(upto - 1, -1, -1):
        if pat.match(lines[ln]):
            return lines[ln].strip()[:80]
    return ""


def render_section(ops: list[Op], lo: int, hi: int, heading: str) -> str:
    seg = ops[lo:hi + 1]
    olds = [o.old for o in seg if o.old is not None]
    news = [o.new for o in seg if o.new is not None]
    if olds:
        os_, oc = olds[0], len(olds)
    else:
        prev = [o.old for o in ops[:lo] if o.old is not None]
        os_, oc = (prev[-1] if prev else 0), 0
    if news:
        ns, nc = news[0], len(news)
    else:
        prev = [o.new for o in ops[:lo] if o.new is not None]
        ns, nc = (prev[-1] if prev else 0), 0
    fmt = lambda s, c: f"{s}" if c == 1 else f"{s},{c}"
    hdr = f"@@ -{fmt(os_, oc)} +{fmt(ns, nc)} @@"
    if heading:
        hdr += " " + heading
    return "\n".join([hdr] + [o.tag + o.text for o in seg])


def unit_hash(changed: list[str]) -> str:
    norm = "\n".join(line[0] + " ".join(line[1:].split()) for line in changed)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def extract_units(repo: str, base: str, head: str) -> list[Unit]:
    listing = git(repo, "diff", "--numstat", "-z", "--no-renames", base, head)
    files = []
    for entry in listing.split("\0"):
        if not entry:
            continue
        added, deleted, path = entry.split("\t", 2)
        if added == "-" and deleted == "-":
            continue  # binary
        files.append(path)
    units: list[Unit] = []
    for path in sorted(files):
        old_src = git_show(repo, base, path)
        new_src = git_show(repo, head, path)
        old = old_src.splitlines() if old_src else []
        new = new_src.splitlines() if new_src else []
        ops = file_ops(repo, base, head, path, old, new)
        is_py = path.endswith(".py")
        extents: dict[str, tuple[int, int]] = {}
        old_map: dict[int, str] = {}
        new_map: dict[int, str] = {}
        if is_py:
            try:
                old_map = symbol_map(old_src)[0] if old_src else {}
                new_map, extents = symbol_map(new_src) if new_src else ({}, {})
                assign_symbols(ops, old_map, new_map)
            except SyntaxError:
                is_py = False
        changes = [i for i, o in enumerate(ops) if o.tag != " "]
        if not is_py:
            for n, (lo, hi) in enumerate(windows(ops, changes, set()), 1):
                for i in range(lo, hi + 1):
                    if ops[i].tag != " ":
                        ops[i].symbol = f"<hunk {n}>"
        groups: dict[str, list[int]] = {}
        for i in changes:
            groups.setdefault(ops[i].symbol, []).append(i)
        for symbol, mine in groups.items():
            foreign = set(changes) - set(mine)
            secs = windows(ops, mine, foreign)
            text = "\n".join(
                render_section(ops, lo, hi, section_heading(ops, lo, hi, old, new, path.endswith(".py"), old_map, new_map))
                for lo, hi in secs
            )
            changed = [ops[i].tag + ops[i].text for i in mine]
            whole = all(ops[i].tag == "+" for i in mine)
            if whole and old_src is not None:
                ext = extents.get(symbol)
                whole = bool(ext) and all(
                    o.tag == "+" for o in ops if o.new is not None and ext[0] <= o.new <= ext[1]
                )
            units.append(Unit(path, symbol, text, unit_hash(changed), changed, whole, (path, mine[0])))
    return units


# ------------------------------------------------------------------ html model

PRE_RE = re.compile(r"<pre\b(?P<attrs>[^>]*)>(?P<body>.*?)</pre>", re.S)
ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')
H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S)
MARKER_RE = re.compile(r"<!--\s*SYNC:(\w+)\s+(.*?)(?:\s|-->)", re.S)


@dataclass
class Pre:
    start: int
    end: int
    attrs: dict[str, str]
    attrs_span: tuple[int, int]
    body: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.attrs.get("data-file", ""), self.attrs.get("data-symbol", ""))


def find_pres(doc: str) -> list[Pre]:
    pres = []
    for m in PRE_RE.finditer(doc):
        attrs = {html.unescape(k): html.unescape(v) for k, v in ATTR_RE.findall(m.group("attrs"))}
        if "hunk" not in attrs.get("class", "").split():
            continue
        pres.append(Pre(m.start(), m.end(), attrs, m.span("attrs"), html.unescape(m.group("body"))))
    return pres


def pre_html(unit: Unit, render: str | None = None) -> str:
    attrs = [("class", "hunk"), ("data-file", unit.file), ("data-symbol", unit.symbol),
             ("data-hash", unit.hash)]
    if render:
        attrs.append(("data-render", render))
    a = " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in attrs)
    return f"<pre {a}>\n{html.escape(unit.text, quote=False)}\n</pre>"


def meta_get(doc: str, name: str) -> str | None:
    m = re.search(rf'<meta\s+name="tour-{name}"\s+content="([^"]*)"', doc)
    return html.unescape(m.group(1)) if m else None


def meta_set(doc: str, name: str, value: str) -> str:
    tag = f'<meta name="tour-{name}" content="{html.escape(value, quote=True)}">'
    pat = re.compile(rf'<meta\s+name="tour-{name}"\s+content="[^"]*"\s*/?>')
    if pat.search(doc):
        return pat.sub(lambda _: tag, doc, count=1)
    i = doc.find("</head>")
    if i < 0:
        raise SystemExit(f"no </head> to put meta tags into")
    return doc[:i] + tag + "\n" + doc[i:]


def comment_safe(s: str) -> str:
    return s.replace("<!--", "&lt;!--").replace("-->", "--&gt;").replace("--!>", "--!&gt;")


def line_of(doc: str, offset: int) -> int:
    return doc.count("\n", 0, offset) + 1


# --------------------------------------------------------------------- template

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="tour-repo" content="{repo}">
<meta name="tour-base" content="{base}">
<meta name="tour-head" content="{head}">
<meta name="tour-compare" content="{compare}">
<link rel="stylesheet" href="tour.css">
<script src="tour.js" defer></script>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""

def new_document(title: str, repo: str, base: str, head: str, compare: str, body: str) -> str:
    return TEMPLATE.format(title=html.escape(title), repo=html.escape(repo, quote=True),
                           base=base, head=head, compare=html.escape(compare, quote=True), body=body)


# --------------------------------------------------------------------- commands


def read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write(path: str, doc: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(doc)


def cmd_init(args: argparse.Namespace) -> int:
    repo = args.repo
    base_sha = git(repo, "merge-base", args.base, args.head).strip()
    head_sha = git(repo, "rev-parse", args.head + "^{commit}").strip()
    slug = github_slug(repo)
    strip = lambda r: re.sub(r"^(origin|upstream)/", "", r)
    compare = f"https://github.com/{slug}/compare/{strip(args.base)}...{strip(args.head)}"
    units = extract_units(repo, base_sha, head_sha)

    here = os.path.dirname(os.path.abspath(__file__))
    for asset in ASSETS:
        if not os.path.exists(asset):
            with open(os.path.join(here, asset), encoding="utf-8") as f:
                write(asset, f.read())

    body = "\n".join(pre_html(u, "added" if u.whole else None) for u in units)
    write(SCRATCH, new_document("scratch", slug, base_sha, head_sha, compare, body))

    if os.path.exists(POST):
        doc = read(POST)
        for name, value in (("repo", slug), ("base", base_sha), ("head", head_sha), ("compare", compare)):
            doc = meta_set(doc, name, value)
        write(POST, doc)
    else:
        write(POST, new_document("Untitled walkthrough", slug, base_sha, head_sha, compare, ""))
    print(f"merge-base {base_sha}\nhead       {head_sha}\n{len(units)} units written to {SCRATCH}")
    return 0


def marker_changed(unit: Unit, old_changed: list[str]) -> str:
    diff = list(difflib.unified_diff(old_changed, unit.changed_lines, n=0, lineterm=""))[2:]
    body = "\n".join(diff)
    return f"<!-- SYNC:changed {unit.file}::{unit.symbol}\n{comment_safe(body)}\n-->"


def sync_document(doc: str, units: dict[tuple[str, str], Unit], placed: set, counts: dict) -> str:
    pieces = []
    pos = 0
    for pre in find_pres(doc):
        line_start = doc.rfind("\n", 0, pre.start) + 1
        indent = doc[line_start:pre.start]
        if indent.strip():
            indent = ""
        pieces.append(doc[pos:pre.start])
        unit = units.get(pre.key)
        if unit is None:
            marker = f"<!-- SYNC:removed {pre.key[0]}::{pre.key[1]} -->"
            before = doc[:pre.start].rstrip()
            if not before.endswith(marker):
                pieces.append(marker + "\n" + indent)
                counts["removed"] += 1
            pieces.append(doc[pre.start:pre.end])
        else:
            placed.add(pre.key)
            old_hash = pre.attrs.get("data-hash")
            attrs = doc[pre.attrs_span[0]:pre.attrs_span[1]]
            if old_hash is not None and old_hash != unit.hash:
                old_changed = [l for l in pre.body.strip("\n").split("\n")
                               if l[:1] in "+-" and not l.startswith(("+++", "---"))]
                pieces.append(marker_changed(unit, old_changed) + "\n" + indent)
                counts["changed"] += 1
            else:
                counts["unchanged"] += 1
            new_hash = f'data-hash="{unit.hash}"'
            if re.search(r'\bdata-hash="[^"]*"', attrs):
                attrs = re.sub(r'\bdata-hash="[^"]*"', new_hash, attrs, count=1)
            else:
                attrs = attrs.rstrip() + " " + new_hash
            pieces.append(f"<pre{attrs}>\n{html.escape(unit.text, quote=False)}\n</pre>")
        pos = pre.end
    pieces.append(doc[pos:])
    return "".join(pieces)


def append_unplaced(doc: str, new_units: list[Unit]) -> str:
    blocks = "\n".join(
        f"<!-- SYNC:new {u.file}::{u.symbol} -->\n{pre_html(u, 'added' if u.whole else None)}"
        for u in new_units
    )
    m = None
    for m in re.finditer(r'<section\s+id="unplaced"[^>]*>', doc):
        pass
    if m:
        close = doc.find("</section>", m.end())
        if close < 0:
            close = len(doc)
        return doc[:close] + blocks + "\n" + doc[close:]
    section = f'<section id="unplaced">\n<h2>Unplaced</h2>\n{blocks}\n</section>\n'
    i = doc.rfind("</body>")
    if i < 0:
        return doc.rstrip("\n") + "\n" + section
    return doc[:i] + section + doc[i:]


def report_markers(paths: list[str]) -> list[tuple[str, int, str, str]]:
    found = []
    for path in paths:
        if not os.path.exists(path):
            continue
        doc = read(path)
        for m in MARKER_RE.finditer(doc):
            found.append((path, line_of(doc, m.start()), m.group(1), m.group(2).strip()))
    return found


def cmd_sync(args: argparse.Namespace) -> int:
    repo = args.repo
    post = read(POST)
    base = meta_get(post, "base")
    if not base:
        raise SystemExit(f"{POST} has no tour-base meta; run init first")
    head = git(repo, "rev-parse", (args.head or "HEAD") + "^{commit}").strip()
    units = {u.key: u for u in extract_units(repo, base, head)}
    counts = {"unchanged": 0, "changed": 0, "removed": 0, "new": 0}
    placed: set = set()
    docs = {}
    for path in documents():
        docs[path] = sync_document(read(path), units, placed, counts)
    new_units = sorted((u for k, u in units.items() if k not in placed), key=lambda u: u.order)
    if new_units:
        counts["new"] = len(new_units)
        docs[POST] = append_unplaced(docs[POST], new_units)
    for path, doc in docs.items():
        write(path, meta_set(doc, "head", head))
    print(f"head {head}")
    print("  ".join(f"{k}: {v}" for k, v in counts.items()))
    for path, ln, kind, what in report_markers(list(docs)):
        print(f"{path}:{ln}: SYNC:{kind} {what}")
    return 0


def doc_keys(path: str) -> set:
    return {p.key for p in find_pres(read(path))} if os.path.exists(path) else set()


def cmd_check(args: argparse.Namespace) -> int:
    problems = []
    for path, ln, kind, what in report_markers([d for d in documents() if d != SCRATCH]):
        problems.append(f"{path}:{ln}: SYNC:{kind} {what}")
    repo = args.repo or os.environ.get("TOUR_REPO")
    if not repo:
        raise SystemExit("check needs REPO_PATH (argument or TOUR_REPO env var) to verify totality")
    post = read(POST)
    base, head = meta_get(post, "base"), meta_get(post, "head")
    known = set().union(*(doc_keys(d) for d in documents()))
    for u in extract_units(repo, base, head):
        if u.key not in known:
            problems.append(f"missing: {u.file}::{u.symbol}")
    for p in problems:
        print(p)
    print("check: " + ("FAILED" if problems else "ok"))
    return 1 if problems else 0


def cmd_list(args: argparse.Namespace) -> int:
    for path in documents():
        if path == SCRATCH:
            continue
        doc = read(path)
        items = [(m.start(), "h2", re.sub(r"<[^>]+>", "", m.group(1)).strip()) for m in H2_RE.finditer(doc)]
        items += [(p.start, "pre", f"{p.key[0]}::{p.key[1]}") for p in find_pres(doc)]
        items.sort()
        print(f"== {path}")
        if items and items[0][1] != "h2":
            print("(before first heading)")
        for _, kind, text in items:
            print(text if kind == "h2" else f"    {text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tour.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="extract all units into scratch.html")
    p.add_argument("repo"); p.add_argument("base"); p.add_argument("head")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("sync", help="refresh hunks against a new head")
    p.add_argument("repo"); p.add_argument("head", nargs="?")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("check", help="fail if markers remain or a unit is unplaced")
    p.add_argument("repo", nargs="?")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("list", help="print the reading order of every document")
    p.set_defaults(fn=cmd_list)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
