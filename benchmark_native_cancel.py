"""Cancel actual native inference, verify weight release and next-turn recovery."""
import json,time,threading
from julius_backend import prepare_backend,close_backend,release_metrics
from julius_cancellation import Cancellation,TurnCancelled
from test_julius_candidate import load_candidate_turn
from julius_benchmark_paths import BENCHMARK_DIR
prepare_backend();ns=load_candidate_turn();control=Cancellation();ns['cancellation']=control;requested=[]
def cancel():
 requested.append(time.perf_counter());control.request()
timer=threading.Timer(2,cancel)
try:
 with control.activity():
  timer.start()
  try:ns['ask_streaming']('Explain overfitting and several ways to prevent it.','',speak_fn=lambda _:None)
  except TurnCancelled:pass
  else:raise AssertionError('Inference finished without exercising cancellation')
 close_backend();latency=time.perf_counter()-requested[0];released=release_metrics()
 assert released['active_memory_bytes']<100*1024**2,released
 with control.activity():answer=ns['ask_streaming']('Say only Ready.','',speak_fn=lambda _:None)
 assert answer and 'ready' in answer.lower(),answer
 close_backend()
 row={'cancel_to_release_seconds':latency,'cancel_release':released,'recovery_answer':answer,'recovery_release':release_metrics()}
 (BENCHMARK_DIR/'native_cancel.json').write_text(json.dumps(row,indent=2));print(json.dumps(row),flush=True)
finally:timer.cancel();close_backend()
