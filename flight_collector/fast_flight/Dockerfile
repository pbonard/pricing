# the fast-flights collector, with flight_collector/common/. build from the repo root: docker build -f flight_collector/fast_flight/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY flight_collector/fast_flight/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY flight_collector/__init__.py ./flight_collector/
COPY flight_collector/common ./flight_collector/common
COPY flight_collector/fast_flight ./flight_collector/fast_flight

# no root needed, it only talks to Google and the database
USER 65532:65532
ENTRYPOINT ["python", "-u", "-m", "flight_collector.fast_flight"]
