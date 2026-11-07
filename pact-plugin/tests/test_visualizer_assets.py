"""
Location: pact-plugin/tests/test_visualizer_assets.py
Summary: Static-asset and launcher gate for PACT Mission Control
         (pact-plugin/visualizer/). Pins the contract-declared package
         layout, the bin/pact-viz launcher contract (executable, bash,
         Python 3.12 floor, runs the visualizer module), the asset
         security posture (no external URLs, no unsafe DOM sinks, no
         WebSocket/EventSource, CSP in the server), the accessibility hooks
         the page needs, the hero screenshot asset, and (when Node is
         available) that every ES module parses.
Used by: pact-plugin test suite (standing merge gate).

This is a static gate: nothing here imports the visualizer package, starts the
server, or reaches the network. The behavioral backend/server contracts live
in test_visualizer_data.py and test_visualizer_server.py; the slash-command
surface lives in test_visualizer_command.py.

Why the scans strip comments and honor reserved URLs: the front-end and demo
data legitimately MENTION the banned shapes (a comment saying "no innerHTML",
an XML namespace in the SVG, a fictional `https://example.invalid/pull/42`
pull-request URL in the demo payload). Matching raw text would fail on correct
work, so the checks below operate on code with comments removed and allow the
non-routable documentation domains plus the reserved XML namespaces.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from visualizer_node import require_node

PLUGIN_ROOT = Path(__file__).parent.parent
VISUALIZER = PLUGIN_ROOT / "visualizer"
STATIC_DIR = VISUALIZER / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
BIN_PACT_VIZ = PLUGIN_ROOT / "bin" / "pact-viz"
HERO_PATH = VISUALIZER / "assets" / "hero.png"

# CONTRACT.md "Package layout (final)" is the interface contract for this
# package, so every path below is required rather than optional. A new file
# may be added freely; a contract file that disappears is a broken interface.
REQUIRED_FILES = (
    "visualizer/__init__.py",
    "visualizer/__main__.py",
    "visualizer/cli.py",
    "visualizer/settings.py",
    "visualizer/discovery.py",
    "visualizer/journal.py",
    "visualizer/roster.py",
    "visualizer/classify.py",
    "visualizer/model.py",
    "visualizer/demo.py",
    "visualizer/server.py",
    "visualizer/CONTRACT.md",
    "visualizer/README.md",
    "visualizer/static/index.html",
    "visualizer/static/favicon.svg",
    "visualizer/static/dev-demo.json",
    "visualizer/static/css/base.css",
    "visualizer/static/css/stage.css",
    "visualizer/static/css/panels.css",
    "visualizer/static/js/api.js",
    "visualizer/static/js/dom.js",
    "visualizer/static/js/app.js",
    "visualizer/static/js/sprites.js",
    "visualizer/static/js/stage.js",
    "visualizer/static/js/panels.js",
    "visualizer/static/js/timeline.js",
    "visualizer/static/js/charts.js",
    "visualizer/static/js/audio.js",
    "visualizer/static/js/achievements.js",
)

# Text assets that can carry a loadable reference. The hero image is binary
# and excluded deliberately; it is checked separately.
TEXT_ASSET_SUFFIXES = (".html", ".css", ".js", ".json", ".svg")

# URLs that are not network references: the reserved XML namespaces the SVG
# and HTML shells declare, and the RFC 2606 documentation/example domains the
# demo payload may display as text. A real CDN/API/font/telemetry host matches
# none of these and is reported.
ALLOWED_URL_PREFIXES = ("http://www.w3.org/",)
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_HOST_SUFFIXES = (".invalid",)
ALLOWED_DOC_HOSTS = frozenset(
    {"example.com", "example.net", "example.org", "www.example.com"}
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# DOM sinks that would bypass the text-node-only rendering contract. `eval(`
# and `new Function(` are included: the page must not synthesize code from
# journal content, which is untrusted input.
FORBIDDEN_DOM_SINKS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function(",
)

def _text_assets() -> list[Path]:
    return [
        path
        for path in sorted(STATIC_DIR.rglob("*"))
        if path.is_file() and path.suffix in TEXT_ASSET_SUFFIXES
    ]


def _js_files() -> list[Path]:
    return sorted(STATIC_JS_DIR.glob("*.js"))


def _strip_js_comments(source: str) -> str:
    """Return `source` with `//` and `/* ... */` comments removed.

    A quote-aware scanner rather than a regex: `//` inside a string literal
    (a URL) is ordinary text, and a `/*` sequence inside a string is not a
    comment. The stripped text is used only for presence scans, so string
    contents are deliberately left in place.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    state = "code"
    quote = ""
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state = "line"
                index += 2
                continue
            if char == "/" and nxt == "*":
                state = "block"
                index += 2
                continue
            if char in ("'", '"', "`"):
                state = "string"
                quote = char
            out.append(char)
            index += 1
            continue
        if state == "line":
            if char == "\n":
                state = "code"
                out.append(char)
            index += 1
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                state = "code"
                index += 2
                continue
            index += 1
            continue
        # string state
        out.append(char)
        if char == "\\" and index + 1 < length:
            out.append(source[index + 1])
            index += 2
            continue
        if char == quote:
            state = "code"
        index += 1
    return "".join(out)


