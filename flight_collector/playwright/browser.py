"""Loading a Google Flights search in headless Chromium until its results are complete.

The page asks Google for its results with GetShoppingResults: first the ones it shows (about 50 for Seoul to Tokyo),
and the whole list (about 220) only once "View more flights" is clicked. So a page is loaded, its first answer
waited for, the button clicked while it's there, and the answer with the most results is the page's.
Flights still without a price are ones Google has no price for, the page says "Total price is unavailable".
"""

import re

import logging
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from . import parse

log = logging.getLogger("collector")

RESULTS_REQUEST = "GetShoppingResults"
# a page that took the traffic for a bot: Google's /sorry/ page or its captcha
BLOCK_MARKERS = ("/sorry/", "unusual traffic", "detected unusual", "recaptcha/api")
MORE_FLIGHTS = re.compile("more flights", re.I)
# the button has always gone after one click, a few more are allowed in case Google pages the list
MAX_CLICKS = 3


@dataclass(frozen=True)
class Page:
    # ok, not_found, blocked or error
    status: str
    # the most complete results payload, None unless ok
    payload: list | None
    error: str | None


class Browser:
    """One Chromium for a run, one page per search. The context keeps Google's cookies across the run's searches,
    like one person searching several times."""

    def __init__(self, language: str, timeout_seconds: float):
        self.language = language
        self.timeout_ms = timeout_seconds * 1000

    def __enter__(self) -> "Browser":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            args=[
                # k8s gives /dev/shm 64 MB, too small for Chromium: it uses /tmp instead
                "--disable-dev-shm-usage",
                # without it navigator.webdriver is true, the plainest sign of an automated browser
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context = self._browser.new_context(
            locale=self.language, timezone_id="Asia/Seoul", viewport={"width": 1400, "height": 1000},
            user_agent=self._user_agent(),
        )
        return self

    def _user_agent(self) -> str:
        """The browser's own user agent without "HeadlessChrome": the same version and operating system, so it
        still matches what else the browser says about itself, but reads like a normal Chrome."""
        probe = self._browser.new_page()
        try:
            return probe.evaluate("navigator.userAgent").replace("HeadlessChrome/", "Chrome/")
        finally:
            probe.close()

    def __exit__(self, *exc) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def load(self, url: str) -> Page:
        page = self._context.new_page()
        answers: list[str] = []

        def on_response(response) -> None:
            if RESULTS_REQUEST in response.url:
                try:
                    answers.append(response.text())
                except PlaywrightError:
                    pass

        page.on("response", on_response)
        try:
            with page.expect_response(lambda r: RESULTS_REQUEST in r.url, timeout=self.timeout_ms):
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
            more = page.get_by_role("button", name=MORE_FLIGHTS)
            for _ in range(MAX_CLICKS):
                if not more.count():
                    break
                with page.expect_response(lambda r: RESULTS_REQUEST in r.url, timeout=self.timeout_ms):
                    more.first.click(timeout=self.timeout_ms)
                page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
            return self._read(page, answers)
        except PlaywrightError as e:
            # no first answer in time is a page that isn't a results page: a block, or something else
            if answers:
                return self._read(page, answers)
            blocked = self._blocked(page)
            return Page("blocked", None, None) if blocked else Page("error", None, f"browser: {e}"[:1000])
        finally:
            page.close()

    @staticmethod
    def _blocked(page) -> bool:
        try:
            content = page.content().lower()
        except PlaywrightError:
            content = ""
        return any(m in page.url or m in content for m in BLOCK_MARKERS)

    @staticmethod
    def _read(page, bodies: list[str]) -> Page:
        payloads = [p for body in bodies for p in parse.payloads(body)]
        if payloads:
            best = max(payloads, key=lambda p: len(parse.raw_results(p)))
            if parse.raw_results(best):
                return Page("ok", best, None)
            return Page("not_found", None, None)
        if Browser._blocked(page):
            return Page("blocked", None, None)
        if any("ErrorResponse" in body for body in bodies):
            # the page says "Oops, something went wrong": Google refused the search, mostly after too many of them.
            # an error, so failures in a row stop the run
            return Page("error", None, "Google answered with an error (the page says something went wrong)")
        if bodies:
            # Google answered, without results and without an error
            return Page("not_found", None, None)
        return Page("error", None, f"no results answer, page {page.url[:200]} titled {page.title()[:100]!r}")
