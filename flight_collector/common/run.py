"""A collector's run, the same for both collectors: the next search of config.py's rotation, through the collector's
own way of searching Google, saved as soon as it's done.

A run does one search: the one after the search the run before saved, or that one again when it failed
(config.next_search). So the cronjob's schedule alone decides how often searches come, and the rotation goes round
DAYS_AHEAD, the routes and the stays. A run starts after a random delay (START_JITTER_MAX_SECONDS), so the search
doesn't come at the same minute every time.
"""

import logging
import random
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone

from . import db
from .config import KST, Config, load_config, next_search, stale
from .model import Flight

log = logging.getLogger("collector")

# a search: (origin, destination, departure, return or None) -> (status, flights, error).
# status is ok, not_found, blocked or error, error says why, or what was skipped
Search = Callable[[str, str, date, date | None], tuple[str, list[Flight], str | None]]


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)


def main(open_search: Callable[[Config], AbstractContextManager[Search]]) -> int:
    """Does this run's search. open_search gives the collector's search, as a context manager: the playwright
    collector opens its browser for it. Returns the exit code: 0 done (or nothing to do), 1 the search was blocked or
    failed, 2 bad config."""
    setup_logging()
    try:
        config = load_config()
    except ValueError as e:
        log.error("%s", e)
        return 2
    log.info("config: %s", config)

    now = datetime.now(KST)
    if stale(config, now.date()):
        # the date given is over, the job is done: it ends without searching, not as a failure
        log.warning("BASE_DATE %s has passed, nothing to search: set a new one or suspend the cronjob", config.base_date)
        return 0
    # schema first, so a bad DATABASE_URL fails the run before any request to Google, and the last search to go on
    # from. a dry run doesn't touch the database, it always shows the rotation's first search
    last, last_day, last_status = None, None, None
    if not config.dry_run:
        with db.connect() as conn:
            db.ensure_schema(conn)
            last, last_day, last_status = db.last_search(conn, config.routes) or (None, None, None)
    # a blocked or failed search is done again, the rotation goes on once it worked
    job = next_search(config, now.date(), last, last_day, last_failed=last_status in ("blocked", "error"))
    if job is None:
        log.warning("nothing to search")
        return 0

    origin, destination, depart, return_date = job
    wait = random.uniform(0, config.start_jitter_max)
    log.info("searching %s->%s %s%s, starting in %.0fs%s", origin, destination, depart,
             f" back {return_date}" if return_date else "", wait, ", dry run" if config.dry_run else "")
    time.sleep(wait)

    with open_search(config) as search:
        fetched_at = datetime.now(timezone.utc)
        status, flights, error = search(origin, destination, depart, return_date)
    prices = [f.price for f in flights if f.price is not None]
    log.info(
        "%s->%s %s%s: %s, %d flights (%d priced), cheapest %s %s",
        origin, destination, depart, f" back {return_date}" if return_date else "", status, len(flights),
        len(prices), min(prices) if prices else "-", error or "",
    )

    # a new connection for the save: the start delay is longer than an idle connection should be trusted to live
    if not config.dry_run:
        with db.connect() as conn:
            db.save(conn, config, uuid.uuid4(), fetched_at, origin, destination, depart, return_date, status, flights,
                    error)

    # a blocked or failed search is a failed job, so it shows in kubectl and alerts
    return 0 if status in ("ok", "not_found") else 1
