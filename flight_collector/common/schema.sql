-- applied by the collector at the start of every run, so it only ever adds: IF NOT EXISTS everywhere.

-- one Google Flights search: a route and a departure date at one moment. failed searches are kept too,
-- so a gap in prices can be told apart from a block, and how often blocks come can be seen
CREATE TABLE IF NOT EXISTS search
(
    id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    -- the searches of one cronjob run share it
    run_id       UUID        NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    -- what was searched: city codes (SEL, TYO, OSA) cover all of a city's airports, an airport code only itself
    origin       CHAR(3)     NOT NULL,
    destination  CHAR(3)     NOT NULL,
    depart_date  DATE        NOT NULL,
    -- 'round-trip' or 'one-way'. a round trip's flights are its outbound flights, each priced as the whole
    -- round-trip ticket with the cheapest return of return_date on the same ticket (Google doesn't say which)
    trip         TEXT        NOT NULL,
    -- null for one-way
    return_date  DATE        NULL,
    -- what was asked for, from the collector's config: the same route and date in business, or for two adults,
    -- is another price. analysis compares searches of the same seat, adults and max_stops
    seat         TEXT        NOT NULL,
    adults       SMALLINT    NOT NULL,
    -- null is any number of stops
    max_stops    SMALLINT    NULL,
    currency     CHAR(3)     NOT NULL,
    -- 'ok', 'not_found', 'blocked' or 'error'
    status       VARCHAR(16) NOT NULL,
    error        TEXT        NULL,
    flight_count INT         NOT NULL,
    PRIMARY KEY (id)
);

-- a route and date's searches over time, what every price history query starts from
CREATE INDEX IF NOT EXISTS search_route_idx ON search (origin, destination, depart_date, return_date, fetched_at);
CREATE INDEX IF NOT EXISTS search_fetched_idx ON search (fetched_at);

-- one result of a search, in the order Google listed it. for a round trip, the outbound flight
CREATE TABLE IF NOT EXISTS flight
(
    search_id        UUID      NOT NULL REFERENCES search (id) ON DELETE CASCADE,
    -- position in Google's list, 0 is the top ("best") result
    rank             SMALLINT  NOT NULL,
    -- the airports it really leaves from and lands at, one of the searched cities' (ICN, GMP -> NRT, HND, KIX, ITM)
    from_airport     CHAR(3)   NOT NULL,
    to_airport       CHAR(3)   NOT NULL,
    -- the whole ticket in the search's currency: for a round trip outbound and return together.
    -- null when Google showed no price
    price            BIGINT    NULL,
    stops            SMALLINT  NOT NULL,
    -- local time of the departure and arrival airports, as Google shows them: no zone, KST and JST are the same anyway
    depart_at        TIMESTAMP NOT NULL,
    arrive_at        TIMESTAMP NOT NULL,
    -- time in the air, summed over the legs, layovers not counted
    flying_min       INT       NOT NULL,
    carbon_g         INT       NULL,
    typical_carbon_g INT       NULL,
    -- the same flights across searches of a departure date: its legs' flight numbers, KE1105 or KE2102+JL308.
    -- a 16 character hash of the airlines and each leg's airports and departure when a leg has no number
    itinerary_key    TEXT      NOT NULL,
    PRIMARY KEY (search_id, rank)
);

-- a city pair's airport pairs compared, ICN->KIX against GMP->KIX
CREATE INDEX IF NOT EXISTS flight_airports_idx ON flight (from_airport, to_airport);

-- one flight's price over time
CREATE INDEX IF NOT EXISTS flight_itinerary_idx ON flight (itinerary_key);

-- each plane ride of an outbound flight, a nonstop has one, a one stop connection two. flight keeps the summary
-- (first departure, last arrival, stops, flying_min) so most queries never need this
CREATE TABLE IF NOT EXISTS leg
(
    search_id     UUID      NOT NULL,
    rank          SMALLINT  NOT NULL,
    -- order within the flight, 0 is the first
    seq           SMALLINT  NOT NULL,
    from_airport  CHAR(3)   NOT NULL,
    to_airport    CHAR(3)   NOT NULL,
    -- local time of each airport, as on flight
    depart_at     TIMESTAMP NOT NULL,
    arrive_at     TIMESTAMP NOT NULL,
    duration_min  INT       NOT NULL,
    -- as Google names it, "Airbus A321". null when Google leaves it out
    plane_type    TEXT      NULL,
    -- who flies it, KE / Korean Air, and its flight number, KE1105. fast-flights leaves these out, the collector
    -- reads them from Google's page itself, null when it couldn't
    airline_code  TEXT      NULL,
    airline       TEXT      NULL,
    flight_number TEXT      NULL,
    PRIMARY KEY (search_id, rank, seq),
    FOREIGN KEY (search_id, rank) REFERENCES flight (search_id, rank) ON DELETE CASCADE
);

-- where the connections stop over, FUK, KIX
CREATE INDEX IF NOT EXISTS leg_from_airport_idx ON leg (from_airport);

-- an airline's flights, low cost against full service
CREATE INDEX IF NOT EXISTS leg_airline_code_idx ON leg (airline_code);
