"""Tests of the fast-flights collector (flight_collector/fast_flight/), run from the repo root: python -m unittest discover -s tests -t .

The search page is faked (tests/fakes.py), so nothing here goes out to the network.
"""

import unittest
from datetime import datetime
from unittest import mock

from flight_collector.fast_flight import search as g
from flight_collector.common import config as conf

from tests.fakes import (CONNECTION, DEPART, NONSTOP, NOT_FOUND_PAGE, RETURN, page, raw_leg,
                                raw_result)


def config(**env) -> conf.Config:
    return conf.load_config({"START_JITTER_MAX_SECONDS": "0", **env})


def run_search(html, cfg=None, depart=DEPART, return_date=None):
    with mock.patch.object(g, "fetch_flights_html", return_value=html) as fetch:
        result = g.search(cfg or config(TRIP="one-way"), "SEL", "TYO", depart, return_date)
    return result, fetch.call_args.args[0]


class LegCarriersTest(unittest.TestCase):
    def test_reads_each_leg_in_result_order(self):
        self.assertEqual(
            g.leg_carriers(page([NONSTOP, CONNECTION])),
            [[("YP", "731", "Air Premia")], [("KE", "1041", "Korean Air"), ("KE", "2125", "Korean Air")]],
        )

    def test_a_malformed_carrier_is_none(self):
        for carrier in (["YP", "73X1", None, "Air Premia"], ["yp", "731", None, "Air Premia"],
                        ["YP", "731", None, " "], ["YP", None, None, "Air Premia"], ["YP"], None, "YP731"):
            with self.subTest(carrier=carrier):
                result = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", carrier)], 1)
                self.assertEqual(g.leg_carriers(page([result])), [[None]])

    def test_a_page_not_as_expected_is_empty(self):
        for html in ("<html>nothing</html>", '<script class="ds:1">no data here</script>', page([])):
            with self.subTest(html=html[:40]):
                self.assertEqual(g.leg_carriers(html), [])


class ResultProblemTest(unittest.TestCase):
    def test_a_good_result_has_no_problem(self):
        self.assertIsNone(g.result_problem(g.parse_results(page([NONSTOP]))[0], DEPART))

    def test_problems(self):
        cases = {
            "price": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None)], -5),
            "airport": raw_result(["Air Premia"], [raw_leg("icn", "NRT", None)], 1),
            "duration": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, duration=0)], 1),
            "time": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, depart=(25, 0))], 1),
            "departs": raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, day=(2026, 12, 21))], 1),
        }
        for word, result in cases.items():
            with self.subTest(problem=word):
                self.assertIn(word, g.result_problem(g.parse_results(page([result]))[0], DEPART))

    def test_a_missing_price_is_allowed(self):
        result = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None)], 1)
        result[1] = []
        self.assertIsNone(g.result_problem(g.parse_results(page([result]))[0], DEPART))


class ParseResultsTest(unittest.TestCase):
    def test_results_without_a_price_dont_lose_the_page(self):
        # every shape a price could be left out in: no price block, an empty one, a null one, no price pair
        cases = [raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None)], 1) for _ in range(4)]
        del cases[0][1]
        cases[1][1] = []
        cases[2][1] = None
        cases[3][1] = [[None]]
        results = g.parse_results(page([NONSTOP, *cases, CONNECTION]))
        self.assertEqual([f.price for f in results], [187400, None, None, None, None, 242400])

    def test_the_same_results_as_fast_flights_on_a_normal_page(self):
        html = page([NONSTOP, CONNECTION])
        self.assertEqual(g.parse_results(html), g.parse_js(html.split('class="ds:1" nonce="x">', 1)[1]))

    def test_no_flights_and_broken_pages(self):
        with self.assertRaises(g.FlightsNotFound):
            g.parse_results(NOT_FOUND_PAGE)
        for html in ("<html>nothing</html>", '<script class="ds:1">no data here</script>'):
            with self.subTest(html=html), self.assertRaises(Exception):
                g.parse_results(html)


