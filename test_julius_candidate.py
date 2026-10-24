from julius_benchmark_paths import BENCHMARK_DIR
"""Exercise the candidate's actual turn function without opening mic/camera."""
import ast, json, re
from pathlib import Path
import julius_tools as jt
from julius_intents import LiveIntentRouter
from julius_backend import llm_chat
from julius_vision_mode import vision_capability_prompt
from julius_conversation import conversation_messages, authority_discussion_reply
def load_candidate_turn():
 source=ast.parse(Path('julius_candidate.py').read_text())
 names={'ask_streaming','_print_tool'}
 ns={'re':re,'jt':jt,'llm_chat':llm_chat,'intent_router':LiveIntentRouter(),'history':[],'conversation_messages':conversation_messages,'authority_discussion_reply':authority_discussion_reply}
 for n in source.body:
  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'SYSTEM','STALL','TOOL_HINT','BAD_ID'} for t in n.targets):
   exec(compile(ast.Module(body=[n],type_ignores=[]),'<candidate>','exec'),ns)
 ns['SYSTEM'] += vision_capability_prompt(False)
 ns['history']=[{'role':'system','content':ns['SYSTEM']}]
 exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<candidate>','exec'),ns)
 return ns

if __name__ == "__main__":
 ns=load_candidate_turn()
 rows=[]
 for text in ["What's the weather today?",'Is that for Springfield?','What about tomorrow?','What time is it in Tokyo?','How many hours ahead is that?','What about Egypt?',"What's the weather in New York?","What's the forecast for this week?","What's 17 times 4839?","What's Bitcoin trading at?",'What time is it?']:
  spoken=[]; out=ns['ask_streaming'](text,'',speak_fn=spoken.append)
  rows.append({'prompt':text,'answer':out,'spoken':spoken,'tool':ns['intent_router'].last})
  assert spoken==[out]
  assert not any(x in out.lower() for x in ["couldn't",'failed:', 'no data', 'no price']), (text,out)
  if 'Tokyo' in text: assert 'Asia/Tokyo' in out
  if 'Egypt' in text: assert 'Africa/Cairo' in out
  if '4839' in text: assert '82,263' in out
 (BENCHMARK_DIR/'candidate_live_tools.json').write_text(json.dumps(rows,indent=2))
 print('Actual candidate turn function completed all 11 live tool/follow-up checks')
