"""Browser smoke test: the app as a visitor sees it. Starts `streamlit run app.py` on a free port against the committed data, opens every
page in Chromium at a laptop and a phone size and fails on what the unit tests cannot see: a Streamlit exception on the page, a layout
wider than the screen, text under 12px, an uppercase letter-spaced label, an error in the browser console. On the Overview it also
toggles One way, asks for a month nobody has fetched (the "Search … on GitHub" button must offer it) and opens Anywhere (a list).

Marked `smoke`: skipped unless Playwright's Python package and a Chromium are available (`pip install playwright && playwright install
--with-deps chromium`; a Chromium under /opt/pw-browsers or in PLAYWRIGHT_CHROMIUM_EXECUTABLE is used too). CI runs it as its own job."""
import glob
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.smoke
playwright = pytest.importorskip("playwright.sync_api", reason="the smoke test needs Playwright (pip install playwright)")

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = {"Overview": "/", "Expert": "/expert", "Flights": "/flights", "Hotels": "/hotel", "Alerts": "/alerts", "Database": "/database"}
VIEWPORTS = {"laptop": (1366, 900), "phone": (390, 844)}
MIN_FONT_PX = 12
# Streamlit's own: on a page URL like /expert the frontend first probes /expert/_stcore/health and host-config (404) to find its base path
STREAMLIT_NOISE = re.compile(r"/_stcore/(health|host-config)")

AUDIT = """() => {
  const vw = window.innerWidth, out = {overflow: [], small: [], upper: []};
  for (const sel of ['html', 'body', '[data-testid="stAppViewContainer"]', '[data-testid="stMain"]', '[data-testid="stMainBlockContainer"]']) {
    const el = document.querySelector(sel);
    if (el && el.scrollWidth > el.clientWidth + 1) out.overflow.push(sel + ' scrolls ' + el.scrollWidth + 'px in ' + el.clientWidth + 'px');
  }
  // an element sticking out of the viewport counts too, unless an ancestor scrolls it on purpose (a wide table, a chart)
  for (const el of document.querySelectorAll('[data-testid="stMainBlockContainer"] *')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.right <= vw + 1 || el.closest('svg')) continue;
    let a = el.parentElement, scrolled = false;
    while (a && a !== document.body) { const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolled = a.getBoundingClientRect().right <= vw + 1; break; } a = a.parentElement; }
    if (!scrolled) out.overflow.push((el.dataset.testid || el.tagName) + ' reaches ' + Math.round(r.right) + 'px of ' + vw);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  let n;
  while ((n = walker.nextNode())) {
    const text = n.textContent.trim(), p = n.parentElement;
    if (!text || !p || p.closest('script, style, noscript, template') || seen.has(p) || !p.getClientRects().length) continue;
    const cs = getComputedStyle(p);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    seen.add(p);
    const fs = parseFloat(cs.fontSize);
    if (fs < %(min_font)d) out.small.push(fs.toFixed(1) + 'px <' + p.tagName.toLowerCase() + '> ' + JSON.stringify(text.slice(0, 40)));
    if (cs.textTransform === 'uppercase' && parseFloat(cs.letterSpacing) > 0) out.upper.push('<' + p.tagName.toLowerCase() + '> ' + JSON.stringify(text.slice(0, 40)));
  }
  out.overflow = out.overflow.slice(0, 8); out.small = out.small.slice(0, 8); out.upper = out.upper.slice(0, 8);
  return out;
}""" % {"min_font": MIN_FONT_PX}

# Streamlit's "Running…" status, elements left stale by a rerun, and the page's size: settled when the first two are gone and the size holds
STATE = """() => { const w = document.querySelector('[data-testid="stStatusWidget"]');
  return [!!w && w.innerText.trim() !== '', document.querySelectorAll('[data-stale="true"]').length,
          document.querySelectorAll('[data-testid="stElementContainer"]').length, document.body.innerText.length]; }"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chromium_executable():
    """None for Playwright's own Chromium (`playwright install chromium`); else the one named in PLAYWRIGHT_CHROMIUM_EXECUTABLE or the
    Node Playwright's under /opt/pw-browsers, as on the development machines."""
    named = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if named and os.path.exists(named):
        return named
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"), reverse=True)
    return found[0] if found else None


@pytest.fixture(scope="module")
def app():
    """The app on a free port, like `streamlit run app.py`: the committed data, no AI keys (the Expert answers in plain Python) and a
    GitHub token that is not one, so the pages offer the "Search … on GitHub" buttons the deployed app has (nothing is pressed; the one
    request the app makes with it, for the repository's newest data files, is refused and the committed files are used)."""
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
    env.update(GITHUB_TOKEN="smoke-test-not-a-token", STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", str(port),
                             "--server.fileWatcherType", "none"], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    try:
        while True:
            try:
                if urllib.request.urlopen(url + "/_stcore/health", timeout=2).status == 200:
                    break
            except Exception:
                pass
            if proc.poll() is not None or time.time() > deadline:
                raise RuntimeError("streamlit did not start:\n" + (proc.stdout.read() if proc.poll() is not None else ""))
            time.sleep(0.5)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=chromium_executable())
        except Exception as err:  # Playwright installed, no browser: `playwright install --with-deps chromium`
            pytest.skip(f"no Chromium for Playwright: {str(err).splitlines()[0]}")
        yield b
        b.close()


