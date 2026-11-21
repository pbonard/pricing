-- queries over search (s) and flight (f), flight_collector/common/schema.sql. from a laptop:
--   kubectl -n backend port-forward svc/postgres-rw 5432 &
--   psql "$(kubectl -n backend get secret postgres-app -o jsonpath='{.data.uri}' | base64 -d | sed 's/postgres-rw.backend/localhost/')" -f analysis/analysis.sql
-- only ok searches have flights, so joining the two already leaves the failed ones out.
-- times are read in Seoul time: s.fetched_at AT TIME ZONE 'Asia/Seoul'
-- prices are whole tickets: for round trips (the default) outbound and return together, and the flights the outbound.
-- they assume one search config (trip, stay, seat, adults, max_stops, currency). when the config was changed, add
-- s.trip = 'round-trip' AND s.return_date - s.depart_date = 3 AND s.seat = 'economy' AND s.adults = 1
-- AND s.max_stops IS NULL AND s.currency = 'KRW' (or the ones wanted) to the WHERE

-- 1. is the collector healthy: searches per status per day. a run of 'blocked' means slow down or add a proxy
SELECT (fetched_at AT TIME ZONE 'Asia/Seoul')::DATE AS day_kst, status, count(*)
FROM search
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

-- 2. cheapest nonstop price per route and departure date, every time it was looked at
SELECT s.origin, s.destination, s.depart_date, s.fetched_at AT TIME ZONE 'Asia/Seoul' AS fetched_kst,
       min(f.price) AS cheapest
FROM search s
         JOIN flight f ON f.search_id = s.id
WHERE f.stops = 0 AND f.price IS NOT NULL
GROUP BY s.id
ORDER BY s.origin, s.destination, s.depart_date, s.fetched_at;

-- 3. booking curve: the cheapest nonstop by how many days before departure it was seen, over all departure dates.
-- answers "how far ahead is cheapest"
SELECT s.origin, s.destination,
       s.depart_date - (s.fetched_at AT TIME ZONE 'Asia/Seoul')::DATE AS days_before,
       min(f.price)                                               AS lowest,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY f.price)       AS median,
       count(DISTINCT s.depart_date)                              AS dates
FROM search s
         JOIN flight f ON f.search_id = s.id
WHERE f.stops = 0 AND f.price IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3 DESC;

-- 4. which hour of the day (KST) the cheapest price of a search turned up, does checking at night pay off
WITH per_search AS (SELECT s.origin, s.destination, s.depart_date, s.fetched_at, min(f.price) AS cheapest
                    FROM search s
                             JOIN flight f ON f.search_id = s.id
                    WHERE f.stops = 0 AND f.price IS NOT NULL
                    GROUP BY s.id),
     ranked AS (SELECT *, cheapest - min(cheapest) OVER (PARTITION BY origin, destination, depart_date) AS above_low
                FROM per_search)
SELECT extract(HOUR FROM fetched_at AT TIME ZONE 'Asia/Seoul') AS hour_kst,
       avg(above_low)::INT                                     AS avg_above_lowest,
       count(*)
FROM ranked
GROUP BY 1
ORDER BY 1;

-- 5. which airports are cheaper: the cheapest nonstop of each airport pair per city pair and departure date,
-- from the last day of searches. ICN vs GMP, NRT vs HND, KIX vs ITM
SELECT s.origin, s.destination, s.depart_date, f.from_airport, f.to_airport,
       min(f.price) AS cheapest, count(*) AS flights
FROM search s
         JOIN flight f ON f.search_id = s.id
WHERE f.stops = 0 AND f.price IS NOT NULL
  AND s.fetched_at > now() - INTERVAL '1 day'
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 3, cheapest;

-- 6. one flight's price over time: an itinerary_key (flight numbers, from the query below) on a departure date
SELECT s.fetched_at AT TIME ZONE 'Asia/Seoul' AS fetched_kst,
       s.depart_date - (s.fetched_at AT TIME ZONE 'Asia/Seoul')::DATE AS days_before,
       f.price, f.depart_at, f.arrive_at
FROM search s
         JOIN flight f ON f.search_id = s.id
WHERE f.itinerary_key = '<itinerary_key>' AND s.depart_date = DATE '2026-12-20'
ORDER BY s.fetched_at;

-- 7. cheapest nonstop by airline for a departure date, as of the latest search. a nonstop has one leg, its airline
SELECT DISTINCT ON (s.origin, s.destination, l.airline_code)
       s.origin, s.destination, l.airline_code, l.airline, f.itinerary_key, f.price,
       f.from_airport, f.to_airport, f.depart_at, s.fetched_at AT TIME ZONE 'Asia/Seoul' AS fetched_kst
FROM search s
         JOIN flight f ON f.search_id = s.id
         JOIN leg l ON (l.search_id, l.rank) = (f.search_id, f.rank)
WHERE s.depart_date = DATE '2026-12-20' AND f.stops = 0 AND f.price IS NOT NULL
ORDER BY s.origin, s.destination, l.airline_code, s.fetched_at DESC, f.price;
