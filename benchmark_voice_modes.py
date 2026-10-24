"""Compare seed-1301 composite vs two transcript-conditioned clone prompts."""
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
os.environ.setdefault('NUMBA_CACHE_DIR',str(ROOT/'.cache/numba'))
import argparse, gc, json, resource, time
import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel
REF_TEXT="Hey there. Yeah, I'm here. Oh — wait, that's actually fascinating! How did you get that working? Ha, okay, I really like that. But seriously, there is one thing we should think through before we change anything. Alright. Show me what you've got."
p=argparse.ArgumentParser();p.add_argument('--output',default='benchmarks/2026-09-30/voice-modes/chunked');args=p.parse_args();out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
refs={}
for mode,file in [('balanced','1301_a_balanced.wav'),('lift','1301_b_more_lift.wav')]:
 refs[mode]=sf.read(ROOT/'julius_1301_tuning'/file,dtype='float32')
a,sr=refs['balanced'];b,srb=refs['lift'];assert sr==srb==24000
composite=np.concatenate([a[:int(sr*2.95)],b[int(sr*2.7):int(sr*11.95)],a[int(sr*12):int(sr*17.25)],b[int(sr*17.3):]]);sf.write(out/'1301_composite_reference.wav',composite,sr)
torch.mps.set_per_process_memory_fraction(.35)
t0=time.perf_counter();model=Qwen3TTSModel.from_pretrained('Qwen/Qwen3-TTS-12Hz-0.6B-Base',device_map='mps',dtype=torch.bfloat16,attn_implementation='sdpa')
from julius_tts import SpeechEngine
from julius_voice_identity import configure_qwen_decoder
configure_qwen_decoder(model)
results={'model':'Qwen/Qwen3-TTS-12Hz-0.6B-Base','decoder_chunk_codes':96,'decoder_left_context_codes':25,'dtype':'bfloat16','sampling':'checkpoint defaults; 192-code limit per <=12-word generation span','load_seconds':time.perf_counter()-t0,'reference_text':REF_TEXT,'prompts':{},'cases':[]}
prompts={}
for mode,audio,text in [('composite',(composite,sr),REF_TEXT),('balanced',refs['balanced'],REF_TEXT),('lift',refs['lift'],REF_TEXT)]:
 t0=time.perf_counter();prompts[mode]=model.create_voice_clone_prompt(ref_audio=audio,ref_text=text,x_vector_only_mode=False)
 results['prompts'][mode]={'build_seconds':time.perf_counter()-t0,'mps_active_bytes':torch.mps.current_allocated_memory(),'reference_seconds':len(audio[0])/audio[1]}
 print('prompt',mode,results['prompts'][mode],flush=True)
texts=[('balanced','Hey there. Yeah, I am here.'),('lift',"Oh, that's fascinating! How did you figure that out?"),('lift','Ha, I really like that idea!'),('balanced','There is one thing we should think through before changing it.'),('lift','Wow, that worked! We can try the next step.'),('balanced',"Let's check the results carefully."),('balanced','Overfitting happens when a model gets too closely tied to the training data, making it perform really well on that data but terribly on new, unseen data.')]
for i,(style,text) in enumerate(texts):
 for approach in ['composite','two-prompt']:
  mode='composite' if approach=='composite' else style
  torch.manual_seed(1301+i);np.random.seed(1301+i)
  start=time.perf_counter();cpu=time.process_time()
  engine=SpeechEngine.__new__(SpeechEngine);engine.model=model;engine.prompts=prompts;engine.delivery_mode=mode
  audio,rate=engine._generate_qwen(text)
  torch.mps.synchronize()
  if not np.isfinite(audio).all():raise FloatingPointError('Nonfinite audio; needs isolated float32 retry')
  path=out/f'{approach}_{i:02d}_{style}.wav';sf.write(path,audio,rate)
  row={'generation_chunks':engine.generation_chunks,'approach':approach,'style':style,'text':text,'path':str(path),'seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu,'audio_seconds':len(audio)/rate,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'mps_active_bytes':torch.mps.current_allocated_memory(),'mps_driver_bytes':torch.mps.driver_allocated_memory()}
  with torch.inference_mode():
   emb=model.model.extract_speaker_embedding(audio=audio,sr=rate).float().flatten()
  row['speaker_cosine']={key:float(torch.nn.functional.cosine_similarity(emb,pr[0].ref_spk_embedding.float().flatten(),dim=0)) for key,pr in prompts.items()}
  results['cases'].append(row);(out/'comparison.json').write_text(json.dumps(results,indent=2));print(json.dumps(row),flush=True)
  torch.mps.empty_cache();gc.collect()
for approach in ['composite','two-prompt']:
 parts=[sf.read(row['path'],dtype='float32')[0] for row in results['cases'] if row['approach']==approach]
 joined=[]
 for part in parts:joined.extend([part,np.zeros(int(rate*.25),dtype=np.float32)])
 sf.write(out/f'{approach}_transitions.wav',np.concatenate(joined),rate)
print('Complete',flush=True)
