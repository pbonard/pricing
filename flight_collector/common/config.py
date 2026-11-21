"""What a collector searches: the environment, checked into a Config. Shared by both collectors.

In k8s the environment comes from the collector's ConfigMap, made from k8s/helm/values.yaml, read when each run
starts. Every value is checked before anything is searched, a bad one fails the run with the reason.
"""

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SEATS = ("economy", "premium-economy", "business", "first")
TRIPS = ("round-trip", "one-way")
AIRPORT = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class Config:
    # (origin, destination) pairs, city or airport codes
    routes: list[tuple[str, str]]
    # a route's own nights of stay, ROUTES' SEL-BER:7. routes without one take stay_nights
    route_stay_nights: dict[tuple[str, str], int]
    # what DAYS_AHEAD counts from, None is the day of the run
    base_date: date | None
    # in the order given: the rotation takes them in this order
    days_ahead: list[int]
    trip: str
    # nights between departure and return, a search for each, for routes without their own. empty for one-way
    stay_nights: list[int]
    seat: str
    adults: int
    # None is any number of stops
    max_stops: int | None
    currency: str
    proxy: str | None
    start_jitter_max: float
    # search and log, but write nothing: to try a config without touching the data
    dry_run: bool


def load_config(env=os.environ) -> Config:
    """Reads the environment. Every problem is collected and raised together, so one failed run shows them all.

    ROUTES                      SEL-TYO,SEL-OSA   origin-destination pairs, city (SEL) or airport (ICN) codes. a pair
                                                  may have its own nights of stay: SEL-BER:7
    BASE_DATE                   (empty)           one date, YYYY-MM-DD, that DAYS_AHEAD counts from. empty is the
                                                  day of the run (KST). once it has passed the run ends at once
    DAYS_AHEAD                  7,14,30,60        days after BASE_DATE to search, 0 is BASE_DATE itself. empty is 0.
                                                  each run does the next one (see next_search), in this order
    TRIP                        round-trip        round-trip or one-way
    STAY_NIGHTS                 3                 round trip: nights between departure and return for the routes
                                                  without their own, 2,3,4 searches each. ignored for one-way
    SEAT                        economy           economy, premium-economy, business or first
    ADULTS                      1                 1 to 9, the price is for all of them
    MAX_STOPS                   (empty)           empty for any, 0 for nonstop only
    CURRENCY                    KRW               ISO 4217
    PROXY                       (empty)           http://user:pass@host:port for Google's requests
    START_JITTER_MAX_SECONDS    1200              random wait before the search, up to this
    DRY_RUN                     false             true searches and logs, writes nothing
    """
    problems: list[str] = []

    def get(name: str, default: str) -> str:
        return env.get(name, default).strip()

    def items(name: str, default: str) -> list[str]:
        return [v.strip() for v in get(name, default).split(",") if v.strip()]

    def number(name: str, default: str, kind=float, low=0, high=None):
        raw = get(name, default)
        try:
            value = kind(raw)
        except ValueError:
            problems.append(f"{name}={raw!r} is not a {'whole number' if kind is int else 'number'}")
            return kind(default)
        if value < low or (high is not None and value > high):
            problems.append(f"{name}={raw!r} is below {low}" if high is None else f"{name}={raw!r} is out of {low}..{high}")
        return value

    routes, route_stay_nights = [], {}
    for route in items("ROUTES", "SEL-TYO,SEL-OSA"):
        pair, _, stay = route.partition(":")
        parts = pair.strip().upper().split("-")
        if not (len(parts) == 2 and all(AIRPORT.fullmatch(p) for p in parts) and parts[0] != parts[1]):
            problems.append(f"ROUTES has {route!r}, not ORIGIN-DESTINATION of two different 3 letter codes")
            continue
        key = (parts[0], parts[1])
        if key in routes:
            problems.append(f"ROUTES has {pair.strip().upper()} twice")
            continue
        routes.append(key)
        if stay.strip():
            if stay.strip().isdigit() and 1 <= int(stay) <= 30:
                route_stay_nights[key] = int(stay)
            else:
                problems.append(f"ROUTES has {route!r}, its stay {stay.strip()!r} is not a number of nights from 1 to 30")
    if not routes and not problems:
        problems.append("ROUTES is empty")

    base_raw = get("BASE_DATE", "")
    base_date = None
    if base_raw:
        try:
            base_date = date.fromisoformat(base_raw)
        except ValueError:
            problems.append(f"BASE_DATE={base_raw!r} is not one YYYY-MM-DD date")

    days_ahead = []
    for n in items("DAYS_AHEAD", "7,14,30,60"):
        if n.isdigit() and int(n) <= 330:
            days_ahead.append(int(n))
        else:
            # Google sells about 11 months ahead
            problems.append(f"DAYS_AHEAD has {n!r}, not a number of days from 0 to 330")

    trip = get("TRIP", "round-trip").lower()
    if trip not in TRIPS:
        problems.append(f"TRIP={trip!r} is not one of {', '.join(TRIPS)}")
    stay_nights = []
    if trip == "round-trip":
        for n in items("STAY_NIGHTS", "3"):
            if n.isdigit() and 1 <= int(n) <= 30:
                stay_nights.append(int(n))
            else:
                problems.append(f"STAY_NIGHTS has {n!r}, not a number of nights from 1 to 30")
        without_own = [f"{o}-{d}" for o, d in routes if (o, d) not in route_stay_nights]
        if not stay_nights and without_own:
            problems.append(f"STAY_NIGHTS is empty, and {', '.join(without_own)} has no stay of its own: "
                            "a round trip needs a return")

    seat = get("SEAT", "economy").lower()
    if seat not in SEATS:
        problems.append(f"SEAT={seat!r} is not one of {', '.join(SEATS)}")

    adults = number("ADULTS", "1", int, 1, 9)
    max_stops_raw = get("MAX_STOPS", "")
    max_stops = number("MAX_STOPS", "0", int, 0, 3) if max_stops_raw else None

    currency = get("CURRENCY", "KRW").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        problems.append(f"CURRENCY={currency!r} is not a 3 letter ISO 4217 code")


    dry_run_raw = get("DRY_RUN", "false").lower()
    if dry_run_raw not in ("true", "false"):
        problems.append(f"DRY_RUN={dry_run_raw!r} is not true or false")

    config = Config(
        routes=routes,
        route_stay_nights=route_stay_nights,
        base_date=base_date,
        days_ahead=days_ahead,
        trip=trip,
        stay_nights=sorted(set(stay_nights)),
        seat=seat,
        adults=adults,
        max_stops=max_stops,
        currency=currency,
        proxy=get("PROXY", "") or None,
        start_jitter_max=number("START_JITTER_MAX_SECONDS", "1200"),
        dry_run=dry_run_raw == "true",
    )
    if problems:
        raise ValueError("bad config:\n  " + "\n  ".join(problems))
    return config


