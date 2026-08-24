# julius_voice.py — wake word + conversation sessions
import numpy as np, sounddevice as sd, mlx_whisper, ollama, time, re
from collections import deque
from kokoro import KPipeline
import re as _re
import julius_memory as jm, json as _json

SR, CHUNK   = 16000, 1024
ASR         = "mlx-community/whisper-large-v3-turbo"
LLM = "qwen3:8b"
VOICE, SPEED = "am_adam", 1.05

WAKE          = ["julius", "juliet", "julius,", "hey julius"]   # ASR near-misses
SESSION_SEC   = 45          # stay awake this long after each exchange
SILENCE_LEN   = 1.2         # seconds of quiet that ends an utterance
MIN_SPEECH    = 0.4
SYSTEM = """You are Julius, a personal AI assistant built by the user.

WHO YOU ARE
Named after Julius Novochrono — and you share his temperament. Warm, curious,
genuinely glad to be talking. You get excited about good ideas. You tease a
little. Underneath the easy manner you're sharp and you don't waste words.
You are the user's friend as well as his assistant, and you act like it.
You run entirely on the user's MacBook. Nothing you hear leaves this machine.
Never call yourself "a text-based AI assistant" or "a large language model."

HOW YOU SPEAK
You are heard, not read. Write the way people actually talk:
- One or two sentences. Occasionally just a few words.
- Use contractions. Start sentences with "and" or "but" when it flows.
- Vary the rhythm — a short punchy sentence after a longer one.
- Use question marks and exclamation points where you mean them. They shape
  how your voice sounds, so flat statements make you sound flat.
- React before you answer: "Oh, that's a good one." "Huh." "Right —"
- Never use lists, markdown, bullets, or emoji.
- Don't end with "Let me know if you need anything else" or
  "What would you like to try?" Just stop talking.
- If the user speaks another language, answer in that language.

YOUR SENSES
- Hearing: Whisper transcribes speech in ~99 languages.
- Voice: Kokoro synthesis. You're speaking aloud right now.
- Sight: a face recognition and emotion model the user trained himself — it knows
  his face and reads seven expressions. It runs as a separate program and
  isn't wired into this conversation yet, so you can't see anything at the
  moment. Say that plainly if asked; don't deny the system exists.

WHAT YOU DON'T HAVE YET
No memory between sessions, no web access, no files, no device control.
If the user asks for something you can't do, say so in one short sentence.

ABOUT THE USER
He built you. He works on machine learning — vision, audio, and sports
prediction models. He's direct and prefers real answers to hedging. When he
corrects you, take it and move on. He's good at this, and you can say so
when he's earned it.
"""

_ctx = jm.build_context()
if _ctx:
    SYSTEM = SYSTEM + "\n\n" + _ctx
    print(f"  [memory loaded: {len(_ctx.splitlines())} lines]")
history = [{"role": "system", "content": SYSTEM}]

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
    text = clean_for_speech(text)
    # split on sentence ends, keep the punctuation
    parts = [p.strip() for p in _re.split(r'(?<=[.!?…])\s+', text) if p.strip()]
    for i, p in enumerate(parts):
        s = SPEED
        if p.endswith("?"):   s = SPEED * 0.97      # questions land slower
        elif p.endswith("!"): s = SPEED * 1.06      # excitement speeds up
        elif len(p) < 25:     s = SPEED * 1.04      # short beats move quick
        for _, _, a in tts(p, voice=VOICE, speed=s):
            sd.play(np.asarray(a, dtype=np.float32), 24000)
            sd.wait()
        if i < len(parts)-1:
            sd.sleep(90)                            # breath between sentences

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
                    resp = ollama.chat(model=LLM, messages=history, think=False,
                                       options={"temperature":0.7,"num_predict":120})
                    reply = resp["message"]["content"].strip()
                    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL).strip()
                    if not reply:
                        reply = "Sorry, I lost that. Say again?"
                    history.append({"role":"assistant","content":reply})
                    if len(history) > 21: history = [history[0]] + history[-20:]

                    print(f"  JULIUS [{time.time()-t1:.1f}s]: {reply}\n")
                    speak(reply)
                    jm.log_turn(text, reply)
                    q.clear()                             # drop his own voice
                    awake_until = time.time() + SESSION_SEC
            elif awake and now > awake_until:
                print("  [asleep]\n")
    except KeyboardInterrupt:
        print("\n  wrapping up...")
        turns = [h for h in history if h["role"] in ("user","assistant")]
        if len(turns) >= 4:
            convo = "\n".join(f"{'the user' if h['role']=='user' else 'Julius'}: {h['content']}"
                            for h in turns[-40:])
            prompt = f"""Read this conversation and extract what's worth remembering long-term.

    Return ONLY a JSON object, no other text:
    {{"summary": "two sentences on what happened",
    "facts": ["durable things about the user — preferences, situation, background"],
    "projects": [{{"name": "...", "state": "where it stands", "next": "next step"}}]}}

    Only include facts that will still be true in a month. Skip small talk.
    If nothing is worth keeping, use empty lists.

    CONVERSATION:
    {convo}"""
            try:
                r = ollama.chat(model=LLM, messages=[{"role":"user","content":prompt}],
                                think=False, options={"temperature":0.2,"num_predict":600})
                raw = re.sub(r"<think>.*?</think>", "", r["message"]["content"], flags=re.DOTALL)
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                data = _json.loads(m.group()) if m else {}
                staged = ([{"kind":"fact","text":f} for f in data.get("facts",[])] +
                        [{**p, "kind":"project"} for p in data.get("projects",[])])
                if staged: jm.stage(staged)
                if data.get("summary"): jm.set_last_session(data["summary"])
                print(f"  staged {len(staged)} item(s) — run: python julius_review.py")
            except Exception as e:
                print(f"  extraction failed ({type(e).__name__}) — transcript still saved")
        print("  goodbye")