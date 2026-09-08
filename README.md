# Narrated diff of the Megatron SPMD type-checking change

`index.html` is the document; `appendix.html` holds hunks that repeat a pattern
already shown, under the same headings. Every `*.html` here is synced and checked;
link to a shunted hunk with `appendix.html#file::symbol`. `tour.py` keeps the diff hunks inside it in sync
with the branch; you write the prose around them.

    python3 tour.py init  REPO_PATH BASE HEAD   # merge-base, extract units into scratch.html, write meta tags
    python3 tour.py sync  REPO_PATH [HEAD]      # refresh hunks against HEAD (default: repo's checked-out HEAD)
    python3 tour.py check [REPO_PATH]           # fail if SYNC: markers remain or a unit is in no document
    python3 tour.py list                        # reading order per document: <h2> headings, file::symbol under each

`check` needs the repo path (argument or `TOUR_REPO` env var) to recompute the unit set.

Rules: the tool only rewrites the text and `data-hash` of `<pre class="hunk">`
elements, the `tour-*` meta tags, inserted `<!-- SYNC:... -->` comments, and an
appended `<section id="unplaced">`. Everything else is yours. Never edit hunk
text by hand; move the whole `<pre>` instead, and shape it with attributes:

    data-render="diff"|"added"|"new"   gutter diff (default) / new side, no gutter, adds still green / new side only
    data-lines="5-20,30"               show only these new-side lines
    data-collapse                      hide context lines
    data-fold                          start with context more than 3 lines from any
                                       change hidden behind an "expand" row
    data-context="class"|"file"|SYMBOL widen to the enclosing class / whole file / a
                                       qualified symbol; every change inside that
                                       span is shown as a diff

`data-context` is how a hunk carries more of the file than its own change, for
example the whole custom autograd function around a new `spmd_typecheck`.
`sync` recomputes the widened text from the repo each time, so the extra
context follows the diff as it changes; only the attribute lives in the HTML.

A unit is one `(file, symbol)`, where symbol is the innermost enclosing
`def`/`class` (`<module>` for imports and other top-level statements,
`<hunk N>` for non-Python files). `sync` matches on `(file, symbol)` only and
uses `data-hash` just to decide whether to leave a `SYNC:changed` marker with
the diff of the diff. Delete markers yourself once the prose is right; run
`check` before publishing. `init` overwrites `scratch.html`; it never
overwrites an existing `index.html`.

## Renderer and browser validation

The renderer is `tour.css` and `tour.js`, loaded by every page. Consecutive
hunks from the same file (nothing but whitespace or comments between them)
share one box and file header. Each hunk opens with a GitHub-style
`@@ -a,b +c,d @@ context` row, omitted when it continues exactly where the
previous hunk ended (per-symbol pieces of a new file); each hunk keeps its own
`file::symbol` anchor. Cross-links are automatic and deliberately dumb: every
added `def NAME` on a page becomes the anchor `#def-NAME`, and every exact
identifier match of `NAME` in that page's hunks (plus prose `<code>NAME</code>`
or `<code>NAME()</code>`) links to it; a name added more than once on the page
is ambiguous and not linked. On wide
viewports it draws a TOC sidebar: sections, and under each the files whose hunks
appear there; `n/N` marks a file whose other hunks (in this page) sit under other
headings, and its tooltip names them.

After changing HTML or the renderer, use the real browser renderer:

    python3 shot.py index.html --dom > /tmp/index-rendered.html
    python3 shot.py index.html --png /tmp/index.png
    python3 shot.py index.html --scroll '#some-section' --png /tmp/section.png

The first command verifies that the JavaScript renderer runs; inspect the DOM
for generated `.tour` elements or the feature being tested. The PNG commands
verify layout. `shot.py` starts Chrome in headless mode as a subprocess, so it
does not need a GUI, display server, or interactive browser tool. Do not replace
this check with jsdom or a hand-written DOM shim: those do not exercise Chrome's
DOM, CSS, or layout behavior.

`shot.py` automatically finds Playwright's cached `chrome-headless-shell` or an
installed Google Chrome. The agent environment normally already has the former.
If the script reports that no Chrome was found, install it and retry before
concluding that browser validation is unavailable:

    npx playwright install chromium --only-shell

Alternatively, set `CHROME=/absolute/path/to/chrome`. If Chrome is found but
fails to start, preserve and report the actual `shot.py` stderr rather than
substituting a different renderer.

Pushing `main` deploys the repo root to GitHub Pages via
`.github/workflows/pages.yml` (set Pages source to "GitHub Actions" once).
