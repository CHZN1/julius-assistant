"""Bounded synthetic file-agent benchmark; never scans personal directories."""
import json, resource, tempfile, time
from pathlib import Path
from julius_file_agent import FileAgent
from julius_router import _dirs

def timed(fn):
    start=time.perf_counter();out=fn();return round((time.perf_counter()-start)*1000,3),out

def main():
    with tempfile.TemporaryDirectory(prefix='julius-file-bench-') as temp:
        root=(Path(temp)/'home').resolve();docs=root/'Documents';docs.mkdir(parents=True)
        for i in range(120):
            folder=docs/f'folder_{i:03d}';folder.mkdir()
            for j in range(100):(folder/f'item_{j:03d}.txt').write_text('benchmark data')
        source=docs/'Batch';source.mkdir()
        for i in range(50):(source/f'file_{i:03d}.txt').write_text('verified content '+str(i))
        agent=FileAgent(root,Path(temp)/'state');rows={}
        ms,scan=timed(lambda:agent.find('no-match-*.txt',recursive=True));rows['bounded_recursive_search']={'milliseconds':ms,**scan['data']};assert scan['data']['truncated'];assert scan['data']['visited_entries']<=10000
        ms,dirs=timed(lambda:_dirs(docs));rows['folder_resolution_inventory']={'milliseconds':ms,'folders':len(dirs)}
        ms,preview=timed(lambda:agent.propose('move',source=str(source),destination=str(docs/'Finished'),turn=1));assert preview['ok'];pid=preview['data']['plan']['id'];rows['preview_50_files_ms']=ms
        ms,done=timed(lambda:agent.execute(pid,confirmed=True,turn=2));assert done['ok'],done;rows['move_50_files_ms']=ms
        ms,undo=timed(agent.undo);assert undo['ok'],undo;rows['undo_50_files_ms']=ms
        assert len(list(source.glob('*.txt')))==50
        rows['process_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rows['scope']='synthetic temporary tree: 12,000 search files plus 50 move/undo files; no inference or devices'
        destination=Path('benchmarks/2026-10-01/files');destination.mkdir(parents=True,exist_ok=True);(destination/'performance.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
