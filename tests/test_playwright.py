"""Tests of the playwright collector (flight_collector/playwright/), run from the repo root:
python -m unittest discover -s tests -t .

The search answers are faked (tests/fakes.py) and the browser is replaced, so nothing here goes out to the network or
starts Chromium. Only the playwright package has to be installed, not its browsers.
"""

import os
import unittest
from datetime import date, datetime
from unittest import mock

from flight_collector.playwright import browser, parse, query
from flight_collector.playwright import main as pw
from flight_collector.common import config as conf

from tests.fakes import (CONNECTION, DEPART, ERROR_ANSWER, NONSTOP, RETURN, TOP, answer, payload, raw_leg,
                                raw_result)


class QueryTest(unittest.TestCase):
    # what fast-flights 3.1.0 encodes for the same searches
    FAST_FLIGHTS = [
        ("round-trip", "economy", 1, None, "SEL", "TYO", "2026-12-20", "2026-12-23",
         "GhoSCjIwMjYtMTItMjBqBRIDU0VMcgUSA1RZTxoaEgoyMDI2LTEyLTIzagUSA1RZT3IFEgNTRUxCAQFIAYgBAZgBAQ=="),
        ("one-way", "business", 2, 0, "ICN", "BER", "2026-12-19", None,
         "GhwSCjIwMjYtMTItMTkoAGoFEgNJQ05yBRIDQkVSQgIBAUgDiAEBmAEC"),
        ("round-trip", "premium-economy", 9, 1, "GMP", "CJU", "2027-01-05", "2027-01-07",
         "GhwSCjIwMjctMDEtMDUoAWoFEgNHTVByBRIDQ0pVGhwSCjIwMjctMDEtMDcoAWoFEgNDSlVyBRIDR01QQgkBAQEBAQEBAQFIAogBAZgBAQ=="),
    ]

    def test_the_same_bytes_as_fast_flights(self):
        for trip, seat, adults, stops, origin, destination, out, back, expected in self.FAST_FLIGHTS:
            legs = [query.Leg(date.fromisoformat(out), origin, destination)]
            if back:
                legs.append(query.Leg(date.fromisoformat(back), destination, origin))
            with self.subTest(trip=trip, seat=seat, adults=adults, stops=stops):
                self.assertEqual(query.tfs(legs, trip, seat, adults, stops), expected)

    def test_the_url_escapes_the_search(self):
        url = query.search_url(pw.legs("SEL", "TYO", DEPART, RETURN), "round-trip", "economy", 1, None, "KRW", "en-US")
        self.assertTrue(url.startswith("https://www.google.com/travel/flights/search?tfs="))
        self.assertTrue(url.endswith("%3D%3D&hl=en-US&curr=KRW"))

    def test_a_one_way_search_has_no_return_leg(self):
        self.assertEqual(pw.legs("SEL", "TYO", DEPART, None), [query.Leg(DEPART, "SEL", "TYO")])


class PageTimeoutTest(unittest.TestCase):
    def test_page_timeout(self):
        self.assertEqual((pw.page_timeout({}), pw.page_timeout({"PAGE_TIMEOUT_SECONDS": "90"})), (60, 90))
        for bad in ("x", "5", "600"):
            with self.subTest(PAGE_TIMEOUT_SECONDS=bad), self.assertRaises(ValueError):
                pw.page_timeout({"PAGE_TIMEOUT_SECONDS": bad})

    def test_a_bad_page_timeout_exits_2_before_any_browser(self):
        with mock.patch.dict(os.environ, {"PAGE_TIMEOUT_SECONDS": "x"}, clear=True), \
                mock.patch.object(pw, "Browser") as browser_class, self.assertLogs("collector", "ERROR"):
            self.assertEqual(pw.main(), 2)
        browser_class.assert_not_called()


