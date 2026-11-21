"""The playwright collector: python -m flight_collector.playwright, from the repo root.

A plain request for the search page gets about 50 results, the ones Google puts on the page before any script
runs, and that's what the fast-flights collector reads. This one opens the search in headless Chromium, clicks
"View more flights" and reads the results the page asks Google for: the whole list (about 210 for Seoul to Tokyo),
top flights included. See browser.py.

What to search and the run itself are flight_collector/common/ (config.py, run.py), the same as the fast-flights collector's.
One more setting is its own:

    PAGE_TIMEOUT_SECONDS   60   how long a search's page may take to finish

Google's page is in English (LANGUAGE): airline names are saved as Google writes them in English.
"""

import os
from contextlib import contextmanager
from datetime import date

from ..common import run
from ..common.config import Config

from . import parse, query
from .browser import Browser

LANGUAGE = "en-US"


def legs(origin: str, destination: str, depart: date, return_date: date | None) -> list[query.Leg]:
    out = [query.Leg(depart, origin, destination)]
    if return_date:
        out.append(query.Leg(return_date, destination, origin))
    return out


def page_timeout(env=os.environ) -> float:
    raw = env.get("PAGE_TIMEOUT_SECONDS", "60").strip()
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"bad config:\n  PAGE_TIMEOUT_SECONDS={raw!r} is not a number") from None
    if not 10 <= value <= 300:
        raise ValueError(f"bad config:\n  PAGE_TIMEOUT_SECONDS={raw!r} is out of 10..300")
    return value


def open_search(timeout: float):
    """The run's search: one browser for the whole run, a page per search."""

    @contextmanager
    def opened(config: Config):
        with Browser(LANGUAGE, timeout) as browser:
            def search(origin: str, destination: str, depart: date, return_date: date | None):
                url = query.search_url(
                    legs(origin, destination, depart, return_date), config.trip, config.seat, config.adults,
                    config.max_stops, config.currency, LANGUAGE,
                )
                page = browser.load(url)
                if page.status != "ok":
                    return page.status, [], page.error
                flights, skipped = parse.flights(page.payload, depart)
                if skipped:
                    run.log.warning("skipped %d results: %s", len(skipped), "; ".join(skipped))
                    return "ok", flights, f"skipped {len(skipped)} invalid results: {'; '.join(skipped)}"[:1000]
                return "ok", flights, None

            yield search

    return opened


def main() -> int:
    try:
        timeout = page_timeout()
    except ValueError as e:
        run.setup_logging()
        run.log.error("%s", e)
        return 2
    return run.main(open_search(timeout))

