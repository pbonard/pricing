"""Tests of flight_collector/common/, shared by both collectors: run from the repo root, python -m unittest discover -s tests -t .

The save tests need a Postgres and run only when TEST_DATABASE_URL is set, in a schema of their own that they drop
afterwards.
"""

import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from flight_collector.common import config, db, run
from flight_collector.common.model import Flight, Leg, carrier, carriers_match, grams, text, whole, without_carriers

from tests.fakes import DEPART, RETURN

KST = ZoneInfo("Asia/Seoul")


def leg(frm="ICN", to="NRT", number="YP731", airline="Air Premia", depart=datetime(2026, 12, 20, 8, 45),
        arrive=datetime(2026, 12, 20, 11, 20), minutes=155, plane="Boeing 787"):
    return Leg(frm, to, depart, arrive, minutes, plane, number[:2] if number else None, airline if number else None,
               number)


NONSTOP = Flight(0, False, 187400, ["Air Premia"], [leg()], 80000, 90000)
CONNECTION = Flight(1, False, 242400, ["Korean Air"], [
    leg("GMP", "CJU", "KE1041", "Korean Air", datetime(2026, 12, 20, 7), datetime(2026, 12, 20, 8, 10), 70, "Airbus A321"),
    leg("CJU", "NRT", "KE2125", "Korean Air", datetime(2026, 12, 20, 9, 30), datetime(2026, 12, 20, 12), 150),
], 80000, 90000)


class LoadConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config.load_config({})
        self.assertEqual(cfg.routes, [("SEL", "TYO"), ("SEL", "OSA")])
        self.assertEqual((cfg.trip, cfg.stay_nights, cfg.days_ahead, cfg.base_date), ("round-trip", [3], [7, 14, 30, 60], None))
        self.assertEqual((cfg.seat, cfg.adults, cfg.max_stops, cfg.currency, cfg.dry_run), ("economy", 1, None, "KRW", False))

    def test_every_setting_is_read(self):
        cfg = config.load_config({
            "ROUTES": "sel-cju, ICN-BKK", "BASE_DATE": "2026-12-20", "DAYS_AHEAD": "7,1", "TRIP": "round-trip",
            "STAY_NIGHTS": "4,2,4", "SEAT": "Business", "ADULTS": "2", "MAX_STOPS": "0", "CURRENCY": "usd",
            "PROXY": "http://proxy:8080", "START_JITTER_MAX_SECONDS": "5", "DRY_RUN": "true",
        })
        self.assertEqual(cfg.routes, [("SEL", "CJU"), ("ICN", "BKK")])
        self.assertEqual(cfg.base_date, DEPART)
        # kept in the order given, the rotation follows it
        self.assertEqual((cfg.days_ahead, cfg.stay_nights), ([7, 1], [2, 4]))
        self.assertEqual((cfg.seat, cfg.adults, cfg.max_stops, cfg.currency, cfg.proxy),
                         ("business", 2, 0, "USD", "http://proxy:8080"))
        self.assertEqual(cfg.start_jitter_max, 5)
        self.assertTrue(cfg.dry_run)

    def test_one_way_ignores_stay_nights(self):
        self.assertEqual(config.load_config({"TRIP": "one-way", "STAY_NIGHTS": "not checked"}).stay_nights, [])

    def test_every_problem_is_reported_together(self):
        with self.assertRaises(ValueError) as raised:
            config.load_config({
                "ROUTES": "SEL-TYO,icn_nrt,SEL-SEL", "BASE_DATE": "2026-13-01", "DAYS_AHEAD": "-3,400",
                "TRIP": "return", "SEAT": "cheap", "ADULTS": "0", "MAX_STOPS": "x", "CURRENCY": "wons",
                "START_JITTER_MAX_SECONDS": "-1", "DRY_RUN": "yes",
            })
        for expected in (
            "'icn_nrt'", "'SEL-SEL'", "BASE_DATE='2026-13-01'", "'-3'", "'400'", "TRIP='return'", "SEAT='cheap'",
            "ADULTS='0'", "MAX_STOPS='x'", "CURRENCY='WONS'", "START_JITTER_MAX_SECONDS='-1' is below 0", "DRY_RUN='yes'",
        ):
            self.assertIn(expected, str(raised.exception))

    def test_base_date_is_one_date(self):
        with self.assertRaises(ValueError) as raised:
            config.load_config({"BASE_DATE": "2026-12-20,2027-01-02"})
        self.assertIn("is not one YYYY-MM-DD date", str(raised.exception))

    def test_round_trip_needs_stay_nights(self):
        for bad in ("", "0", "31"):
            with self.subTest(STAY_NIGHTS=bad), self.assertRaises(ValueError):
                config.load_config({"STAY_NIGHTS": bad})

    def test_a_route_can_have_its_own_stay(self):
        cfg = config.load_config({"ROUTES": "SEL-TYO:3, sel-ber:7, SEL-OSA", "STAY_NIGHTS": "2"})
        self.assertEqual(cfg.routes, [("SEL", "TYO"), ("SEL", "BER"), ("SEL", "OSA")])
        self.assertEqual(cfg.route_stay_nights, {("SEL", "TYO"): 3, ("SEL", "BER"): 7})
        self.assertEqual(cfg.stay_nights, [2])

    def test_stay_nights_can_be_empty_when_every_route_has_its_own(self):
        self.assertEqual(config.load_config({"ROUTES": "SEL-TYO:3,SEL-BER:7", "STAY_NIGHTS": ""}).stay_nights, [])
        with self.assertRaises(ValueError) as raised:
            config.load_config({"ROUTES": "SEL-TYO,SEL-BER:7", "STAY_NIGHTS": ""})
        self.assertIn("SEL-TYO has no stay of its own", str(raised.exception))

    def test_a_bad_route_stay_or_a_route_twice(self):
        with self.assertRaises(ValueError) as raised:
            config.load_config({"ROUTES": "SEL-BER:0,SEL-TYO:x,SEL-OSA:2|3,SEL-CJU:3,sel-cju:5"})
        message = str(raised.exception)
        for expected in ("'SEL-BER:0', its stay '0'", "'SEL-TYO:x', its stay 'x'", "'SEL-OSA:2|3', its stay '2|3'",
                         "ROUTES has SEL-CJU twice"):
            self.assertIn(expected, message)

    def test_routes_cant_be_empty(self):
        with self.assertRaises(ValueError):
            config.load_config({"ROUTES": " , "})