class Visit:
    """One browser tab on the app: opens pages, waits for Streamlit to finish a run, keeps the console errors and audits the page."""

    def __init__(self, browser, app, size):
        self.app = app
        self.context = browser.new_context(viewport={"width": size[0], "height": size[1]})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" and not STREAMLIT_NOISE.search(m.text + (m.location or {}).get("url", "")) else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"uncaught: {e}"))

    def open(self, path):
        self.errors.clear()
        self.page.goto(self.app + path)
        self.page.wait_for_selector('[data-testid="stMainBlockContainer"] [data-testid="stElementContainer"]', state="attached", timeout=60000)
        self.settle()

    def settle(self, wait=0.4):
        """Until Streamlit has finished running the page: no "Running…" status, no stale element, and the same elements and text over
        three looks in a row (after a click the rerun starts within a moment, so wait for it first)."""
        time.sleep(wait)
        deadline, last, held = time.time() + 90, None, 0
        while held < 3:
            running, stale, *size = self.page.evaluate(STATE)
            held = held + 1 if not running and not stale and size == last else 0
            last = size
            assert time.time() < deadline, "the page is still running after 90 s"
            time.sleep(0.3)

    def audit(self, where):
        exceptions = self.page.locator('[data-testid="stException"]').all_inner_texts()
        a = self.page.evaluate(AUDIT)
        problems = ([f"exception on the page: {x[:300]}" for x in exceptions] + [f"wider than the screen: {x}" for x in a["overflow"]]
                    + [f"text under {MIN_FONT_PX}px: {x}" for x in a["small"]] + [f"uppercase letter-spaced: {x}" for x in a["upper"]]
                    + [f"console error: {x[:300]}" for x in self.errors])
        assert not problems, f"{where}:\n  " + "\n  ".join(problems)

    def buttons(self, text):
        return [t.strip() for t in self.page.locator("button").all_inner_texts() if text in t]

    def close(self):
        self.context.close()


@pytest.fixture(params=list(VIEWPORTS), ids=list(VIEWPORTS))
def visit(request, browser, app):
    v = Visit(browser, app, VIEWPORTS[request.param])
    yield v
    v.close()


@pytest.mark.parametrize("name", list(PAGES))
def test_page_renders_cleanly(visit, name):
    visit.open(PAGES[name])
    visit.audit(f"{name} at {visit.page.viewport_size['width']}px")


def far_month(today):
    """The last whole calendar month the date picker reaches (today + trip_plan.HORIZON): far enough that no daily run has fetched it."""
    import trip_plan
    last = today + timedelta(trip_plan.HORIZON)
    end = last.replace(day=1) - timedelta(1)
    return end.replace(day=1), end


def test_overview_one_way_far_month_and_anywhere(browser, app):
    v = Visit(browser, app, VIEWPORTS["laptop"])
    try:
        v.open("/")
        assert v.page.locator('[data-testid="stSlider"]').count() == 1, "the Nights slider shows for a return trip"
        v.page.locator('[data-testid="stButtonGroup"] button', has_text="One way").first.click()
        v.settle()
        assert v.page.locator('[data-testid="stSlider"]').count() == 0, "One way: no nights to choose"
        v.audit("Overview, one way")

        start, end = far_month(date.today())
        picker = v.page.locator('[data-testid="stDateInput"]').first
        for field, day in (("start", start), ("end", end)):  # the picker's day/month/year segments; typing moves on to the next one
            picker.locator(f'[data-range-field="{field}"] [role="spinbutton"][data-type="day"]').click()
            v.page.keyboard.type(f"{day:%d%m%Y}")
        v.page.mouse.click(5, 5)  # a click outside the picker commits the range
        v.settle()
        assert f"{start:%B %Y}" in v.page.inner_text("body") or f"{start:%d}–{end:%d %b}" in v.page.inner_text("body")
        searches = v.buttons("on GitHub")
        assert any(re.search(r"Search .* on GitHub", b) for b in searches), f"no Search … on GitHub button for {start:%B %Y}: {searches}"
        v.audit(f"Overview, {start:%B %Y}")

        v.open("/")  # a fresh session: the default route and dates again
        to = v.page.locator('[data-testid="stSelectbox"]').nth(1)
        to.locator('button[aria-label="Open"]').click()
        v.page.locator('[role="option"]', has_text="Anywhere").first.click()
        v.settle()
        assert v.page.locator("p.group").first.inner_text().startswith("From "), "Anywhere names the airport it lists from"
        assert v.page.locator(".fares .fare").count() >= 1, "Anywhere lists at least one destination with fares saved"
        v.audit("Overview, Anywhere")
    finally:
        v.close()
