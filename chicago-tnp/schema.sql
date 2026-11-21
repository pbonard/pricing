-- Chicago TNP (rideshare) trips, data.cityofchicago.org/d/6dvr-xwnh, 2025 onward. Applied by load.sh on every run,
-- it only adds what isn't there. The *_centroid_location columns (POINT text) are left out: they repeat lat/lon.
-- ZSTD: ~50 bytes a row against ~84 with the default LZ4, measured on 2025-01-01. trip_id (random hex) is most of it.
CREATE DATABASE IF NOT EXISTS tnp;

CREATE TABLE IF NOT EXISTS tnp.trips
(
    trip_id                    String CODEC(ZSTD(3)),
    -- Chicago local time, rounded to 15 minutes by the city
    trip_start_timestamp       DateTime('America/Chicago') CODEC(DoubleDelta, ZSTD(3)),
    trip_end_timestamp         Nullable(DateTime('America/Chicago')) CODEC(DoubleDelta, ZSTD(3)),
    trip_seconds               Nullable(UInt32) CODEC(ZSTD(3)),
    trip_miles                 Nullable(Float32) CODEC(ZSTD(3)),
    percent_time_chicago       Nullable(Float32) CODEC(ZSTD(3)),
    percent_distance_chicago   Nullable(Float32) CODEC(ZSTD(3)),
    -- suppressed (null) on some trips for privacy
    pickup_census_tract        LowCardinality(Nullable(String)),
    dropoff_census_tract       LowCardinality(Nullable(String)),
    pickup_community_area      Nullable(UInt8),
    dropoff_community_area     Nullable(UInt8),
    -- fare rounded to $2.50, tip to $1 by the city
    fare                       Nullable(Float32) CODEC(ZSTD(3)),
    tip                        Nullable(Float32) CODEC(ZSTD(3)),
    additional_charges         Nullable(Float32) CODEC(ZSTD(3)),
    trip_total                 Nullable(Float32) CODEC(ZSTD(3)),
    shared_trip_authorized     Bool,
    shared_trip_match          Bool,
    trips_pooled               Nullable(UInt8),
    pickup_centroid_latitude   Nullable(Float64) CODEC(ZSTD(3)),
    pickup_centroid_longitude  Nullable(Float64) CODEC(ZSTD(3)),
    dropoff_centroid_latitude  Nullable(Float64) CODEC(ZSTD(3)),
    dropoff_centroid_longitude Nullable(Float64) CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(trip_start_timestamp)
ORDER BY (trip_start_timestamp, trip_id);

-- one row per day whose rows all arrived (loaded = expected, the API's count). A rerun skips these days
CREATE TABLE IF NOT EXISTS tnp.load_log
(
    day       Date,
    expected  UInt64,
    loaded    UInt64,
    seconds   Float32,
    loaded_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY day;