class BaseDateTest(unittest.TestCase):
    today = date(2026, 11, 19)

    def dates(self, base_date, days_ahead):
        return config.departure_dates(config.load_config({"BASE_DATE": base_date, "DAYS_AHEAD": days_ahead}), self.today)

    def test_nothing_set_is_today(self):
        self.assertEqual(self.dates("", ""), [self.today])

    def test_days_ahead_count_from_today_without_a_base_date(self):
        self.assertEqual(self.dates("", "1,7"), [date(2026, 11, 20), date(2026, 11, 26)])
        self.assertEqual(self.dates("", "0,1,7"), [self.today, date(2026, 11, 20), date(2026, 11, 26)])

    def test_days_ahead_count_from_the_base_date(self):
        self.assertEqual(self.dates("2026-12-20", ""), [DEPART])
        self.assertEqual(self.dates("2026-12-20", "1"), [date(2026, 12, 21)])
        self.assertEqual(self.dates("2026-12-20", "0,2,0"), [DEPART, date(2026, 12, 22)])

    def test_a_base_date_of_today_still_counts(self):
        self.assertEqual(self.dates("2026-11-19", "0,1"), [self.today, date(2026, 11, 20)])

    def test_a_passed_base_date_is_stale_and_has_nothing(self):
        cfg = config.load_config({"BASE_DATE": "2026-11-18", "DAYS_AHEAD": "7,14"})
        self.assertTrue(config.stale(cfg, self.today))
        self.assertEqual(config.departure_dates(cfg, self.today), [])
        self.assertFalse(config.stale(config.load_config({}), self.today))


