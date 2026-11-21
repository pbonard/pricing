"""Saving to Postgres, the same for both collectors: a search with its Flights and their Legs (model.py).

The database is DATABASE_URL, in k8s the CloudNativePG cluster's postgres-app secret. The tables are schema.sql,
next to this file, applied at the start of every run: it only adds what isn't there.
"""

import os
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from .config import Config, Search
from .model import Flight

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def connect() -> psycopg.Connection:
    """A connection to DATABASE_URL, each statement committed on its own unless in a transaction."""
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def ensure_schema(conn) -> None:
    conn.execute(SCHEMA.read_text())


def last_search(conn, routes: list[tuple[str, str]]) -> tuple[Search, date, str] | None:
    """The search of these routes saved last, failed ones too, the day (KST) it was saved and its status: where the
    rotation goes on from, or what it does again when it failed. Only these routes: another collector searching other
    routes into the same tables (its own cronjob and configmap) has its own rotation."""
    row = conn.execute(
        "SELECT origin, destination, depart_date, return_date, fetched_at, status FROM search "
        "WHERE origin || '-' || destination = ANY(%s) ORDER BY fetched_at DESC LIMIT 1",
        ([f"{origin}-{destination}" for origin, destination in routes],),
    ).fetchone()
    if row is None:
        return None
    origin, destination, depart, return_date, fetched_at, status = row
    return (origin, destination, depart, return_date), fetched_at.astimezone(ZoneInfo("Asia/Seoul")).date(), status


def flight_row(search_id, f: Flight) -> tuple:
    first, last = f.legs[0], f.legs[-1]
    return (
        search_id, f.rank, first.from_airport, last.to_airport, f.price, f.stops, first.depart_at, last.arrive_at,
        f.flying_min, f.carbon_g, f.typical_carbon_g, f.itinerary_key,
    )


def leg_rows(search_id, f: Flight) -> list[tuple]:
    return [
        (
            search_id, f.rank, seq, leg.from_airport, leg.to_airport, leg.depart_at, leg.arrive_at, leg.duration_min,
            leg.plane_type, leg.airline_code, leg.airline, leg.flight_number,
        )
        for seq, leg in enumerate(f.legs)
    ]


def save(conn, config: Config, run_id, fetched_at, origin, destination, depart, return_date, status,
         flights: list[Flight], error) -> None:
    with conn.transaction():
        search_id = conn.execute(
            """INSERT INTO search
                   (run_id, fetched_at, origin, destination, depart_date, trip, return_date, seat, adults,
                    max_stops, currency, status, error, flight_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                run_id, fetched_at, origin, destination, depart, config.trip, return_date, config.seat,
                config.adults, config.max_stops, config.currency, status, error, len(flights),
            ),
        ).fetchone()[0]
        if flights:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO flight
                           (search_id, rank, from_airport, to_airport, price, stops, depart_at, arrive_at,
                            flying_min, carbon_g, typical_carbon_g, itinerary_key)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [flight_row(search_id, f) for f in flights],
                )
                cur.executemany(
                    """INSERT INTO leg
                           (search_id, rank, seq, from_airport, to_airport, depart_at, arrive_at, duration_min,
                            plane_type, airline_code, airline, flight_number)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [row for f in flights for row in leg_rows(search_id, f)],
                )