def _external_urls(text: str) -> list[str]:
    """Return the URLs in `text` that would leave the machine."""
    offenders: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0)
        if url.startswith(ALLOWED_URL_PREFIXES):
            continue
        host = (urlsplit(url).hostname or "").lower()
        if host in ALLOWED_HOSTS or host in ALLOWED_DOC_HOSTS:
            continue
        if host.endswith(ALLOWED_HOST_SUFFIXES):
            continue
        offenders.append(url)
    return offenders


# ─── Package layout ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("relpath", REQUIRED_FILES, ids=REQUIRED_FILES)
def test_contract_file_exists(relpath):
    path = PLUGIN_ROOT / relpath
    assert path.is_file(), (
        f"{relpath} is declared in visualizer/CONTRACT.md's final package "
        f"layout but is missing. Either restore the file or update the "
        f"contract first — the layout is the interface between the parallel "
        f"workstreams."
    )
    assert path.stat().st_size > 0, f"{relpath} exists but is empty"


def test_assets_directory_is_scannable():
    """Control: the asset walks below must reach real files. An empty walk
    would make the security scans vacuously green."""
    assets = _text_assets()
    assert assets, f"no text assets found under {STATIC_DIR}"
    assert len(_js_files()) >= 5, (
        f"expected the contract's JS modules under {STATIC_JS_DIR}, found "
        f"{[p.name for p in _js_files()]}"
    )


# ─── bin/pact-viz launcher ───────────────────────────────────────────────────


def test_bin_pact_viz_exists():
    assert BIN_PACT_VIZ.is_file(), (
        f"{BIN_PACT_VIZ} is missing. /PACT:visualize and the documented "
        f"direct launch both invoke this path."
    )


def test_bin_pact_viz_is_executable():
    assert BIN_PACT_VIZ.is_file()
    assert os.access(BIN_PACT_VIZ, os.X_OK), (
        "bin/pact-viz is not executable. A symlink onto PATH requires the "
        "execute bit; run `chmod +x pact-plugin/bin/pact-viz`."
    )
    assert BIN_PACT_VIZ.stat().st_mode & 0o111


def test_bin_pact_viz_has_bash_shebang():
    first_line = BIN_PACT_VIZ.read_text(encoding="utf-8").splitlines()[0]
    assert first_line in ("#!/usr/bin/env bash", "#!/bin/bash"), (
        f"bin/pact-viz must start with a bash shebang "
        f"(#!/usr/bin/env bash preferred for PATH-honoring portability); "
        f"got {first_line!r}"
    )


def test_bin_pact_viz_checks_the_python_floor():
    """The launcher must refuse an interpreter below the documented floor.

    Without the check, an old `python3` fails deep inside the package with a
    traceback instead of a one-line reason. The visualizer requires 3.12+.
    """
    text = BIN_PACT_VIZ.read_text(encoding="utf-8")
    assert "python3" in text, "bin/pact-viz does not invoke python3"
    assert re.search(r"3[.,]\s*12", text), (
        "bin/pact-viz carries no Python 3.12 floor check. The package is "
        "documented as 3.12+; the launcher must gate the interpreter version "
        "so an older python3 fails with a clear message."
    )


