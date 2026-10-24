"""Lightweight temporary-data measurements; no inference, microphone or accounts."""
import json
from pathlib import Path
import resource
import statistics
import tempfile
import time
from julius_planning import Planner
from julius_conversions import convert_units
from julius_documents import read_document
from julius_file_agent import FileAgent


def measure(fn,count):
    samples=[]
    for _ in range(count):
        start=time.perf_counter();result=fn();samples.append((time.perf_counter()-start)*1000)
        if isinstance(result,dict) and not result.get('ok',True):raise RuntimeError(result)
    return {'median_ms':statistics.median(samples),'max_ms':max(samples),'samples':count}

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp).resolve();(root/'Documents').mkdir()
        p=Planner(root/'state');p.list()
        rows={'idle_reminder_poll':measure(p.claim_due,500),
              'unit_conversion':measure(lambda:convert_units(123,'mi','km'),500)}
        rows['task_create']=measure(lambda:p.add('task',str(time.monotonic_ns())),20)
        file=root/'Documents'/'notes.txt';file.write_text('Meeting on Friday\nBudget approved\n')
        rows['isolated_text_extract']=measure(lambda:read_document(str(file),file_agent=FileAgent(root,root/'state')),1)
        rows['parent_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    dest=Path('benchmarks/2026-10-01/utilities');dest.mkdir(parents=True,exist_ok=True)
    (dest/'performance.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
