"""Browser layer.

`Session` is the narrow interface the rest of the tool uses. `PlaywrightSession`
drives real Chrome through Playwright's sync API with a persistent, dedicated
profile; tests swap in `tests/fakes.py::FakeSession`.

Rules enforced here:
- The profile directory may never be your everyday Chrome profile.
- Top-level navigation is allowed to linkedin.com and its asset hosts only.
  Anything else aborts the run. Third-party subresources are blocked silently.
- No `page.evaluate` anywhere, so scraped text can never become script.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol, Sequence
from urllib.parse import urlsplit

from linkreach.safety import DomainViolation

ALLOWED_HOST_SUFFIXES = ("linkedin.com", "licdn.com")
# Non-http(s) schemes the browser uses internally.
INTERNAL_SCHEMES = {"about", "data", "blob", "chrome-error", "chrome"}

Selector = str | Sequence[str]


class ProfileGuardError(Exception):
    pass


def default_chrome_dirs() -> list[Path]:
    """Your everyday browser profile roots on each OS."""
    home = Path.home()
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        dirs += [
            local / "Google" / "Chrome" / "User Data",
            local / "Google" / "Chrome Beta" / "User Data",
            local / "Google" / "Chrome SxS" / "User Data",
            local / "Chromium" / "User Data",
            local / "Microsoft" / "Edge" / "User Data",
        ]
    elif sys.platform == "darwin":
        sup = home / "Library" / "Application Support"
        dirs += [sup / "Google" / "Chrome", sup / "Google" / "Chrome Beta", sup / "Chromium", sup / "Microsoft Edge"]
    else:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        dirs += [cfg / "google-chrome", cfg / "google-chrome-beta", cfg / "chromium", cfg / "microsoft-edge"]
    return [d.resolve() for d in dirs]


def guard_profile_dir(path: Path) -> None:
    """Refuse the default Chrome profile (or anything inside it)."""
    p = Path(path).expanduser().resolve()
    for root in default_chrome_dirs():
        if p == root or root in p.parents:
            raise ProfileGuardError(
                f"chrome_profile_dir {p} is your default browser profile. "
                "Use a dedicated profile such as ~/.linkreach/chrome-profile."
            )
    if (p.parent / "Local State").exists():
        raise ProfileGuardError(f"chrome_profile_dir {p} is a profile inside another Chrome user-data folder")


def host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme in INTERNAL_SCHEMES:
        return True
    if parts.scheme not in {"http", "https"}:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def _as_list(sel: Selector) -> list[str]:
    return [sel] if isinstance(sel, str) else list(sel)


class Session(Protocol):
    """What linkedin.py needs from a browser. Every selector arg accepts one CSS
    selector or a list of fallbacks tried in order."""

    @property
    def url(self) -> str: ...

    def goto(self, url: str) -> None: ...
    def exists(self, selector: Selector, timeout_ms: int = 0) -> bool: ...
    def text(self, selector: Selector, timeout_ms: int = 0) -> str | None: ...
    def texts(self, selector: Selector) -> list[str]: ...
    def cards(self, card: Selector, fields: dict[str, tuple[Selector, str | None]]) -> list[dict[str, str]]: ...
    def click(self, selector: Selector, timeout_ms: int = 5000) -> bool: ...
    def fill(self, selector: Selector, value: str, timeout_ms: int = 5000) -> bool: ...
    def press(self, key: str) -> None: ...
    def body_text(self) -> str: ...
    def screenshot(self, path: Path) -> None: ...
    def check_domain(self) -> None: ...
    def close(self) -> None: ...


class PlaywrightSession:
    """Real Chrome, persistent dedicated profile, sync API."""

    def __init__(self, profile_dir: Path, channel: str = "chrome", headless: bool = False):
        guard_profile_dir(profile_dir)
        from playwright.sync_api import sync_playwright

        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(profile_dir, 0o700)
        except OSError:
            pass
        self._pw = sync_playwright().start()
        self._violation: str | None = None
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=channel or None,
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        self._ctx.route("**/*", self._route)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.on("framenavigated", self._on_nav)

    # ----- domain allowlist -----
    def _route(self, route, request) -> None:
        url = request.url
        if host_allowed(url):
            route.continue_()
            return
        try:
            is_top = request.is_navigation_request() and request.frame == self.page.main_frame
        except Exception:
            is_top = False
        if is_top:
            self._violation = urlsplit(url).hostname or url[:80]
        route.abort()

    def _on_nav(self, frame) -> None:
        try:
            if frame == self.page.main_frame and not host_allowed(frame.url):
                self._violation = urlsplit(frame.url).hostname or frame.url[:80]
        except Exception:
            pass

    def check_domain(self) -> None:
        if self._violation or not host_allowed(self.page.url):
            raise DomainViolation(f"navigation to a non-LinkedIn domain: {self._violation or self.page.url}")

    # ----- interface -----
    @property
    def url(self) -> str:
        return self.page.url

    def goto(self, url: str) -> None:
        if not host_allowed(url):
            raise DomainViolation(f"refusing to open non-LinkedIn URL: {urlsplit(url).hostname}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        self.check_domain()

    def _first(self, selector: Selector, timeout_ms: int):
        sels = _as_list(selector)
        if timeout_ms:
            try:
                self.page.locator(", ".join(sels)).first.wait_for(state="visible", timeout=timeout_ms)
            except Exception:
                pass
        for s in sels:
            loc = self.page.locator(s).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def exists(self, selector: Selector, timeout_ms: int = 0) -> bool:
        return self._first(selector, timeout_ms) is not None

    def text(self, selector: Selector, timeout_ms: int = 0) -> str | None:
        loc = self._first(selector, timeout_ms)
        if loc is None:
            return None
        try:
            return loc.inner_text(timeout=2000)
        except Exception:
            return None

    def texts(self, selector: Selector) -> list[str]:
        for s in _as_list(selector):
            try:
                vals = [t for t in self.page.locator(s).all_inner_texts() if t.strip()]
            except Exception:
                vals = []
            if vals:
                return vals
        return []

    def cards(self, card: Selector, fields: dict[str, tuple[Selector, str | None]]) -> list[dict[str, str]]:
        for cs in _as_list(card):
            try:
                items = self.page.locator(cs).all()
            except Exception:
                items = []
            if not items:
                continue
            out = []
            for item in items:
                rec: dict[str, str] = {}
                for name, (fsel, attr) in fields.items():
                    rec[name] = ""
                    for s in _as_list(fsel):
                        loc = item.locator(s).first
                        try:
                            if not loc.count():
                                continue
                            val = loc.get_attribute(attr, timeout=1500) if attr else loc.inner_text(timeout=1500)
                        except Exception:
                            continue
                        if val and val.strip():
                            rec[name] = val.strip()
                            break
                out.append(rec)
            return out
        return []

    def click(self, selector: Selector, timeout_ms: int = 5000) -> bool:
        loc = self._first(selector, timeout_ms)
        if loc is None:
            return False
        try:
            loc.click(timeout=timeout_ms)
            self.check_domain()
            return True
        except DomainViolation:
            raise
        except Exception:
            return False

    def fill(self, selector: Selector, value: str, timeout_ms: int = 5000) -> bool:
        loc = self._first(selector, timeout_ms)
        if loc is None:
            return False
        try:
            loc.fill(value, timeout=timeout_ms)
            return True
        except Exception:
            return False

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    def body_text(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(path), full_page=False)
            os.chmod(path, 0o600)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._ctx.close()
        finally:
            self._pw.stop()