class RotationTest(unittest.TestCase):
    TODAY = date(2026, 11, 21)

    def cfg(self, **env):
        return config.load_config({"BASE_DATE": "", "STAY_NIGHTS": "3", **env})

    def rotate(self, cfg, n, start=None, days=None):
        """n runs one after the other, each taking the search after the one before. days gives each run's day."""
        out, last, last_day = [], start, None
        for i in range(n):
            today = days[i] if days else self.TODAY
            search = config.next_search(cfg, today, last, last_day)
            out.append(search)
            last, last_day = search, today
        return out

    def test_the_plan_is_days_ahead_in_order_then_routes_then_stays(self):
        plan = config.plan(self.cfg(ROUTES="SEL-TYO,SEL-OSA", DAYS_AHEAD="7,1", STAY_NIGHTS="3,4"), self.TODAY)
        self.assertEqual([(d, (dep - self.TODAY).days, (ret - dep).days) for _, d, dep, ret in plan], [
            ("TYO", 7, 3), ("TYO", 7, 4), ("OSA", 7, 3), ("OSA", 7, 4),
            ("TYO", 1, 3), ("TYO", 1, 4), ("OSA", 1, 3), ("OSA", 1, 4),
        ])

    def test_each_route_its_own_stay_the_others_stay_nights(self):
        plan = config.plan(self.cfg(ROUTES="SEL-TYO:3,SEL-BER:7,SEL-OSA", DAYS_AHEAD="1", STAY_NIGHTS="2,4"), self.TODAY)
        self.assertEqual([(d, (ret - dep).days) for _, d, dep, ret in plan],
                         [("TYO", 3), ("BER", 7), ("OSA", 2), ("OSA", 4)])

    def test_one_way_has_no_return_whatever_the_route_stay(self):
        plan = config.plan(self.cfg(ROUTES="SEL-BER:7", DAYS_AHEAD="1", TRIP="one-way"), self.TODAY)
        self.assertEqual(plan, [("SEL", "BER", date(2026, 11, 22), None)])

    def test_the_rotation_goes_round_routes_with_their_own_stays(self):
        runs = self.rotate(self.cfg(ROUTES="SEL-TYO:3,SEL-BER:7", DAYS_AHEAD="1,7"), 5)
        self.assertEqual([(d, (dep - self.TODAY).days, (ret - dep).days) for _, d, dep, ret in runs],
                         [("TYO", 1, 3), ("BER", 1, 7), ("TYO", 7, 3), ("BER", 7, 7), ("TYO", 1, 3)])

    def test_nothing_saved_yet_starts_at_the_first_search(self):
        self.assertEqual(config.next_search(self.cfg(ROUTES="SEL-TYO", DAYS_AHEAD="1,7"), self.TODAY, None),
                         ("SEL", "TYO", date(2026, 11, 22), date(2026, 11, 25)))

    def test_each_run_takes_the_next_one_then_over_again(self):
        runs = self.rotate(self.cfg(ROUTES="SEL-TYO", DAYS_AHEAD="1,7,14,30,60"), 7)
        self.assertEqual([(dep - self.TODAY).days for _, _, dep, _ in runs], [1, 7, 14, 30, 60, 1, 7])

    def test_routes_take_turns_for_each_date(self):
        runs = self.rotate(self.cfg(ROUTES="SEL-TYO,SEL-OSA", DAYS_AHEAD="1,7"), 5)
        self.assertEqual([(d, (dep - self.TODAY).days) for _, d, dep, _ in runs],
                         [("TYO", 1), ("OSA", 1), ("TYO", 7), ("OSA", 7), ("TYO", 1)])

    def test_the_rotation_goes_on_across_days(self):
        # +7 yesterday was a different date, the rotation still knows it came after +1
        days = [date(2026, 11, 21), date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 22)]
        runs = self.rotate(self.cfg(ROUTES="SEL-TYO", DAYS_AHEAD="1,7,14"), 4, days=days)
        self.assertEqual([(dep - day).days for (_, _, dep, _), day in zip(runs, days)], [1, 7, 14, 1])

    def test_a_failed_search_is_done_again_the_next_day_for_that_day(self):
        cfg = self.cfg(ROUTES="SEL-TYO", DAYS_AHEAD="1,7")
        failed = ("SEL", "TYO", date(2026, 11, 22), date(2026, 11, 25))
        # +1 failed on the 21st: the same day it's 11-22 again, the next day it's the new day's +1, 11-23
        self.assertEqual(config.next_search(cfg, self.TODAY, failed, self.TODAY, last_failed=True), failed)
        self.assertEqual(config.next_search(cfg, date(2026, 11, 22), failed, self.TODAY, last_failed=True),
                         ("SEL", "TYO", date(2026, 11, 23), date(2026, 11, 26)))
        self.assertEqual(config.next_search(cfg, self.TODAY, failed, self.TODAY, last_failed=False)[2],
                         date(2026, 11, 28))

    def test_a_last_search_not_in_the_plan_starts_over(self):
        cfg = self.cfg(ROUTES="SEL-TYO", DAYS_AHEAD="1,7")
        osaka = ("SEL", "OSA", date(2026, 11, 22), date(2026, 11, 25))
        self.assertEqual(config.next_search(cfg, self.TODAY, osaka, self.TODAY), config.plan(cfg, self.TODAY)[0])

    def test_a_fixed_base_date_rotates_from_it(self):
        cfg = self.cfg(BASE_DATE="2026-12-20", ROUTES="SEL-TYO", DAYS_AHEAD="0,2")
        runs = self.rotate(cfg, 3)
        self.assertEqual([dep for _, _, dep, _ in runs], [DEPART, date(2026, 12, 22), DEPART])

    def test_nothing_left_is_nothing_to_search(self):
        cfg = self.cfg(BASE_DATE="2026-11-01", DAYS_AHEAD="")
        self.assertIsNone(config.next_search(cfg, self.TODAY, None))


