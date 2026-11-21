# the playwright collector, with flight_collector/common/. build from the repo root: docker build -f flight_collector/playwright/Dockerfile .
FROM python:3.12-slim

# browsers where every user can read them, and a writable home in /tmp for chromium (the root filesystem is read only)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright HOME=/tmp

WORKDIR /app
COPY flight_collector/playwright/requirements.txt .
# playwright's own node needs libatomic1 before it can install chromium and the libraries it needs
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY flight_collector/__init__.py ./flight_collector/
COPY flight_collector/common ./flight_collector/common
COPY flight_collector/playwright ./flight_collector/playwright

# no root needed, it only talks to Google and the database
USER 65532:65532
ENTRYPOINT ["python", "-u", "-m", "flight_collector.playwright"]
