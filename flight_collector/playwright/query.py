"""The Google Flights search URL, built without fast-flights.

Google takes the search as tfs=, a protobuf message in base64. Only the fields the collector sets are written, in
field number order, the way protobuf serializes them. The message, as reverse engineered by fast-flights:

    Info:       data = 3 (FlightData, one per direction), passengers = 8 (packed enum, 1 = adult), seat = 9,
                hide_separate_and_self_transfer = 17, trip = 19
    FlightData: date = 2 ("2026-12-20"), max_stops = 5, from_airport = 13, to_airport = 14
    Airport:    airport = 2 ("SEL", a city or an airport code)
"""

from base64 import b64encode
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

SEATS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}
TRIPS = {"round-trip": 1, "one-way": 2}
ADULT = 1
URL = "https://www.google.com/travel/flights/search"


@dataclass(frozen=True)
class Leg:
    """One direction of the search: a round trip has two, the return from destination back to origin."""

    date: date
    origin: str
    destination: str


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, wire_type: int) -> bytes:
    return _varint(number << 3 | wire_type)


def _int(number: int, value: int) -> bytes:
    return _field(number, 0) + _varint(value)


def _bytes(number: int, value: bytes) -> bytes:
    return _field(number, 2) + _varint(len(value)) + value


def _flight_data(leg: Leg, max_stops: int | None) -> bytes:
    out = _bytes(2, leg.date.isoformat().encode())
    if max_stops is not None:
        out += _int(5, max_stops)
    out += _bytes(13, _bytes(2, leg.origin.encode()))
    out += _bytes(14, _bytes(2, leg.destination.encode()))
    return out


def tfs(legs: list[Leg], trip: str, seat: str, adults: int, max_stops: int | None) -> str:
    """The search as Google's tfs= value. Separate tickets are always hidden, so every price is one ticket."""
    out = b"".join(_bytes(3, _flight_data(leg, max_stops)) for leg in legs)
    out += _bytes(8, b"".join(_varint(ADULT) for _ in range(adults)))
    out += _int(9, SEATS[seat])
    out += _int(17, 1)
    out += _int(19, TRIPS[trip])
    return b64encode(out).decode()


def search_url(legs: list[Leg], trip: str, seat: str, adults: int, max_stops: int | None, currency: str,
               language: str) -> str:
    return URL + "?" + urlencode({
        "tfs": tfs(legs, trip, seat, adults, max_stops), "hl": language, "curr": currency,
    })