class ModelTest(unittest.TestCase):
    def test_itinerary_key_is_the_flight_numbers(self):
        self.assertEqual((NONSTOP.itinerary_key, CONNECTION.itinerary_key), ("YP731", "KE1041+KE2125"))
        self.assertEqual((CONNECTION.stops, CONNECTION.flying_min), (1, 220))

    def test_itinerary_key_without_numbers_is_a_stable_hash(self):
        flight = Flight(0, False, 1, ["Air Premia"], without_carriers(NONSTOP.legs), None, None)
        self.assertRegex(flight.itinerary_key, r"^[0-9a-f]{16}$")
        self.assertEqual(flight.itinerary_key, Flight(5, True, 9, ["Air Premia"], without_carriers(NONSTOP.legs), None,
                                                      None).itinerary_key)

    def test_carriers_match_the_result_airlines(self):
        self.assertTrue(carriers_match(CONNECTION.legs, ["Korean Air"]))
        self.assertFalse(carriers_match(CONNECTION.legs, ["Korean Air", "JAL"]))
        self.assertFalse(carriers_match(without_carriers(NONSTOP.legs), ["Air Premia"]))

    def test_carrier_format(self):
        self.assertEqual(carrier(["7C", "1101", None, "Jeju Air"]), ("7C", "1101", "Jeju Air"))
        for bad in (["YP", "73X1", None, "x"], ["yp", "731", None, "x"], ["YP", "731", None, " "], ["YP"], None, "YP731"):
            with self.subTest(raw=bad):
                self.assertIsNone(carrier(bad))

    def test_values(self):
        self.assertEqual((whole(3), whole(True), whole(3.0)), (True, False, False))
        self.assertEqual((text(" "), text("A321"), text(None)), (None, "A321", None))
        self.assertEqual((grams(-1), grams("x"), grams(0), grams(80000)), (None, None, 0, 80000))


class FakeSearch:
    """The collector's search for run.main(): each call answers with the next of results."""

    def __init__(self, results):
        self.results, self.calls = list(results), []

    @contextmanager
    def __call__(self, cfg):
        def search(origin, destination, depart, return_date):
            self.calls.append((origin, destination, depart, return_date))
            return self.results.pop(0)

        yield search


OK = ("ok", [NONSTOP], None)