def test_bin_pact_viz_runs_the_visualizer_module():
    text = BIN_PACT_VIZ.read_text(encoding="utf-8")
    assert re.search(r"-m\s+visualizer|visualizer/__main__\.py", text), (
        "bin/pact-viz must execute the visualizer package (for example "
        "`python3 -m visualizer \"$@\"`); it currently does not name the "
        "package entry point."
    )


# ─── No external URLs in the shipped page ───────────────────────────────────


def test_scan_helper_catches_external_hosts():
    """Non-vacuity for the URL scan: a real external host is flagged, while
    the namespace/demo-domain exceptions stay quiet."""
    assert _external_urls(
        'import x from "https://cdn.example.com/lib.js"'
    ) == ["https://cdn.example.com/lib.js"]
    assert _external_urls('<svg xmlns="http://www.w3.org/2000/svg">') == []
    assert _external_urls('{"pr_url": "https://example.invalid/pull/42"}') == []
    assert _external_urls('const u = "http://127.0.0.1:8080/api/health";') == []


def test_static_assets_reference_no_external_urls():
    """No CDN scripts, remote fonts, images, API hosts, or beacon endpoints.

    The page is fetched from a loopback server and talks to that same origin
    only; a remote reference would break the offline/local-only guarantee.
    """
    findings = []
    for path in _text_assets():
        text = path.read_text(encoding="utf-8", errors="replace")
        for url in _external_urls(text):
            findings.append(f"{path.relative_to(PLUGIN_ROOT).as_posix()}: {url}")
    assert not findings, (
        "static asset(s) reference external URL(s); Mission Control must stay "
        "local-only and offline-safe:\n" + "\n".join(f"  - {f}" for f in findings)
    )


# ─── No unsafe DOM sinks, no push transports ────────────────────────────────


def test_comment_stripper_is_quote_aware():
    """Non-vacuity for the DOM-sink scan: a banned token in a comment is
    ignored, the same token in code is not, and a `//` inside a string is
    not mistaken for a comment."""
    assert "innerHTML" not in _strip_js_comments(
        "// we never use innerHTML\nconst x = 1;\n"
    )
    assert "innerHTML" in _strip_js_comments("node.innerHTML = value;\n")
    assert "/api/health" in _strip_js_comments('fetch("/api/health");\n')


def test_static_js_has_no_unsafe_dom_sinks():
    findings = []
    for path in _js_files():
        code = _strip_js_comments(path.read_text(encoding="utf-8"))
        for sink in FORBIDDEN_DOM_SINKS:
            if sink in code:
                findings.append(f"{path.name}: {sink}")
    assert not findings, (
        "static JS uses a DOM sink that bypasses the text-node rendering "
        "contract; journal content is untrusted input:\n"
        + "\n".join(f"  - {f}" for f in findings)
    )


def test_static_js_has_no_push_transport():
    """The server is plain HTTP polling; a WebSocket/EventSource client would
    imply a protocol the standard-library server does not implement."""
    findings = []
    for path in _js_files():
        code = _strip_js_comments(path.read_text(encoding="utf-8"))
        for transport in ("WebSocket", "EventSource"):
            if transport in code:
                findings.append(f"{path.name}: {transport}")
    assert not findings, (
        "static JS references a push transport the local server does not "
        f"provide: {findings}"
    )


