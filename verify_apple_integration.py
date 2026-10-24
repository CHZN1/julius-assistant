"""Creates and removes ONLY token-owned temporary test items in Apple destinations."""
from pathlib import Path
import json,tempfile,time
from julius_apple import AppleProvider
from julius_planning import Planner
config=json.loads(Path('julius_memory/apple_calendar.json').read_text())
rows=[]
with tempfile.TemporaryDirectory() as tmp:
 planner=Planner(tmp);apple=AppleProvider(planner,config)
 try:
  for kind in ['event','reminder']:
   due=time.time()+180;end=due+60 if kind=='event' else None
   item=planner.add(kind,'Julius integration test — safe to delete',due,end)
   identifier=item['data']['id']
   saved=apple.sync(identifier,kind,'Julius integration test — safe to delete',due,end)
   row={'kind':kind,'save':saved};rows.append(row)
   if not saved.get('ok'):continue
   with planner.db() as con:link=dict(con.execute('SELECT * FROM apple_links WHERE local_id=?',(identifier,)).fetchone())
   request=dict(kind=kind,calendar_id=link['target'],token=link['token'],remote_id=link['remote_id'])
   row['inspect']=apple.call(dict(request,action='inspect'))
   row['duplicate_check']=apple.call(dict(request,action='create',title='Julius integration test — safe to delete',due=due,end=end))
   row['cleanup']=apple.cancel(identifier)
   print(json.dumps(row),flush=True)
 finally:
  Path('benchmarks/2026-10-02/apple_integration.json').write_text(json.dumps(rows,indent=2))
print('All Apple temporary-item operations recorded.')
