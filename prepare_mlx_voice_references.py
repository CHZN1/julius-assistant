"""Export official Qwen codec prompts once, without changing reference audio."""
import os
os.environ['JULIUS_TTS']='qwen-clone'
os.environ['JULIUS_TTS_WORKER']='1'
import hashlib, json
from pathlib import Path
import numpy as np
from julius_tts import SpeechEngine
from julius_voice_identity import BALANCED_REFERENCE, LIFT_REFERENCE, REF_TEXT
out=Path('.cache/julius-mlx-prompts');out.mkdir(parents=True,exist_ok=True)
engine=SpeechEngine()
for mode,path in [('balanced',BALANCED_REFERENCE),('lift',LIFT_REFERENCE)]:
    prompt=engine.prompts[mode][0]
    codes=prompt.ref_code.detach().cpu().numpy()
    np.save(out/f'{mode}.npy',codes)
    (out/f'{mode}.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'text':REF_TEXT,'shape':list(codes.shape),'source':'Qwen/Qwen3-TTS-12Hz-0.6B-Base official encoder'}))
    print(mode,codes.shape,flush=True)
engine.close()
