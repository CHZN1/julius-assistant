# julius_voice.py — wake word + conversation sessions
import numpy as np, sounddevice as sd, mlx_whisper, ollama, time, re
from collections import deque
from kokoro import KPipeline

SR, CHUNK   = 16000, 1024
ASR         = "mlx-community/whisper-large-v3-turbo"
LLM         = "llama3.2:3b"
VOICE, SPEED = "am_adam", 1.05

WAKE          = ["julius", "juliet", "julius,", "hey julius"]   # ASR near-misses
SESSION_SEC   = 45          # stay awake this long after each exchange
SILENCE_LEN   = 1.2         # seconds of quiet that ends an utterance
MIN_SPEECH    = 0.4
SYSTEM = (
    "You are Julius, a calm and perceptive personal assistant. "
    "You speak aloud, so keep replies to one or two short sentences. "
    "No lists, no markdown, no emoji. Be direct and warm. "
    "If the user speaks another language, reply in that language."
)

FIXES = {
    r"\bAI\b":"A.I.", r"\bML\b":"M.L.", r"\bLLM\b":"L.L.M.", r"\bAPI\b":"A.P.I.",
    r"\bGPU\b":"G.P.U.", r"\bCPU\b":"C.P.U.", r"\bRAM\b":"ram", r"\bOS\b":"O.S.",
    r"\bURL\b":"U.R.L.", r"\bSQL\b":"sequel", r"\bJSON\b":"jason", r"\bCSV\b":"C.S.V.",
    r"\bPDF\b":"P.D.F.", r"\bUSB\b":"U.S.B.", r"\bUI\b":"U.I.", r"\bUX\b":"U.X.",
    r"\bNLP\b":"N.L.P.", r"\bCNN\b":"C.N.N.", r"\bRNN\b":"R.N.N.", r"\bTTS\b":"T.T.S.",
    r"\bSSD\b":"S.S.D.", r"\bVGG\b":"V.G.G.", r"\bIDE\b":"I.D.E.",
    r"\bresume\b":"résumé", r"\bcafe\b":"café", r"\bnaive\b":"naïve",
    r"\bcliche\b":"cliché", r"\bfiance\b":"fiancé", r"\bdeja vu\b":"déjà vu",
    r"(\d)\s*-\s*(\d)":r"\1 to \2", r"\bvs\.?\b":"versus",
    r"\be\.g\.\b":"for example", r"\bi\.e\.\b":"that is", r"\betc\.\b":"et cetera",
    r"&":" and ", r"%":" percent",
}

def clean_for_speech(t):
    for p, r in FIXES.items(): t = re.sub(p, r, t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", re.sub(r"[*_#`~]", "", t)).strip()

print("loading kokoro...", flush=True)
tts = KPipeline(lang_code="a")
history = [{"role":"system","content":SYSTEM}]

def speak(text):
    for _,_,a in tts(clean_for_speech(text), voice=VOICE, speed=SPEED):
        sd.play(np.asarray(a, dtype=np.float32), 24000); sd.wait()

def transcribe(audio):
    r = mlx_whisper.transcribe(audio, path_or_hf_repo=ASR,
                               condition_on_previous_text=False,
                               no_speech_threshold=0.6)
    segs = r.get("segments", [])
    nsp  = max((s.get("no_speech_prob",0) for s in segs), default=0)
    return r["text"].strip(), r.get("language","?"), nsp

def has_wake(text):
    low = re.sub(r"[^\w\s]", "", text.lower())
    return any(w.replace(",","") in low for w in WAKE)

def strip_wake(text):
    out = re.sub(r"^\W*(hey\s+)?(julius|juliet)\W*", "", text, flags=re.IGNORECASE)
    return out.strip() or text.strip()

# ---- audio capture ------------------------------------------------------
q = deque()
def cb(indata, n, t, s): q.append(indata[:,0].copy())

print("calibrating room noise — stay quiet 2s...")
with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    time.sleep(2)
floor = float(np.abs(np.concatenate(list(q))).mean()) if q else 0.001
TH = max(0.012, floor*4); q.clear()
print(f"noise floor {floor:.4f} -> threshold {TH:.4f}")
print(f'\njulius asleep · say "Julius" to wake · Ctrl+C to quit\n')

buf, recording, sil_t, spk_t = [], False, None, None
awake_until = 0.0

with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    try:
        while True:
            awake = time.time() < awake_until
            if not q: time.sleep(0.01); continue
            chunk = q.popleft()
            lvl, now = float(np.abs(chunk).mean()), time.time()

            if lvl > TH:
                if not recording:
                    recording, spk_t = True, now
                sil_t = None; buf.append(chunk)
            elif recording:
                buf.append(chunk)
                if sil_t is None: sil_t = now
                elif now - sil_t > SILENCE_LEN:
                    dur = now - spk_t
                    audio = np.concatenate(buf)
                    buf, recording, sil_t = [], False, None
                    if dur < MIN_SPEECH: continue

                    text, lang, nsp = transcribe(audio)
                    if not text or nsp > 0.6: continue

                    if not awake:
                        if not has_wake(text):
                            continue                      # ignore, stay asleep
                        text = strip_wake(text)
                        print("  [awake]")
                        if len(text.split()) < 2:         # just the name
                            awake_until = time.time() + SESSION_SEC
                            speak("Yes?")
                            continue

                    print(f"  YOU [{lang}]: {text}")
                    history.append({"role":"user","content":text})
                    t1 = time.time()
                    resp = ollama.chat(model=LLM, messages=history,
                                       options={"temperature":0.7,"num_predict":120})
                    reply = resp["message"]["content"].strip()
                    history.append({"role":"assistant","content":reply})
                    if len(history) > 21: history = [history[0]] + history[-20:]

                    print(f"  JULIUS [{time.time()-t1:.1f}s]: {reply}\n")
                    speak(reply)
                    q.clear()                             # drop his own voice
                    awake_until = time.time() + SESSION_SEC
            elif awake and now > awake_until:
                print("  [asleep]\n")
    except KeyboardInterrupt:
        print("\n  goodbye")