class SearchTest(unittest.TestCase):
    def test_flights_with_their_legs_and_page_rank(self):
        (status, flights, error), _ = run_search(page([NONSTOP, CONNECTION]))
        self.assertEqual((status, error), ("ok", None))
        self.assertEqual([(f.rank, f.top, f.price, f.itinerary_key) for f in flights],
                         [(0, False, 187400, "YP731"), (1, False, 242400, "KE1041+KE2125")])
        connection = flights[1]
        self.assertEqual((connection.stops, connection.flying_min, connection.carbon_g, connection.typical_carbon_g),
                         (1, 220, 80000, 90000))
        self.assertEqual(connection.legs[0].depart_at, datetime(2026, 12, 20, 7, 0))
        self.assertEqual(connection.legs[1].arrive_at, datetime(2026, 12, 20, 12, 0))
        self.assertEqual((connection.legs[0].plane_type, connection.legs[0].airline_code, connection.legs[0].airline),
                         ("Airbus A321", "KE", "Korean Air"))

    def test_a_result_without_a_price_is_saved_with_the_others(self):
        priceless = raw_result(["ZIPAIR Tokyo"], [raw_leg("ICN", "NRT", ["ZG", "46", None, "ZIPAIR Tokyo"])], 1)
        priceless[1] = []
        (status, flights, error), _ = run_search(page([priceless, NONSTOP]))
        self.assertEqual((status, error), ("ok", None))
        self.assertEqual([(f.rank, f.price) for f in flights], [(0, None), (1, 187400)])

    def test_an_invalid_result_is_skipped_and_the_rest_keep_their_rank(self):
        bad = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", None, day=(2026, 12, 21))], 1)
        with self.assertLogs(g.log, "WARNING"):
            (status, flights, error), _ = run_search(page([bad, NONSTOP]))
        self.assertEqual((status, [f.rank for f in flights]), ("ok", [1]))
        self.assertRegex(error, r"skipped 1 invalid results: #0 departs 2026-12-21")

    def test_carriers_not_matching_the_airlines_are_left_out(self):
        other = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", ["ZG", "46", None, "ZIPAIR Tokyo"])], 200000)
        with self.assertLogs(g.log, "WARNING"):
            (_, flights, _), _ = run_search(page([other]))
        self.assertIsNone(flights[0].legs[0].flight_number)
        self.assertRegex(flights[0].itinerary_key, r"^[0-9a-f]{16}$")

    def test_carriers_that_dont_line_up_with_the_results_are_left_out(self):
        with mock.patch.object(g, "leg_carriers", return_value=[]), self.assertLogs(g.log, "WARNING"):
            (status, flights, _), _ = run_search(page([NONSTOP]))
        self.assertEqual((status, flights[0].legs[0].flight_number), ("ok", None))

    def test_unusable_optional_values_are_null(self):
        result = raw_result(["Air Premia"], [raw_leg("ICN", "NRT", ["YP", "731", None, "Air Premia"], plane=" ")], 1,
                            carbon=(-1, "x"))
        (_, (flight,), _), _ = run_search(page([result]))
        self.assertEqual((flight.carbon_g, flight.typical_carbon_g, flight.legs[0].plane_type), (None, None, None))

    def test_statuses(self):
        cases = {
            "blocked": "<html>Our systems have detected unusual traffic from your computer</html>",
            "error": "<html>something else entirely</html>",
            "not_found": NOT_FOUND_PAGE,
        }
        for status, html in cases.items():
            with self.subTest(status=status):
                (got, flights, _), _ = run_search(html)
                self.assertEqual((got, flights), (status, []))

    def test_a_normal_page_mentioning_captcha_is_not_blocked(self):
        html = page([NONSTOP]).replace("</html>", '<a href="/recaptcha/challenge">x</a></html>')
        (status, _, _), _ = run_search(html)
        self.assertEqual(status, "ok")

    def test_a_fetch_failure_is_an_error(self):
        with mock.patch.object(g, "fetch_flights_html", side_effect=TimeoutError("slow")):
            status, flights, error = g.search(config(), "SEL", "TYO", DEPART, None)
        self.assertEqual((status, flights), ("error", []))
        self.assertIn("TimeoutError", error)

    def test_a_round_trip_query_has_the_return_and_one_ticket_only(self):
        _, query = run_search(page([NONSTOP]), config(SEAT="business", ADULTS="2", MAX_STOPS="0"), return_date=RETURN)
        self.assertEqual((query.get_trip_type(), query.get_seat_type(), len(query.passengers)), ("round-trip", "business", 2))
        self.assertTrue(query.hide_separate_and_self_transfer)
        outbound, back = query.flight_data
        self.assertEqual((outbound.date, outbound.from_airport.airport, outbound.to_airport.airport, outbound.max_stops),
                         ("2026-12-20", "SEL", "TYO", 0))
        self.assertEqual((back.date, back.from_airport.airport, back.to_airport.airport), ("2026-12-23", "TYO", "SEL"))

    def test_a_one_way_query_has_no_return(self):
        _, query = run_search(page([NONSTOP]), config(TRIP="one-way"))
        self.assertEqual((query.get_trip_type(), len(query.flight_data)), ("one-way", 1))
        self.assertTrue(query.hide_separate_and_self_transfer)


if __name__ == "__main__":
    unittest.main()