def test_static_js_fetches_same_origin_only():
    findings = []
    absolute_fetch = re.compile(r"fetch\(\s*[`\"']\s*(?:https?:)?//")
    for path in _js_files():
        code = _strip_js_comments(path.read_text(encoding="utf-8"))
        for match in absolute_fetch.finditer(code):
            findings.append(f"{path.name}: {match.group(0)!r}")
    assert not findings, (
        "static JS fetches an absolute URL; every request must be same-origin "
        f"or relative: {findings}"
    )

    # Control: the API client must actually fetch, and do it relatively.
    api_source = _strip_js_comments(
        (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")
    )
    assert "fetch(" in api_source, (
        "api.js no longer contains a fetch call — the same-origin pin above "
        "would then pass over a client that fetches nothing."
    )
    assert re.search(r"fetch\(\s*[`\"']\./", api_source), (
        "api.js no longer fetches a relative path (expected e.g. "
        '`fetch("./dev-demo.json")`); the token-free mock mode depends on it.'
    )


# ─── Content-Security-Policy ────────────────────────────────────────────────


def _backend_sources() -> dict[str, str]:
    return {
        path.relative_to(PLUGIN_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(VISUALIZER.rglob("*.py"))
    }


def test_backend_sources_are_scannable():
    sources = _backend_sources()
    assert sources, (
        "no Python backend modules under visualizer/ — the CSP header and the "
        "loopback binding have nowhere to live, so the gate below would be "
        "vacuous."
    )


def test_csp_header_is_defined_in_the_server():
    joined = "\n".join(_backend_sources().values())
    assert "Content-Security-Policy" in joined, (
        "the visualizer server defines no Content-Security-Policy header. "
        "The contract requires the CSP on every response so the local page "
        "cannot be tricked into loading remote code."
    )
    for token in ("default-src", "script-src", "connect-src", "frame-ancestors", "base-uri"):
        assert token in joined, (
            f"the CSP in the visualizer server does not name {token!r}; the "
            "policy documented in CONTRACT.md must stay complete."
        )
    assert "'self'" in joined, (
        "the CSP does not restrict sources to 'self'; the page is same-origin "
        "only."
    )


# ─── Accessibility hooks ────────────────────────────────────────────────────


def test_index_html_has_aria_live_region():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "aria-live" in html, (
        "index.html has no aria-live region; the comms feed and toasts update "
        "asynchronously and a screen reader would announce nothing."
    )


def test_index_html_has_skip_link():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "skip-link" in html, (
        "index.html has no skip-link; keyboard users cannot bypass the header "
        "controls."
    )
    assert 'href="#layout"' in html, (
        "the skip-link does not target the main layout anchor; it would be a "
        "decorative no-op."
    )


class _Controls(HTMLParser):
    """Collect each header button and labelled select from index.html."""

    def __init__(self):
        super().__init__()
        self.buttons = []  # (attrs, text)
        self.selects = []  # (attrs, visible label text)
        self._button = None
        self._pick_label = None
        self._in_pick_label = False
        self._label_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button":
            self._button = (attrs, [])
        elif tag == "span" and "pick-label" in (attrs.get("class") or ""):
            self._in_pick_label = True
            self._label_text = ""
        elif tag == "select":
            self.selects.append((attrs, self._label_text))

    def handle_endtag(self, tag):
        if tag == "button" and self._button is not None:
            attrs, parts = self._button
            self.buttons.append((attrs, "".join(parts).strip()))
            self._button = None
        elif tag == "span" and self._in_pick_label:
            self._in_pick_label = False

    def handle_data(self, data):
        if self._button is not None:
            self._button[1].append(data)
        if self._in_pick_label:
            self._label_text += data


def test_icon_buttons_and_selects_have_accessible_names():
    """A glyph is not a name, and a name must contain its visible label.

    A button's content wins over its title in name computation, so a button
    whose text is a glyph or a terse code needs aria-label. A select's
    aria-label overrides its visible label, so it must contain that text
    (WCAG label-in-name).
    """
    parser = _Controls()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    assert parser.buttons and parser.selects, "the control scan found nothing"
    for attrs, text in parser.buttons:
        if re.fullmatch(r"[A-Za-z][A-Za-z ]{3,}", text):
            continue
        if attrs.get("id") in {"live-badge", "btn-demo", "error-retry", "error-demo"}:
            continue
        assert (attrs.get("aria-label") or "").strip(), (
            f"button #{attrs.get('id')} shows {text!r} and has no aria-label"
        )
    for attrs, label in parser.selects:
        name = (attrs.get("aria-label") or "").lower()
        assert label.strip().lower() in name, (
            f"select #{attrs.get('id')} is labelled {label!r} on screen but named {name!r}"
        )


def test_widget_roles_match_their_children():
    """No list of buttons, and no tab roles without the tab pattern."""
    panels = _strip_js_comments((STATIC_JS_DIR / "panels.js").read_text(encoding="utf-8"))
    assert 'role: "list"' not in panels, "a role=list container holds button rows"
    assert 'role: "tab"' not in panels and '"tablist"' not in panels, (
        "tab roles promise arrow-key navigation and tab panels the debrief does not have"
    )
    stage = _strip_js_comments((STATIC_JS_DIR / "stage.js").read_text(encoding="utf-8"))
    assert "Interactive:" not in stage, (
        "the canvas label promises interaction the canvas does not offer to the keyboard"
    )


def _css_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(STATIC_DIR.rglob("*.css")))


def test_no_text_below_the_11px_floor():
    """Stylesheet text and the debrief chart labels stay at 11px or larger."""
    small = [
        size for size in re.findall(r"font-size:\s*([0-9.]+)px", _css_text()) if float(size) < 11
    ]
    assert not small, f"font sizes below the 11px floor: {sorted(set(small))}"
    charts = (STATIC_JS_DIR / "charts.js").read_text(encoding="utf-8")
    sizes = [int(size) for size in re.findall(r"setFont\(ctx,\s*(\d+)\)", charts)]
    assert sizes and min(sizes) >= 11, f"chart label sizes below 11px: {sizes}"


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_text_tokens_meet_contrast_on_the_panel_backgrounds():
    """Each text colour token clears 4.5:1 on each background text sits on."""
    tokens = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", _css_text()))
    assert {"text", "muted", "dim", "bg", "panel", "panel-2"} <= tokens.keys()
    for foreground in ("text", "muted", "dim"):
        for background in ("bg", "panel", "panel-2"):
            ratio = _contrast(tokens[foreground], tokens[background])
            assert ratio >= 4.5, f"--{foreground} on --{background} is {ratio:.2f}:1"
    assert not re.search(r"(?<![-\w])color:\s*rgba\([^)]*,\s*0?\.\d+\s*\)", _css_text()), (
        "a text colour uses partial alpha, which lowers its contrast below the token's"
    )


def test_reduced_motion_is_respected():
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(STATIC_DIR.rglob("*.css"))
    )
    js = "\n".join(
        _strip_js_comments(path.read_text(encoding="utf-8"))
        for path in _js_files()
    )
    assert "prefers-reduced-motion" in css + js, (
        "neither the stylesheet nor the JS honors prefers-reduced-motion; the "
        "stage animation must be suppressible for motion-sensitive users."
    )


