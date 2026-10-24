import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
BENCHMARK_DIR = Path(os.environ.get('JULIUS_BENCHMARK_DIR', str(Path(__file__).parent/'benchmarks'/str(datetime.now(ZoneInfo('America/Los_Angeles')).date()))))
BENCHMARK_DIR.mkdir(parents=True,exist_ok=True)