class PayloadsTest(unittest.TestCase):
    def test_reads_every_results_payload(self):
        self.assertEqual(parse.payloads(answer(payload(others=[NONSTOP]), payload(others=[CONNECTION]))),
                         [payload(others=[NONSTOP]), payload(others=[CONNECTION])])

    def test_an_error_answer_has_none(self):
        self.assertEqual((parse.payloads(ERROR_ANSWER), parse.payloads("not an answer")), ([], []))

    def test_top_flights_first_then_the_others(self):
        results = parse.raw_results(payload(top=[TOP], others=[NONSTOP, CONNECTION]))
        self.assertEqual([top for top, _ in results], [True, False, False])
        self.assertEqual(results[0][1], TOP)

    def test_missing_lists_are_empty(self):
        self.assertEqual(parse.raw_results([None] * 8), [])
        self.assertEqual(parse.raw_results([None, None, [None], [[NONSTOP]]]), [(False, NONSTOP)])


class FlightsTest(unittest.TestCase):
    def test_flights_with_their_legs_and_page_rank(self):
        flights, skipped = parse.flights(payload(top=[TOP], others=[NONSTOP, CONNECTION]), DEPART)
        self.assertEqual(skipped, [])
        self.assertEqual([(f.rank, f.top, f.price, f.itinerary_key) for f in flights], [
            (0, True, 433489, "ZG46"), (1, False, 187400, "YP731"), (2, False, 242400, "KE1041+KE2125"),
        ])
        connection = flights[2]
        self.assertEqual((connection.stops, connection.flying_min, connection.carbon_g), (1, 220, 80000))
        self.assertEqual((connection.legs[1].depart_at, connection.legs[1].arrive_at, connection.legs[1].plane_type),
                         (datetime(2026, 12, 20, 9, 30), datetime(2026, 12, 20, 12, 0), "Boeing 787"))

    def test_time_shorthand(self):
        leg = raw_leg("ICN", "NRT", ["YP", "731", None, "Air Premia"], depart=[None, 31], arrive=[8])
        (f,), _ = parse.flights(payload(others=[raw_result(["Air Premia"], [leg], 1)]), DEPART)
        self.assertEqual((f.legs[0].depart_at.time().isoformat(), f.legs[0].arrive_at.time().isoformat()),
                         ("00:31:00", "08:00:00"))

    def test_results_without_a_price_are_kept(self):
        cases = [raw_result(["Air Premia"], [raw_leg("ICN", "NRT", ["YP", "731", None, "Air Premia"])], 1)
                 for _ in range(4)]
        del cases[0][1]
        cases[1][1] = []
        cases[2][1] = None
        cases[3][1] = [[None]]
        flights, skipped = parse.flights(payload(others=cases), DEPART)
        self.assertEqual(([f.price for f in flights], skipped), ([None] * 4, []))

    def test_a_result_that_doesnt_check_out_is_skipped_with_the_reason(self):
        cases = {
            "price -5": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None)], -5),
            "airport 'icn'": raw_result(["Air Premia"], [raw_leg("icn", "NRT", None)], 1),
            "duration 0": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, duration=0)], 1),
            "time": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, depart=(25, 0))], 1),
            "departs 2026-12-21": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, day=(2026, 12, 21))], 1),
            "no legs": raw_result(["Air Premia"], [], 1),
        }
        for reason, result in cases.items():
            with self.subTest(reason=reason):
                flights, skipped = parse.flights(payload(others=[NONSTOP, result]), DEPART)
                self.assertEqual([f.rank for f in flights], [0])
                self.assertEqual(len(skipped), 1)
                self.assertTrue(skipped[0].startswith(f"#1 {reason}"), skipped[0])

    def test_a_malformed_or_mismatched_carrier_is_left_out(self):
        for carrier, airlines in ((["YP", "73X1", None, "Air Premia"], ["Air Premia"]), (None, ["Air Premia"]),
                                  (["ZG", "46", None, "ZIPAIR Tokyo"], ["Air Premia"])):
            with self.subTest(carrier=carrier):
                (f,), _ = parse.flights(payload(others=[raw_result(airlines, [raw_leg("ICN", "NRT", carrier)], 1)]), DEPART)
                self.assertEqual((f.legs[0].airline_code, f.legs[0].airline, f.legs[0].flight_number), (None, None, None))
                self.assertRegex(f.itinerary_key, r"^[0-9a-f]{16}$")

    def test_unusable_optional_values_are_null(self):
        result = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", ["YP", "731", None, "Air Premia"], plane=" ")], 1,
                            carbon=(-1, "x"))
        (f,), _ = parse.flights(payload(others=[result]), DEPART)
        self.assertEqual((f.carbon_g, f.typical_carbon_g, f.legs[0].plane_type), (None, None, None))


