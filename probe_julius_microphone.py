"""Bound externally: test native microphone capture without models or saved audio."""
import argparse,json,time
import numpy as np
import sounddevice as sd
p=argparse.ArgumentParser();p.add_argument('--rate',type=int,default=48000);args=p.parse_args()
print('Opening microphone at '+str(args.rate)+' Hz',flush=True)
start=time.perf_counter()
with sd.InputStream(device=0,samplerate=args.rate,channels=1,dtype='float32',blocksize=0,latency='high') as stream:
    print('Microphone open; recording two seconds',flush=True)
    audio,overflow=stream.read(args.rate*2)
print(json.dumps({'sample_rate':args.rate,'seconds':time.perf_counter()-start,'frames':len(audio),
    'overflow':bool(overflow),'rms':float(np.sqrt(np.mean(audio**2))),'peak':float(np.max(np.abs(audio)))}),flush=True)
