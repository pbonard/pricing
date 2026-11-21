"""The fast-flights collector: python -m flight_collector.fast_flight, from the repo root."""

import sys
from contextlib import nullcontext

from ..common import run

from .search import search

# a plain request per search, nothing to open for the run
sys.exit(run.main(lambda config: nullcontext(lambda *where: search(config, *where))))
