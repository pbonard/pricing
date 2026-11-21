-- One day of trips (Chicago time, {day:String} = YYYY-MM-DD), fetched by ClickHouse itself from the SODA API and
-- inserted as it streams. The location POINT columns are read and dropped, the booleans come quoted ("false"), so
-- they are read as text.
INSERT INTO tnp.trips
SELECT trip_id, trip_start_timestamp, trip_end_timestamp, trip_seconds, trip_miles, percent_time_chicago,
       percent_distance_chicago, pickup_census_tract, dropoff_census_tract, pickup_community_area,
       dropoff_community_area, fare, tip, additional_charges, trip_total, shared_trip_authorized = 'true', shared_trip_match = 'true',
       trips_pooled, pickup_centroid_latitude, pickup_centroid_longitude, dropoff_centroid_latitude,
       dropoff_centroid_longitude
FROM url(
    'https://data.cityofchicago.org/resource/6dvr-xwnh.csv?$where=trip_start_timestamp%3E=%27' || {day:String} ||
    'T00:00:00%27%20AND%20trip_start_timestamp%3C%27' || toString(toDate({day:String}) + 1) || 'T00:00:00%27&$limit=10000000',
    CSVWithNames,
    'trip_id String, trip_start_timestamp DateTime(\'America/Chicago\'), trip_end_timestamp Nullable(DateTime(\'America/Chicago\')),
     trip_seconds Nullable(UInt32), trip_miles Nullable(Float32), percent_time_chicago Nullable(Float32),
     percent_distance_chicago Nullable(Float32), pickup_census_tract Nullable(String), dropoff_census_tract Nullable(String),
     pickup_community_area Nullable(UInt8), dropoff_community_area Nullable(UInt8), fare Nullable(Float32), tip Nullable(Float32),
     additional_charges Nullable(Float32), trip_total Nullable(Float32), shared_trip_authorized String, shared_trip_match String,
     trips_pooled Nullable(UInt8), pickup_centroid_latitude Nullable(Float64), pickup_centroid_longitude Nullable(Float64),
     pickup_centroid_location Nullable(String), dropoff_centroid_latitude Nullable(Float64),
     dropoff_centroid_longitude Nullable(Float64), dropoff_centroid_location Nullable(String)'
)
-- http_connection_timeout: the default 1s timed out SSL handshakes with the API under load
SETTINGS date_time_input_format = 'best_effort', http_connection_timeout = 30,
         http_receive_timeout = 900, http_send_timeout = 900,
         max_threads = 2, max_insert_threads = 1
