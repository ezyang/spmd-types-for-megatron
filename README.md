# Narrated diff of the Megatron SPMD type-checking change

`post.html` is the document. `tour.py` keeps the diff hunks inside it in sync
with the branch; you write the prose around them.

    python3 tour.py init  REPO_PATH BASE HEAD   # merge-base, extract units into scratch.html, write meta tags
    python3 tour.py sync  REPO_PATH [HEAD]      # refresh hunks against HEAD (default: repo's checked-out HEAD)
    python3 tour.py check [REPO_PATH]           # fail if SYNC: markers remain or a unit is in neither file
    python3 tour.py list                        # reading order: <h2> headings and file::symbol under each

`check` needs the repo path (argument or `TOUR_REPO` env var) to recompute the unit set.

Rules: the tool only rewrites the text and `data-hash` of `<pre class="hunk">`
elements, the `tour-*` meta tags, inserted `<!-- SYNC:... -->` comments, and an
appended `<section id="unplaced">`. Everything else is yours. Never edit hunk
text by hand; move the whole `<pre>` instead, and shape it with attributes:

    data-render="diff"|"added"|"new"   gutter diff (default) / plain code / new side only
    data-lines="5-20,30"               show only these new-side lines
    data-collapse                      hide context lines

A unit is one `(file, symbol)`, where symbol is the innermost enclosing
`def`/`class` (`<module>` for imports and other top-level statements,
`<hunk N>` for non-Python files). `sync` matches on `(file, symbol)` only and
uses `data-hash` just to decide whether to leave a `SYNC:changed` marker with
the diff of the diff. Delete markers yourself once the prose is right; run
`check` before publishing. `init` overwrites `scratch.html`; it never
overwrites an existing `post.html`.
