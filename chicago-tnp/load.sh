#!/usr/bin/env bash
# Loads Chicago TNP trips (data.cityofchicago.org/d/6dvr-xwnh) into ClickHouse, one day per query, oldest day first,
# PARALLEL days at a time. ClickHouse fetches each day from the SODA API itself (insert_day.sql), this only orders
# the days and checks them.
#
# A day counts as loaded when its rows in tnp.trips equal the API's count for it, then it goes into tnp.load_log
# and a rerun skips it. A day that doesn't match is deleted and loaded again, up to 3 times. So the job can be
# stopped and started again at any time, it goes on from the days not in load_log.
#
# Env: CLICKHOUSE_HOST, CLICKHOUSE_PASSWORD (user default), PARALLEL (8), FROM_DAY (2025-01-01), TO_DAY (empty: the
# last day the API has).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
export CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-clickhouse-clickhouse-headless.backend}
PARALLEL=${PARALLEL:-8}
FROM_DAY=${FROM_DAY:-2025-01-01}
TO_DAY=${TO_DAY:-}
API=https://data.cityofchicago.org/resource/6dvr-xwnh.csv

ch() { clickhouse-client --host "$CLICKHOUSE_HOST" --user default --password "$CLICKHOUSE_PASSWORD" "$@"; }
export -f ch

ch --multiquery < "$HERE/schema.sql"

if [ -z "$TO_DAY" ]; then
  TO_DAY=$(ch -q "SELECT toDate(parseDateTimeBestEffort(m)) FROM url('$API?\$select=max(trip_start_timestamp)%20as%20m', CSVWithNames, 'm String')")
fi

# every day from FROM_DAY to TO_DAY that isn't loaded yet, oldest first
DAYS=$(ch -q "
  SELECT d FROM (SELECT toDate('$FROM_DAY') + number AS d FROM numbers(dateDiff('day', toDate('$FROM_DAY'), toDate('$TO_DAY')) + 1))
  WHERE d NOT IN (SELECT day FROM tnp.load_log FINAL WHERE loaded = expected)
  ORDER BY d")
echo "$(date -u +%FT%TZ) $FROM_DAY..$TO_DAY: $(echo "$DAYS" | grep -c . || true) days to load, $PARALLEL at a time"

load_day() {
  local day=$1 next expected loaded start secs
  next=$(date -u -d "$day + 1 day" +%F)
  expected=$(ch -q "SELECT c FROM url('$API?\$select=count(*)%20as%20c&\$where=trip_start_timestamp%3E=%27${day}T00:00:00%27%20AND%20trip_start_timestamp%3C%27${next}T00:00:00%27', CSVWithNames, 'c UInt64')")
  for attempt in 1 2 3; do
    # rows of an earlier try that stopped half way
    if [ "$(ch -q "SELECT count() FROM tnp.trips WHERE toDate(trip_start_timestamp) = '$day'")" != 0 ]; then
      ch -q "DELETE FROM tnp.trips WHERE toDate(trip_start_timestamp) = '$day'"
    fi
    start=$(date +%s)
    if ch --param_day="$day" < "$HERE/insert_day.sql"; then
      secs=$(( $(date +%s) - start ))
      loaded=$(ch -q "SELECT count() FROM tnp.trips WHERE toDate(trip_start_timestamp) = '$day'")
      if [ "$loaded" = "$expected" ]; then
        ch -q "INSERT INTO tnp.load_log (day, expected, loaded, seconds) VALUES ('$day', $expected, $loaded, $secs)"
        echo "$(date -u +%FT%TZ) $day ok $loaded rows ${secs}s"
        return 0
      fi
      echo "$(date -u +%FT%TZ) $day try $attempt: $loaded rows, the API has $expected"
    else
      echo "$(date -u +%FT%TZ) $day try $attempt: insert failed"
    fi
    sleep $((attempt * 30))
  done
  echo "$(date -u +%FT%TZ) $day FAILED"
  return 1
}
export -f load_day
export API HERE

# xargs starts the days in order, PARALLEL at once. it exits non zero when any day failed, so does the job
echo "$DAYS" | grep . | xargs -P "$PARALLEL" -I{} bash -c 'load_day "$1"' _ {}

ch -q "SELECT count() AS days, sum(loaded) AS rows, min(day), max(day) FROM tnp.load_log FINAL WHERE loaded = expected FORMAT PrettyCompactMonoBlock"
ch -q "SELECT formatReadableSize(sum(data_compressed_bytes)) AS on_disk FROM system.parts WHERE database = 'tnp' AND table = 'trips' AND active"