class RunTest(unittest.TestCase):
    ENV = {
        "ROUTES": "SEL-TYO,SEL-OSA", "BASE_DATE": "", "DAYS_AHEAD": "1,7", "STAY_NIGHTS": "3",
        "START_JITTER_MAX_SECONDS": "0", "DRY_RUN": "false",
    }
    NOW = datetime(2026, 11, 19, 9, 0, tzinfo=KST)

    def run_main(self, results, last=None, **env):
        """run.main() with the collector's search answering results, last as the search saved before."""
        fake = FakeSearch(results)
        with mock.patch.dict(os.environ, {**self.ENV, **env}, clear=True), \
                mock.patch.object(run.time, "sleep"), \
                mock.patch.object(run, "datetime", wraps=datetime) as fake_datetime, \
                mock.patch.object(run.db, "connect") as connect, \
                mock.patch.object(run.db, "ensure_schema"), \
                mock.patch.object(run.db, "last_search", return_value=last), \
                mock.patch.object(run.db, "save") as save, \
                self.assertLogs("collector", "INFO"):
            fake_datetime.now.side_effect = lambda tz=None: self.NOW.astimezone(tz) if tz else self.NOW
            code = run.main(fake)
        return code, fake.calls, connect, save

    def test_one_search_the_one_after_the_last_saved_and_it_is_saved(self):
        last = (("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23)), date(2026, 11, 19), "ok")
        code, calls, _, save = self.run_main([OK], last)
        self.assertEqual((code, calls), (0, [("SEL", "OSA", date(2026, 11, 20), date(2026, 11, 23))]))
        save.assert_called_once()
        self.assertEqual(save.call_args.args[4:10], ("SEL", "OSA", date(2026, 11, 20), date(2026, 11, 23), "ok", [NONSTOP]))

    def test_a_blocked_or_failed_search_is_done_again(self):
        for status in ("blocked", "error"):
            with self.subTest(status=status):
                last = (("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23)), date(2026, 11, 19), status)
                _, calls, _, _ = self.run_main([OK], last)
                self.assertEqual(calls, [("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23))])

    def test_no_flights_found_moves_on(self):
        last = (("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23)), date(2026, 11, 19), "not_found")
        _, calls, _, _ = self.run_main([OK], last)
        self.assertEqual(calls, [("SEL", "OSA", date(2026, 11, 20), date(2026, 11, 23))])

    def test_nothing_saved_yet_is_the_first_search(self):
        _, calls, _, _ = self.run_main([OK])
        self.assertEqual(calls, [("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23))])

    def test_a_dry_run_does_the_first_search_and_touches_no_database(self):
        code, calls, connect, save = self.run_main([OK], DRY_RUN="true")
        self.assertEqual((code, calls), (0, [("SEL", "TYO", date(2026, 11, 20), date(2026, 11, 23))]))
        connect.assert_not_called()
        save.assert_not_called()

    def test_a_blocked_or_failed_search_is_saved_and_fails_the_job(self):
        for result in (("blocked", [], None), ("error", [], "x")):
            with self.subTest(status=result[0]):
                code, calls, _, save = self.run_main([result])
                self.assertEqual((code, len(calls)), (1, 1))
                save.assert_called_once()

    def test_no_flights_found_is_not_a_failure(self):
        code, _, _, _ = self.run_main([("not_found", [], None)])
        self.assertEqual(code, 0)

    def test_a_bad_config_exits_2_before_searching(self):
        fake = FakeSearch([])
        with mock.patch.dict(os.environ, {"SEAT": "cheap"}, clear=True), self.assertLogs("collector", "ERROR"):
            self.assertEqual(run.main(fake), 2)
        self.assertEqual(fake.calls, [])

    def test_a_passed_base_date_ends_the_run_without_searching(self):
        fake = FakeSearch([])
        env = {**self.ENV, "BASE_DATE": "2026-11-01"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(run.db, "connect") as connect, \
                mock.patch.object(run, "datetime", wraps=datetime) as fake_datetime, \
                self.assertLogs("collector", "WARNING") as logs:
            fake_datetime.now.side_effect = lambda tz=None: self.NOW.astimezone(tz) if tz else self.NOW
            code = run.main(fake)
        self.assertEqual((code, fake.calls), (0, []))
        # not even the database is touched
        connect.assert_not_called()
        self.assertIn("BASE_DATE 2026-11-01 has passed", "\n".join(logs.output))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class SaveTest(unittest.TestCase):
    def setUp(self):
        self.schema = f"test_{uuid.uuid4().hex[:8]}"
        self.conn = db.psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.conn.execute(f"SET search_path TO {self.schema}")
        db.ensure_schema(self.conn)
        # applying it again changes nothing, it runs at the start of every run
        db.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.close()

    def test_a_round_trip_search_its_flights_and_legs(self):
        run_id = uuid.uuid4()
        db.save(self.conn, config.load_config({}), run_id, datetime.now().astimezone(), "SEL", "TYO", DEPART, RETURN,
                "ok", [NONSTOP, CONNECTION], None)
        self.assertEqual(
            self.conn.execute("SELECT run_id, trip, return_date, seat, adults, max_stops, currency, status, "
                              "flight_count FROM search").fetchall(),
            [(run_id, "round-trip", RETURN, "economy", 1, None, "KRW", "ok", 2)],
        )
        self.assertEqual(
            self.conn.execute("SELECT rank, itinerary_key, price, stops, flying_min, carbon_g FROM flight "
                              "ORDER BY rank").fetchall(),
            [(0, "YP731", 187400, 0, 155, 80000), (1, "KE1041+KE2125", 242400, 1, 220, 80000)],
        )
        self.assertEqual(
            self.conn.execute("SELECT rank, seq, flight_number, plane_type FROM leg ORDER BY rank, seq").fetchall(),
            [(0, 0, "YP731", "Boeing 787"), (1, 0, "KE1041", "Airbus A321"), (1, 1, "KE2125", "Boeing 787")],
        )

    def test_the_last_search_is_the_one_saved_last(self):
        self.assertIsNone(db.last_search(self.conn, [("SEL", "TYO"), ("SEL", "OSA")]))
        cfg = config.load_config({})
        for when, destination in ((datetime(2026, 11, 19, 1, tzinfo=KST), "TYO"),
                                  (datetime(2026, 11, 20, 0, 30, tzinfo=KST), "OSA")):
            db.save(self.conn, cfg, uuid.uuid4(), when, "SEL", destination, DEPART, RETURN, "ok", [], None)
        # the day is KST: 00:30 KST on the 20th is still the 19th in UTC
        self.assertEqual(db.last_search(self.conn, [("SEL", "TYO"), ("SEL", "OSA")]),
                         (("SEL", "OSA", DEPART, RETURN), date(2026, 11, 20), "ok"))

    def test_the_last_search_is_of_the_routes_asked_for(self):
        # another collector's routes saved later don't count: each collector has its own rotation
        cfg = config.load_config({})
        for when, origin, destination in ((datetime(2026, 11, 20, 8, tzinfo=KST), "SEL", "TYO"),
                                          (datetime(2026, 11, 20, 10, tzinfo=KST), "BER", "SEL")):
            db.save(self.conn, cfg, uuid.uuid4(), when, origin, destination, DEPART, RETURN, "ok", [], None)
        self.assertEqual(db.last_search(self.conn, [("SEL", "TYO")])[0][:2], ("SEL", "TYO"))
        self.assertEqual(db.last_search(self.conn, [("BER", "SEL")])[0][:2], ("BER", "SEL"))
        self.assertIsNone(db.last_search(self.conn, [("SEL", "OSA")]))

    def test_a_failed_search_is_kept_without_flights(self):
        db.save(self.conn, config.load_config({"TRIP": "one-way"}), uuid.uuid4(), datetime.now().astimezone(), "SEL",
                "TYO", DEPART, None, "blocked", [], None)
        self.assertEqual(self.conn.execute("SELECT trip, status, flight_count FROM search").fetchall(),
                         [("one-way", "blocked", 0)])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM flight").fetchone(), (0,))


if __name__ == "__main__":
    unittest.main()
