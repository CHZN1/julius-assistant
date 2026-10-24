"""Recorded-output intelligibility and pitch estimates; no listening-quality claim."""
import json, os, re
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
root=Path(__file__).resolve().parent
os.environ.setdefault('NUMBA_CACHE_DIR',str(root/'.cache/numba'))
import mlx_whisper
import argparse
p=argparse.ArgumentParser();p.add_argument('--output',default='benchmarks/2026-09-30/voice-modes/compact');args=p.parse_args()
out=root/args.output
data=json.loads((out/'comparison.json').read_text())
def words(s):return re.findall(r"[a-z0-9]+",s.lower())
def wer(ref,hyp):
 a,b=words(ref),words(hyp);row=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  nxt=[i]
  for j,y in enumerate(b,1):nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
  row=nxt
 return row[-1]/max(1,len(a))
def pitch(a,sr):
 from scipy.signal import correlate
 a=resample_poly(a,1,3);rate=sr//3;values=[]
 for i in range(0,len(a)-480,160):
  f=a[i:i+480].astype(float);f-=f.mean()
  if np.sqrt(np.mean(f*f))<.015:continue
  c=correlate(f,f,mode='full')[len(f)-1:];lo=rate//350;hi=rate//65
  lag=lo+np.argmax(c[lo:hi]);strength=c[lag]/max(c[0],1e-12)
  if strength>.65:values.append(rate/lag)
 if not values:return None
 return {'median_hz':float(np.median(values)),'p10_hz':float(np.percentile(values,10)),'p90_hz':float(np.percentile(values,90)),'voiced_frames':len(values),'method':'approximate autocorrelation; not an emotion score'}
for row in data['cases']:
 a,sr=sf.read(row['path'],dtype='float32');text=mlx_whisper.transcribe(resample_poly(a,2,3).astype('float32'),path_or_hf_repo='mlx-community/whisper-large-v3-turbo',language='en')['text']
 row.update(transcript=text,word_error_rate=wer(row['text'],text),pitch=pitch(a,sr));print(row['approach'],row['style'],text,flush=True)
 (out/'analysis.json').write_text(json.dumps(data,indent=2))
