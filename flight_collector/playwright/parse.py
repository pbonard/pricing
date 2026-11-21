"""Google's results, as the browser gets them, into flights and legs.

The page asks Google for its results with GetShoppingResults. The answer is Google's batchexecute format: a
)]}' line, then lines of length and JSON. The results are in the ["wrb.fr", ...] lines, as a JSON string holding
a payload laid out like the page's ds:1 script:

    payload[2][0]   the top flights Google shows first
    payload[3][0]   every other flight
    result[0]       the itinerary: [1] airline names, [2] legs, [22][7] / [22][8] carbon and typical carbon (g)
    result[1][0][1] the price, missing when Google has none ("Total price is unavailable")
    leg             [3] / [6] from / to, [8] / [10] departure / arrival time, [20] / [21] their dates,
                    [11] minutes in the air, [17] plane, [22] carrier: [code, number, None, name]

Every value is checked before it's handed on as a Flight (flight_collector/common/model.py): a result whose flight data is wrong is
skipped with the reason, a leg's carrier that doesn't look right is left out (null), so a misread never lands in a
table.
"""

import json
from datetime import date, datetime

from ..common.model import AIRPORT, Flight, Leg, carrier, carriers_match, grams, text, whole, without_carriers


def payloads(body: str) -> list:
    """Every results payload in a GetShoppingResults answer. An error answer has none."""
    found = []
    for line in body.split("\n"):
        if not line.startswith('[["wrb.fr"'):
            continue
        try:
            frame = json.loads(line)
            if isinstance(frame[0][2], str):
                found.append(json.loads(frame[0][2]))
        except (ValueError, IndexError, TypeError):
            continue
    return found


def raw_results(payload) -> list[tuple[bool, list]]:
    """(top, result) in page order, top flights first."""
    out = []
    for index, top in ((2, True), (3, False)):
        try:
            block = payload[index][0]
        except (IndexError, TypeError):
            continue
        out.extend((top, k) for k in block or [] if isinstance(k, list))
    return out


def _time(value) -> tuple[int, int]:
    """Google drops trailing zero parts and writes a leading one as null: [8] is 08:00, [None, 31] is 00:31."""
    padded = [*(value or []), None, None]
    return padded[0] or 0, padded[1] or 0


def _datetime(day, time) -> datetime:
    return datetime(*day, *_time(time))


def _leg(raw) -> Leg:
    """Raises ValueError with the reason when the leg can't be saved as it is."""
    for code in (raw[3], raw[6]):
        if not (isinstance(code, str) and AIRPORT.fullmatch(code)):
            raise ValueError(f"airport {code!r}")
    if not (whole(raw[11]) and raw[11] > 0):
        raise ValueError(f"duration {raw[11]!r}")
    try:
        depart_at, arrive_at = _datetime(raw[20], raw[8]), _datetime(raw[21], raw[10])
    except (TypeError, ValueError) as e:
        raise ValueError(f"time {e}") from None
    found = carrier(raw[22]) if len(raw) > 22 else None
    return Leg(
        from_airport=raw[3],
        to_airport=raw[6],
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_min=raw[11],
        plane_type=text(raw[17]),
        airline_code=found[0] if found else None,
        airline=found[2] if found else None,
        flight_number=f"{found[0]}{found[1]}" if found else None,
    )


def flight(rank: int, top: bool, raw, depart: date) -> Flight:
    """One result. Raises ValueError with the reason when it can't be saved as it is."""
    try:
        itinerary, airlines, raw_legs = raw[0], raw[0][1], raw[0][2]
    except (IndexError, TypeError):
        raise ValueError("not a result") from None
    if not isinstance(raw_legs, list) or not raw_legs:
        raise ValueError("no legs")
    try:
        legs = [_leg(leg) for leg in raw_legs]
    except (IndexError, TypeError):
        raise ValueError("leg not as expected") from None

    try:
        price = raw[1][0][1]
    except (IndexError, TypeError):
        price = None
    if price is not None and not (whole(price) and price > 0):
        raise ValueError(f"price {price!r}")
    # Google lists flights leaving on the searched day, another day means the dates were read from the wrong place
    if legs[0].depart_at.date() != depart:
        raise ValueError(f"departs {legs[0].depart_at.date()}, searched {depart}")

    airlines = [a for a in airlines if isinstance(a, str)] if isinstance(airlines, list) else []
    # carriers that don't agree with Google's airlines of the result are left out rather than saved wrong
    if not carriers_match(legs, airlines):
        legs = without_carriers(legs)

    extras = itinerary[22] if len(itinerary) > 22 and isinstance(itinerary[22], list) else []
    return Flight(
        rank=rank,
        top=top,
        price=price,
        airlines=airlines,
        legs=legs,
        carbon_g=grams(extras[7]) if len(extras) > 7 else None,
        typical_carbon_g=grams(extras[8]) if len(extras) > 8 else None,
    )


def flights(payload, depart: date) -> tuple[list[Flight], list[str]]:
    """The results of a payload that check out, and why each other one was skipped (#rank reason)."""
    found, skipped = [], []
    for rank, (top, raw) in enumerate(raw_results(payload)):
        try:
            found.append(flight(rank, top, raw, depart))
        except ValueError as e:
            skipped.append(f"#{rank} {e}")
    return found, skipped
