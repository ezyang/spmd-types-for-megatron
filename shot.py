#!/usr/bin/env python3
"""shot: render a page from this directory in headless Chrome.

    python3 shot.py index.html [--width 1700] [--height 1000] [--png /tmp/out.png] [--dom] [--scroll SELECTOR_OR_Y]

Serves the working directory over a local HTTP port (file:// blocks fetch-ish
things and fragment scrolling), then screenshots and/or dumps the rendered DOM.
Finds Playwright's chrome-headless-shell (`npx playwright install chromium
--only-shell`) or falls back to the Google Chrome app; `CHROME=<path>` overrides.
Standard library only.
"""
import argparse, glob, http.server, os, socket, subprocess, sys, tempfile, threading


def chrome_path() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    cache = os.path.expanduser("~/Library/Caches/ms-playwright") if sys.platform == "darwin" else os.path.expanduser("~/.cache/ms-playwright")
    shells = sorted(glob.glob(os.path.join(cache, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
    if shells:
        return shells[-1]
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if os.path.exists(p):
            print("shot: chrome-headless-shell not found; using", p, file=sys.stderr)
            return p
    sys.exit("shot: no Chrome found; set CHROME=<path>")


def serve() -> tuple[http.server.ThreadingHTTPServer, int]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--width", type=int, default=1700)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--png", help="screenshot path")
    ap.add_argument("--dom", action="store_true", help="print rendered DOM to stdout")
    ap.add_argument("--scroll", help="CSS selector (or pixel offset) to scroll to before capture")
    a = ap.parse_args()
    if not a.png and not a.dom:
        a.png = os.path.join(tempfile.gettempdir(), "shot.png")
    srv, port = serve()
    url = f"http://127.0.0.1:{port}/{a.page}"
    # The headless shell captures blank once the window is scrolled, so fake it:
    # pull the body up with a negative margin (offsetTop shifts with it, so
    # scroll-position logic keyed on offsetTop vs scrollY still behaves).
    if a.scroll:
        page_src = open(a.page, encoding="utf-8").read()
        js = f"""<script>window.addEventListener('load',function(){{
          var t={a.scroll!r}; var y=/^\\d+$/.test(t)?+t:(document.querySelector(t)||document.body).getBoundingClientRect().top+window.scrollY;
          var top=parseFloat(getComputedStyle(document.body).marginTop)||0;
          document.body.style.marginTop=(top-y)+'px'; window.dispatchEvent(new Event('scroll'));}});</script></body>"""
        with open(os.path.join(os.getcwd(), ".shot-tmp.html"), "w", encoding="utf-8") as f:
            f.write(page_src.replace("</body>", js))
        url = f"http://127.0.0.1:{port}/.shot-tmp.html"
    args = [chrome_path(), "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            f"--window-size={a.width},{a.height}", "--virtual-time-budget=3000"]
    try:
        if a.png:
            subprocess.run(args + [f"--screenshot={a.png}", url], check=True, stderr=subprocess.DEVNULL)
            print(a.png, file=sys.stderr)
        if a.dom:
            out = subprocess.run(args + ["--dump-dom", url], check=True, capture_output=True, text=True).stdout
            sys.stdout.write(out)
    finally:
        srv.shutdown()
        if a.scroll and os.path.exists(".shot-tmp.html"):
            os.remove(".shot-tmp.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
