"""Compatibility facade for the scoped, structured Julius file service.

New UI/dispatcher code should use FileAgent results directly. No mutation is
available to model tool calls; the human command session owns confirmation.
"""
from pathlib import Path
import os
from julius_file_agent import FileAgent
SAFE_ROOT=Path.home()
STATE=Path(os.environ.get('JULIUS_STATE_DIR',os.environ.get('JULIUS_MEMORY_DIR',str(Path(__file__).resolve().parent/'julius_memory')))).expanduser().resolve()
PLANS=STATE/'plans.json'  # Legacy state retained; never silently migrated/executed.
LOG=STATE/'file_log.jsonl'
_TURN={'n':0}
_INSTANCE=None

def set_turn(n): _TURN['n']=n

def agent():
    global _INSTANCE
    key=(str(SAFE_ROOT),str(STATE))
    if _INSTANCE is None or _INSTANCE[0]!=key:
        _INSTANCE=(key,FileAgent(SAFE_ROOT,STATE))
    return _INSTANCE[1]

def _safe(p): return agent().resolve(p,new=True)

def render(res):
    text=res['message'];data=res.get('data',{})
    if 'plan' in data:
        p=data['plan'];text=f"PLAN {p['id']}: "+text; text+='\n'+ '\n'.join(f"{op['from'] or 'New text file'} -> {op['to']}" for op in p['ops'][:20])
        if len(p['ops'])>20: text+=f"\n...and {len(p['ops'])-20} more."
    elif 'items' in data:
        text+='\n'+ '\n'.join(item.get('relative_path',item['name']) for item in data['items'][:40])
    elif 'text' in data: text+='\n'+data['text']
    return text

def propose_move(pattern='*',source='.',destination=None,recursive=False,**_):
    return render(agent().propose('move',pattern=pattern,source=source,destination=destination,recursive=recursive,turn=_TURN['n']))

def show_plan(plan_id,**_): return render(agent().show(plan_id))

def execute_plan(plan_id,confirmed=False,**_):
    return render(agent().execute(plan_id,confirmed=confirmed,turn=_TURN['n']))

def undo_last(**_): return render(agent().undo())
