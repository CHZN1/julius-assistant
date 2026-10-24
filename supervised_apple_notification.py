"""Create/inspect/clean only a token-owned supervised Apple notification test."""
import argparse,json,time
from datetime import datetime
from pathlib import Path
from julius_apple import AppleProvider
from julius_planning import Planner
p=argparse.ArgumentParser();p.add_argument('action',choices=['create','inspect','complete','cleanup']);p.add_argument('--state-dir',default='.cache/supervised-apple-notification');args=p.parse_args()
root=Path(args.state_dir);root.mkdir(parents=True,exist_ok=True)
planner=Planner(root);apple=AppleProvider(planner,json.loads(Path('julius_memory/apple_calendar.json').read_text()))
state_path=root/'test.json'
if args.action=='create':
    if state_path.exists():raise RuntimeError('Existing supervised item must be inspected/cleaned before creating another')
    title='Julius supervised notification test';due=time.time()+120
    item=planner.add('reminder',title,due);identifier=item['data']['id']
    state={'id':identifier,'due':due,'title':title};state_path.write_text(json.dumps(state,indent=2))
    result=apple.sync(identifier,'reminder',title,due)
else:
    state=json.loads(state_path.read_text());identifier=state['id']
    if args.action=='cleanup':result=apple.cancel(identifier)
    elif args.action=='complete':
        result=apple.complete(identifier)
        if result.get('ok'):result=planner.finish(identifier,'done','reminder')
    else:
        with planner.db() as con:link=dict(con.execute('SELECT * FROM apple_links WHERE local_id=?',(identifier,)).fetchone())
        result=apple.call(dict(action='inspect',kind='reminder',calendar_id=link['target'],token=link['token'],remote_id=link['remote_id']))
(root/f'{args.action}_result.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'ok':result.get('ok'),'code':result.get('code'),'action':args.action,
    'title':state['title'],'due_local':datetime.fromtimestamp(state['due']).astimezone().isoformat(),
    'alerts':result.get('alerts'),'alert_due':result.get('alert_due'),'completed':result.get('completed'),'message':result.get('message')}),flush=True)
if not result.get('ok'):raise SystemExit(1)