# ─── Hero screenshot ────────────────────────────────────────────────────────


def test_hero_asset_exists():
    """The README embeds assets/hero.png; the file is a captured screenshot of
    the running viewer and is required to exist."""
    assert HERO_PATH.is_file(), (
        f"{HERO_PATH} is missing. It is the captured screenshot embedded in "
        "visualizer/README.md. Capture the running demo view at desktop size "
        "and commit the PNG; do not weaken this gate by deleting the asset "
        "reference."
    )


def test_hero_asset_is_a_png_under_two_megabytes():
    assert HERO_PATH.is_file(), f"{HERO_PATH} is missing (see previous test)"
    data = HERO_PATH.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", (
        f"{HERO_PATH} is not a PNG despite its extension. Capture the "
        "screenshot as PNG."
    )
    assert 0 < len(data) < 2 * 1024 * 1024, (
        f"hero.png is {len(data)} bytes; the budget is under 2 MB so the "
        "plugin checkout stays light. Re-capture or optimize the PNG."
    )


# ─── JS syntax ──────────────────────────────────────────────────────────────


def test_js_modules_parse():
    """Parse every ES module with `node --check`.

    The files are copied to a temporary `.mjs` suffix so Node parses them as
    modules regardless of the checkout's package.json situation; the copy is
    content-identical.
    """
    node = require_node("the JS module syntax check (the URL, DOM-sink, transport and CSP scans still run)")
    js_files = _js_files()
    assert js_files, "no JS modules to check"
    with tempfile.TemporaryDirectory() as tmp_dir:
        for path in js_files:
            target = Path(tmp_dir) / f"{path.name}.mjs"
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            proc = subprocess.run(
                [node, "--check", str(target)],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"{path.name} does not parse as an ES module:\n{proc.stderr}"
            )
