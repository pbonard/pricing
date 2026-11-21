"""What a search finds, the same for both collectors: a Flight per result with its Legs, as saved by db.py.

Each collector reads Google its own way and hands on Flights. The checks every value read from Google goes through
before it lands in one are here too, so both collectors keep and leave out the same things.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

# what a value read from Google has to look like before it's saved, anything else is a misread
AIRPORT = re.compile(r"[A-Z]{3}")
AIRLINE_CODE = re.compile(r"[A-Z0-9]{2,3}")
FLIGHT_NUMBER = re.compile(r"[0-9]{1,4}[A-Z]?")


@dataclass(frozen=True)
class Leg:
    from_airport: str
    to_airport: str
    # local time of each airport, as Google shows it
    depart_at: datetime
    arrive_at: datetime
    duration_min: int
    plane_type: str | None
    # all three or none: a carrier that doesn't check out is left out whole
    airline_code: str | None
    airline: str | None
    flight_number: str | None


@dataclass(frozen=True)
class Flight:
    # position on Google's page, 0 is the first
    rank: int
    # one of the top flights Google shows first (only the playwright collector sees them)
    top: bool
    price: int | None
    airlines: list[str]
    legs: list[Leg]
    carbon_g: int | None
    typical_carbon_g: int | None

    @property
    def stops(self) -> int:
        return len(self.legs) - 1

    @property
    def flying_min(self) -> int:
        return sum(leg.duration_min for leg in self.legs)

    @property
    def itinerary_key(self) -> str:
        """The same flights from one search to the next, with the search's departure date: the flight numbers,
        KE1105 or KE2102+JL308. When a leg has no number, a hash of the airlines and each leg's airports and
        departure stands in."""
        if all(leg.flight_number for leg in self.legs):
            return "+".join(leg.flight_number for leg in self.legs)
        legs = "|".join(
            f"{leg.from_airport}{leg.to_airport}{leg.depart_at.date().isoformat()}T{leg.depart_at:%H%M}"
            for leg in self.legs
        )
        return hashlib.sha1(f"{','.join(self.airlines)}#{legs}".encode()).hexdigest()[:16]


def without_carriers(legs: list[Leg]) -> list[Leg]:
    return [Leg(**{**leg.__dict__, "airline_code": None, "airline": None, "flight_number": None}) for leg in legs]


def carriers_match(legs: list[Leg], airlines: list[str]) -> bool:
    """The legs' airline names, each once in order, are Google's airlines of the result: two places of Google's data
    agree, so the carriers were read where they should be."""
    return all(leg.airline for leg in legs) and list(dict.fromkeys(leg.airline for leg in legs)) == list(airlines)


def carrier(raw) -> tuple[str, str, str] | None:
    """(code, number, name) of Google's [code, number, None, name], None when it doesn't look like one."""
    try:
        code, number, _, name = raw[:4]
    except (TypeError, ValueError):
        return None
    if (
        isinstance(code, str) and AIRLINE_CODE.fullmatch(code)
        and isinstance(number, str) and FLIGHT_NUMBER.fullmatch(number)
        and isinstance(name, str) and name.strip()
    ):
        return code, number, name
    return None


def whole(value) -> bool:
    """An int, not a bool (True is an int in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def text(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def grams(value) -> int | None:
    return value if whole(value) and value >= 0 else None
