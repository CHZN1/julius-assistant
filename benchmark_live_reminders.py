"""Actual Qwen synthesis/playback and restart catch-up. Does not sleep the Mac."""
import argparse,json,tempfile,time
from contextlib import ExitStack
from pathlib import Path
from julius_planning import Planner
from julius_utility_session import UtilitySession
from julius_tts import SpeechEngine
p=argparse.ArgumentParser();p.add_argument('--play',action='store_true');p.add_argument('--output',default='benchmarks/2026-10-02/live_reminders');args=p.parse_args()
import soundfile as sf
import sounddevice as sd
import numpy as np
root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
rows=[];start=time.perf_counter();engine=SpeechEngine();load=time.perf_counter()-start
try:
 with tempfile.TemporaryDirectory() as tmp:
  planner=Planner(tmp);session=UtilitySession(planner)
  def speak(text):
   started=time.perf_counter();first_audio=None;audio_seconds=0;parts=[]
   with ExitStack() as stack:
    output=None
    for index,(audio,rate) in enumerate(engine.synthesize(text)):
     if first_audio is None:first_audio=time.perf_counter()-started
     audio_seconds+=len(audio)/rate
     parts.append(audio)
     sf.write(str(root/f'reminder_{len(rows)}_{index}.wav'),audio,rate)
     if args.play:
      if engine.backend=='qwen-mlx':
       if output is None:output=stack.enter_context(sd.OutputStream(samplerate=rate,channels=1,dtype='float32'))
       output.write(audio.reshape(-1,1))
      else:sd.play(audio,rate);sd.wait()
   path=root/f'joined_{len(rows)}.wav';sf.write(path,np.concatenate(parts),rate)
   from julius_mlx_voice_support import normalize_speech
   rows.append({'text':text,'spoken_text':normalize_speech(text) if engine.backend=='qwen-mlx' else text,
    'path':str(path),'first_audio_seconds':first_audio,'audio_seconds':audio_seconds,'elapsed_seconds':time.perf_counter()-started,'playback_executed':args.play,'worker_metrics':engine.worker_metrics})
  item=planner.add('reminder','the user, this is your Julius reminder test.',time.time()+1)
  time.sleep(1.1);assert session.deliver_due(speak)==[item['data']['id']]
  assert session.deliver_due(speak)==[]
  recovered=planner.add('reminder','This reminder recovered after Julius restarted.',time.time()+1)
  time.sleep(1.1)
  restarted=UtilitySession(Planner(tmp))
  assert restarted.deliver_due(speak)==[recovered['data']['id']]
  assert restarted.deliver_due(speak)==[]
  cancelled=planner.add('reminder','This cancelled reminder should never play.',time.time()+1)
  planner.finish(cancelled['data']['id']);time.sleep(1.1)
  assert session.deliver_due(speak)==[]
  result={'load_seconds':load,'cases':rows,'restart_recovery':True,'cancelled_not_played':True,'actual_os_sleep_tested':False,'listener_judgement_pending':True}
  (root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
finally:engine.close()