class FakePage:
    def __init__(self, content="<html></html>", url="https://www.google.com/travel/flights/search?tfs=x"):
        self._content, self.url = content, url

    def content(self):
        return self._content

    def title(self):
        return "Seoul to Tokyo | Google Flights"


class BrowserReadTest(unittest.TestCase):
    def test_the_answer_with_the_most_results_is_the_page(self):
        full = payload(top=[TOP], others=[NONSTOP, CONNECTION])
        page = browser.Browser._read(FakePage(), [answer(payload(others=[NONSTOP])), answer(full)])
        self.assertEqual((page.status, page.payload), ("ok", full))

    def test_an_error_answer_is_an_error(self):
        self.assertEqual(browser.Browser._read(FakePage("<html>Oops, something went wrong.</html>"), [ERROR_ANSWER]).status,
                         "error")

    def test_a_block_page(self):
        for page in (FakePage(url="https://www.google.com/sorry/index?continue=x"),
                     FakePage("<html>Our systems have detected unusual traffic</html>")):
            with self.subTest(url=page.url):
                self.assertEqual(browser.Browser._read(page, []).status, "blocked")

    def test_no_answer_is_an_error_naming_the_page(self):
        page = browser.Browser._read(FakePage(), [])
        self.assertEqual(page.status, "error")
        self.assertIn("Seoul to Tokyo", page.error)

    def test_an_answer_without_results_or_error_is_not_found(self):
        self.assertEqual(browser.Browser._read(FakePage(), [answer(payload())]).status, "not_found")


class FakeBrowser:
    """Browser for open_search: each load gives the next of pages."""

    def __init__(self, pages):
        self.pages, self.urls = list(pages), []

    def __call__(self, language, timeout):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def load(self, url):
        self.urls.append(url)
        return self.pages.pop(0)


class OpenSearchTest(unittest.TestCase):
    def search(self, pages, **env):
        fake = FakeBrowser(pages)
        cfg = conf.load_config({"TRIP": "round-trip", **env})
        with mock.patch.object(pw, "Browser", fake), pw.open_search(60)(cfg) as search:
            results = [search("SEL", "TYO", DEPART, RETURN) for _ in pages]
        return results, fake.urls

    def test_a_page_becomes_flights(self):
        ((status, flights, error),), (url,) = self.search([browser.Page("ok", payload(top=[TOP], others=[NONSTOP]), None)])
        self.assertEqual((status, [f.itinerary_key for f in flights], error), ("ok", ["ZG46", "YP731"], None))
        self.assertEqual(url, query.search_url(pw.legs("SEL", "TYO", DEPART, RETURN), "round-trip", "economy", 1, None,
                                               "KRW", "en-US"))

    def test_skipped_results_are_logged_and_noted(self):
        bad = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, day=(2026, 12, 21))], 1)
        with self.assertLogs("collector", "WARNING"):
            ((status, flights, error),), _ = self.search([browser.Page("ok", payload(others=[bad, NONSTOP]), None)])
        self.assertEqual((status, [f.rank for f in flights]), ("ok", [1]))
        self.assertRegex(error, r"skipped 1 invalid results: #0 departs")

    def test_a_failed_page_has_no_flights(self):
        for page in (browser.Page("error", None, "the search answered with an error"), browser.Page("blocked", None, None)):
            with self.subTest(status=page.status):
                ((status, flights, error),), _ = self.search([page])
                self.assertEqual((status, flights, error), (page.status, [], page.error))


if __name__ == "__main__":
    unittest.main()