def stale(config: Config, today: date) -> bool:
    """BASE_DATE has passed: nothing is left to search from it, a run ends at once."""
    return config.base_date is not None and config.base_date < today


def departure_dates(config: Config, today: date, offsets: list[int] | None = None) -> list[date]:
    """BASE_DATE (today when it's empty) plus each offset, by default all of DAYS_AHEAD. Nothing once BASE_DATE has
    passed. A fixed BASE_DATE follows the same trips up to the day, today as the base shows how far out is cheapest."""
    if stale(config, today):
        return []
    base = config.base_date or today
    offsets = offsets if offsets is not None else (config.days_ahead or [0])
    return sorted({base + timedelta(days=n) for n in offsets})


Search = tuple[str, str, date, date | None]


def plan(config: Config, today: date) -> list[Search]:
    """Every search, as (origin, destination, departure, return or None), in the rotation's order: DAYS_AHEAD as
    given, each of its dates for every route in ROUTES' order, every stay of the route. Nothing once BASE_DATE has
    passed."""
    return [
        (origin, destination, depart, depart + stay if stay else None)
        for offset in (config.days_ahead or [0])
        for depart in departure_dates(config, today, [offset])
        for origin, destination in config.routes
        for stay in stays(config, (origin, destination))
    ]


def stays(config: Config, route: tuple[str, str]) -> list[timedelta | None]:
    """A route's stays: its own from ROUTES, otherwise STAY_NIGHTS. [None] for one-way, it has no return."""
    if config.trip != "round-trip":
        return [None]
    if route in config.route_stay_nights:
        return [timedelta(days=config.route_stay_nights[route])]
    return [timedelta(days=n) for n in config.stay_nights]


def _position(config: Config, today: date, search: Search) -> tuple:
    """Where a search is in the rotation, the same from one day to the next: its route, how many days after the
    base date it leaves, and its stay. The date itself moves with the day when the base is the day of the run."""
    origin, destination, depart, return_date = search
    base = config.base_date or today
    return origin, destination, (depart - base).days, (return_date - depart).days if return_date else None


def next_search(config: Config, today: date, last: Search | None, last_day: date | None = None,
                last_failed: bool = False) -> Search | None:
    """The search of this run: the one after the last saved one (last, saved on last_day) in the rotation, the
    first one when nothing was saved yet or the last one isn't in the plan any more (the config changed). None when
    there's nothing to search. So how often a search comes is the cronjob's schedule alone.

    When the last one failed (last_failed, blocked or error) it's done again instead: the rotation doesn't move on
    until it worked. Again is its place in the rotation, Tokyo +1 the next day is the new day's +1."""
    planned = plan(config, today)
    if not planned:
        return None
    if last is None:
        return planned[0]
    positions = [_position(config, today, s) for s in planned]
    previous = _position(config, last_day or today, last)
    if previous not in positions:
        return planned[0]
    return planned[(positions.index(previous) + (0 if last_failed else 1)) % len(planned)]
