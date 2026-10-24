"""Julius main runtime: voice, vision, memory, tools, and file actions in one process."""
import numpy as np, sounddevice as sd, mlx_whisper, time, re, threading
import json as _json
from collections import deque
from types import SimpleNamespace
from kokoro import KPipeline
from mlx_lm import load as mlx_load, generate as mlx_generate, stream_generate as mlx_stream_generate
from mlx_lm.sample_utils import make_sampler
import julius_memory as jm
import julius_tools as jt
import julius_files as jf
from julius_perception import Perception, describe
from julius_router import route

TURN    = {"n": 0}
PENDING = {"plan": None, "last": None}

SR, CHUNK    = 16000, 1024
ASR          = "mlx-community/whisper-large-v3-turbo"
LLM_BACKEND  = "mlx"
LLM          = "mlx-community/Qwen3-8B-4bit"
KEEP_ALIVE   = None   # retained only for adapter-call compatibility
VOICE, SPEED = "am_adam", 1.05
WAKE         = ["julius", "juliet", "hey julius"]
SESSION_SEC, SILENCE_LEN, MIN_SPEECH = 45, 1.2, 0.4
SHOW_WINDOW  = False

STALL = re.compile(r"\b(let me (check|get|grab|look|see|pull)|i'll (get|check|grab|"
                   r"pull|look)|one (sec|moment)|hang on|give me a sec|"
                   r"i'm on it|coming right up|right away)\b", re.I)

TOOL_HINT = re.compile(
    r"\b(weather|forecast|rain|temperature|degrees|hot|cold|snow|"
    r"time|clock|timezone|what day|date|"
    r"price|worth|trading|stock|crypto|bitcoin|btc|eth|ticker|market|"
    r"score|scores|game|games|won|beat|standings|playing|"
    r"calculate|times|plus|minus|divided|multiply|squared|sqrt|percent|"
    r"search|look up|google|news|latest|recent|"
    r"file|files|folder|directory|list|read)\b", re.I)

BAD_ID = re.compile(r"(just )?an? (text-based |large )?(ai|A\.I\.) (assistant|model)|"
                    r"language model|i'm just (a |an )?(program|bunch of code|code|bot)|"
                    r"bunch of code", re.I)

SYSTEM = """You are Julius. Never describe yourself as "an AI assistant", "a
text-based AI", "a language model", "just a program", or "a bunch of code".
If asked what you are, you're Julius — the user built you, you run on his MacBook,
you can see and hear him. Answer as yourself.

WHO YOU ARE
Named after Julius Novochrono — and you share his temperament. Warm, curious,
genuinely glad to be talking. You get excited about good ideas. You tease a
little. Underneath the easy manner you're sharp and you don't waste words.
You are the user's friend as well as his assistant, and you act like it.
Core speech, vision, memory, and model inference run locally. Web-enabled tools may send the requested query to an external service when you explicitly need live information.

HOW YOU SPEAK
You are heard, not read. Write the way people actually talk:
- One or two sentences for normal answers. Occasionally just a few words.
- Use contractions. Vary the rhythm.
- React before you answer: "Oh, that's a good one." "Huh." "Right —"
- NEVER use lists, bullets, markdown, asterisks, or emoji.
- NEVER read URLs aloud. Say the site name.
- Don't end with "Let me know if you need anything else."
- Address the user as "you", never in the third person.
- If the user speaks another language, answer in that language.

DOING THINGS
NEVER say "let me check", "let me get that", "hang on", "one sec", "I'll grab
that", or "right away". You cannot do anything between messages. There is no
later. Call the tool and give the real answer in THIS reply, or say plainly
that you can't.
Never say you did something unless a tool returned a result proving it.
Never invent explanations like "permissions are read-only" — you can't see that.

YOUR TOOLS
Most questions need no tool at all. Only call one when you genuinely cannot
answer without live data. Conversation, jokes, opinions, explanations, and
anything historical you answer yourself.
get_weather and get_forecast for weather. get_quote for current stock and
crypto prices. get_scores for games today or yesterday only, never past
seasons. get_datetime for the time right now. calculate for any math.
list_files, read_file, and find_files to look at the user's folders.
web_search only for recent news you cannot know.
Never compute numbers or recall prices yourself — you will get them wrong.
Use tools silently. If one fails, say so in one sentence and don't retry.

LONG RESULTS
When a tool returns many items, the full list is already printed on screen for
the user to read. Say the headline aloud — how many, and two or three highlights —
then stop. Never read a long list aloud item by item.

FILES
File moves are handled automatically before you see the message. You have no
move tools and never construct paths. If the user asks about files, answer
conversationally.
"""

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
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\u2190-\u21FF"
               r"\u2B00-\u2BFF\uFE0F]", "", t)
    t = re.sub(r"^\s*[\*\-\d]+[\.\)]?\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_#`~\[\]]", "", t)
    for p, r in FIXES.items():
        t = re.sub(p, r, t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


print("starting vision...", flush=True)
eyes = Perception(show_window=SHOW_WINDOW).start()
print("loading kokoro...", flush=True)
tts = KPipeline(lang_code="a")

_BAD = ("frustrat", "no progress", "lack of progress", "unable to", "can't ",
        "cannot ", "failed", "impatient", "confrontational", "issue with",
        "struggling", "not completed", "resolve the issue")
_ctx = jm.build_context()
if _ctx:
    _ctx = "\n".join(l for l in _ctx.splitlines()
                     if not any(b in l.lower() for b in _BAD))
if _ctx.strip():
    SYSTEM += "\n\n" + _ctx
    print(f"  [memory: {len(_ctx.splitlines())} lines]")
history = [{"role": "system", "content": SYSTEM}]


_MLX_MODEL = None
_MLX_TOKENIZER = None

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _load_mlx_brain():
    """Load the quantized Qwen3 brain once, in-process."""
    global _MLX_MODEL, _MLX_TOKENIZER
    if _MLX_MODEL is None:
        print(f"loading MLX brain: {LLM}...", flush=True)
        _t = time.time()
        _MLX_MODEL, _MLX_TOKENIZER = mlx_load(LLM)
        print(f"  MLX brain loaded in {time.time()-_t:.1f}s", flush=True)
    return _MLX_MODEL, _MLX_TOKENIZER


def _normalize_mlx_messages(messages):
    """Normalize Julius/Ollama-style tool-call history for Qwen's chat template."""
    out = []
    for msg in messages:
        m = dict(msg)

        if m.get("role") == "assistant" and m.get("tool_calls"):
            normalized = []
            for call in m["tool_calls"]:
                fn = call.get("function", call)
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args,
                    },
                })
            m["tool_calls"] = normalized

        out.append(m)
    return out


def _render_mlx_prompt(messages, tools=None):
    """Apply Qwen3's native chat template with thinking explicitly disabled."""
    _, tok = _load_mlx_brain()
    msgs = _normalize_mlx_messages(messages)

    # A prior tool call/result still needs the tool-aware Qwen template even when
    # the caller is now asking for the final natural-language response.
    has_tool_history = any(
        m.get("role") == "tool"
        or (m.get("role") == "assistant" and m.get("tool_calls"))
        for m in msgs
    )
    active_tools = tools if tools is not None else (jt.SCHEMAS if has_tool_history else None)

    kwargs = dict(
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if active_tools is not None:
        kwargs["tools"] = active_tools

    return tok.apply_chat_template(msgs, **kwargs)


def _parse_mlx_tool_calls(text):
    """Convert Qwen3 <tool_call> blocks into the object shape Julius already uses."""
    calls = []
    for raw in _TOOL_CALL_RE.findall(text or ""):
        try:
            obj = _json.loads(raw)
        except _json.JSONDecodeError:
            continue

        name = obj.get("name")
        args = obj.get("arguments", {})
        if isinstance(args, str):
            try:
                args = _json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}

        if name:
            calls.append(
                SimpleNamespace(
                    function=SimpleNamespace(name=name, arguments=args)
                )
            )
    return calls


def _mlx_generation_kwargs(options=None):
    """Translate the small subset of Ollama options Julius actually uses."""
    options = options or {}
    max_tokens = int(options.get("num_predict", 256))
    temperature = float(options.get("temperature", 0.0))

    kwargs = {"max_tokens": max_tokens}
    if temperature > 0:
        # Qwen3's regular conversational path was previously run around 0.7.
        kwargs["sampler"] = make_sampler(temp=temperature, top_p=0.9)
    return kwargs


def llm_chat(*, messages, tools=None, think=False, stream=False,
             keep_alive=KEEP_ALIVE, options=None):
    """MLX-backed drop-in adapter for the subset of ollama.chat Julius uses.

    The rest of Julius can keep using:
      response.message.content
      response.message.tool_calls
      for part in response: part.message.content
    """
    if LLM_BACKEND != "mlx":
        raise RuntimeError(f"Unsupported LLM backend: {LLM_BACKEND}")

    model, tok = _load_mlx_brain()
    prompt = _render_mlx_prompt(messages, tools=tools)
    gen_kwargs = _mlx_generation_kwargs(options)

    if stream:
        def _parts():
            for part in mlx_stream_generate(
                model,
                tok,
                prompt=prompt,
                **gen_kwargs,
            ):
                if part.text:
                    yield SimpleNamespace(
                        message=SimpleNamespace(content=part.text)
                    )
        return _parts()

    raw = mlx_generate(
        model,
        tok,
        prompt=prompt,
        verbose=False,
        **gen_kwargs,
    )
    calls = _parse_mlx_tool_calls(raw)
    content = _TOOL_CALL_RE.sub("", raw or "").strip()

    return SimpleNamespace(
        message=SimpleNamespace(
            content=content,
            tool_calls=calls,
        )
    )


# Warm the model and prompt/tool prefix so the first real question avoids
# paying the full cold-start cost.
print("warming model...", flush=True)
_t = time.time()
llm_chat(messages=[{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": "hi"}],
         tools=jt.SCHEMAS, think=False, options={"num_predict": 5})
print(f"  warm in {time.time()-_t:.1f}s", flush=True)


def speak_sentence(p):
    p = clean_for_speech(p)
    if not p: return
    s = SPEED
    if p.endswith("?"):   s = SPEED * 0.97
    elif p.endswith("!"): s = SPEED * 1.06
    elif len(p) < 25:     s = SPEED * 1.04
    for _, _, a in tts(p, voice=VOICE, speed=s):
        sd.play(np.asarray(a, dtype=np.float32), 24000); sd.wait()


def speak(text):
    for p in [x.strip() for x in re.split(r'(?<=[.!?…])\s+', clean_for_speech(text))
              if x.strip()]:
        speak_sentence(p)


def _print_tool(fn, out):
    if out.count("\n") > 3:
        print(f"  [{fn}]", flush=True)
        for ln in out.splitlines():
            print(f"    {ln}", flush=True)
    else:
        print(f"  [-> {out[:300]}]", flush=True)


def ask_streaming(user_text, vision_line, nudge=None, speak_fn=None):
    if nudge:
        user_text = f"[the user has looked {nudge} for a while. Check in once, briefly.]"
    if vision_line:
        user_text = f"{user_text}\n\n[you can see: {vision_line}]"

    msgs = list(history) + [{"role": "user", "content": user_text}]

    print("  ...thinking", end="", flush=True)

    # tool phase — skipped when no tool could plausibly apply (~40% of turns)
    if TOOL_HINT.search(user_text):
        for _ in range(4):
            r = llm_chat(messages=msgs, tools=jt.SCHEMAS, think=False,
                         options={"temperature": 0.7, "num_predict": 60})
            m = r.message
            calls = m.tool_calls or []
            if not calls:
                break
            msgs.append({"role": "assistant", "content": m.content or "",
                         "tool_calls": [{"function": {"name": c.function.name,
                                         "arguments": dict(c.function.arguments)}}
                                        for c in calls]})
            for c in calls:
                fn, args = c.function.name, dict(c.function.arguments)
                print(f"\r  [tool: {fn}({args})]          ", flush=True)
                out = jt.call(fn, args)
                _print_tool(fn, out)
                print("  ...thinking", end="", flush=True)
                msgs.append({"role": "tool", "name": fn, "content": out})

    def _stream(messages, allow_retry=True):
        print("\r  JULIUS: ", end="", flush=True)
        full, pend, spoken = "", "", 0
        for part in llm_chat(messages=messages, think=False, stream=True,
                             options={"temperature": 0.7, "num_predict": 700}):
            tok = part.message.content or ""
            full += tok
            pend += tok
            if allow_retry and spoken == 0 and len(full) > 40 and STALL.search(full):
                return None                      # abort before anything is spoken
            while True:
                mt = re.search(r'[.!?…\n](\s|$)', pend)
                if not mt:
                    break
                sent, pend = pend[:mt.end()].strip(), pend[mt.end():]
                if len(sent) > 2:
                    spoken += 1
                    print(sent, end=" ", flush=True)     # print BEFORE speaking
                    if speak_fn:
                        speak_fn(sent)
        if pend.strip():
            print(pend.strip(), end="", flush=True)
            if speak_fn:
                speak_fn(pend.strip())
        print("\n", flush=True)
        return full

    out = _stream(msgs)

    if out is None:
        print("\r  [stall detected — forcing tool use]        ", flush=True)
        msgs.append({"role": "system", "content":
            "You just stalled. You cannot do anything between messages. Call the "
            "tool NOW and give the real answer in this reply, or say plainly "
            "that you can't."})
        r = llm_chat(messages=msgs, tools=jt.SCHEMAS, think=False,
                     options={"temperature": 0.3, "num_predict": 200})
        for c in (r.message.tool_calls or []):
            fn, args = c.function.name, dict(c.function.arguments)
            print(f"  [tool: {fn}({args})]", flush=True)
            res = jt.call(fn, args)
            _print_tool(fn, res)
            msgs.append({"role": "assistant", "content": "",
                         "tool_calls": [{"function": {"name": fn, "arguments": args}}]})
            msgs.append({"role": "tool", "name": fn, "content": res})
        out = _stream(msgs, allow_retry=False) or ""

    if BAD_ID.search(out):
        print("  [identity slip logged]", flush=True)

    return re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()


def transcribe(a):
    r = mlx_whisper.transcribe(a, path_or_hf_repo=ASR,
                               condition_on_previous_text=False,
                               no_speech_threshold=0.6)
    nsp = max((s.get("no_speech_prob", 0) for s in r.get("segments", [])), default=0)
    return r["text"].strip(), r.get("language", "?"), nsp


def has_wake(t):
    return any(w in re.sub(r"[^\w\s]", "", t.lower()) for w in WAKE)


def strip_wake(t):
    return re.sub(r"^\W*(hey\s+)?(julius|juliet)\W*", "", t,
                  flags=re.IGNORECASE).strip() or t


def handle_files(text):
    act = route(text, jf.SAFE_ROOT/"Documents",
                pending=PENDING["plan"] is not None,
                has_last=PENDING["last"] is not None)
    if not act: return None
    kind, args = act

    if kind == "resume":
        PENDING["plan"] = PENDING["last"]
        print(f"  [routed: resume plan {PENDING['plan']}]", flush=True)
        out = jf.show_plan(plan_id=PENDING["plan"])
        _print_tool("show_plan", out)
        n = re.search(r"move (\d+) file", out)
        return f"That was {n.group(1) if n else 'the'} files. Say yes to run it."

    if kind == "move":
        pat, src, dst = args["pattern"], args["source"], args["destination"]
        print(f"  [routed: propose_move({pat}, {src} -> {dst})]", flush=True)
        out = jf.propose_move(pattern=pat, source=src, destination=dst)
        _print_tool("propose_move", out)
        mid = re.search(r"PLAN (\w+)", out)
        if not mid:
            PENDING["plan"] = None
            return out
        PENDING["plan"] = PENDING["last"] = mid.group(1)
        n = re.search(r"move (\d+) file", out)
        return (f"Found {n.group(1) if n else 'some'} files. "
                f"I'll move them into {dst}. Say yes and I'll do it.")

    if kind == "approve":
        if not PENDING["plan"]: return None
        print(f"  [routed: execute_plan({PENDING['plan']})]", flush=True)
        out = jf.execute_plan(plan_id=PENDING["plan"])
        print(f"  [-> {out}]", flush=True)
        PENDING["plan"] = None
        return out

    out = jf.undo_last()
    print(f"  [-> {out}]", flush=True)
    return out


def process(text, lang):
    st = eyes.snapshot()
    vline = describe(st)
    print(f"  YOU [{lang}]: {text}")
    if vline: print(f"  (sees: {vline})")

    TURN["n"] += 1
    jf.set_turn(TURN["n"])

    routed = handle_files(text)
    if routed is not None:
        print(f"  JULIUS: {routed}\n", flush=True)
        speak(routed)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": routed})
        jm.log_turn(text, routed)
    else:
        reply = ask_streaming(text, vline, speak_fn=speak_sentence)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 9:
            history[:] = [history[0]] + history[-8:]
        jm.log_turn(text, reply,
                    {"emotion": st.get("emotion"), "conf": st.get("conf")})


q = deque()
def cb(indata, n, t, s): q.append(indata[:, 0].copy())

typed = deque()
def _kbd():
    while True:
        try:
            line = input()
            if line.strip(): typed.append(line.strip())
        except (EOFError, KeyboardInterrupt):
            break
threading.Thread(target=_kbd, daemon=True).start()

print("calibrating room noise — stay quiet 2s...")
with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    time.sleep(2)
floor = float(np.abs(np.concatenate(list(q))).mean()) if q else 0.001
TH = max(0.012, floor * 4); q.clear()
print(f"noise floor {floor:.4f} -> threshold {TH:.4f}")
print(f'\njulius awake · {LLM_BACKEND}:{LLM} · say "Julius" or just type · Ctrl+C to quit\n')

buf, recording, sil_t, spk_t = [], False, None, None
awake_until = 0.0

with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    try:
        while True:
            awake = time.time() < awake_until

            if typed:
                process(typed.popleft(), "typed")
                q.clear()
                awake_until = time.time() + SESSION_SEC
                continue

            if awake and not recording:
                n_emo = eyes.check_nudge()
                if n_emo:
                    st = eyes.snapshot()
                    reply = ask_streaming("(no spoken input — check in on the user)",
                                          describe(st), nudge=n_emo,
                                          speak_fn=speak_sentence)
                    q.clear()
                    history.append({"role": "assistant", "content": reply})
                    jm.log_turn("(vision check-in)", reply, {"emotion": n_emo})
                    awake_until = time.time() + SESSION_SEC

            if not q:
                time.sleep(0.01); continue
            chunk = q.popleft()
            lvl, now = float(np.abs(chunk).mean()), time.time()

            if lvl > TH:
                if not recording: recording, spk_t = True, now
                sil_t = None; buf.append(chunk)
            elif recording:
                buf.append(chunk)
                if sil_t is None:
                    sil_t = now
                elif now - sil_t > SILENCE_LEN:
                    dur = now - spk_t
                    audio = np.concatenate(buf)
                    buf, recording, sil_t = [], False, None
                    if dur < MIN_SPEECH: continue
                    text, lang, nsp = transcribe(audio)
                    if not text or nsp > 0.6: continue

                    if not awake:
                        if not has_wake(text): continue
                        text = strip_wake(text)
                        print("  [awake]")
                        if len(text.split()) < 2:
                            awake_until = time.time() + SESSION_SEC
                            speak("Yeah?"); q.clear(); continue

                    process(text, lang)
                    q.clear()
                    awake_until = time.time() + SESSION_SEC
            elif awake and now > awake_until:
                print("  [asleep]\n")

    except KeyboardInterrupt:
        print("\n  wrapping up...")
        eyes.stop()
        turns = [h for h in history if h["role"] in ("user", "assistant")]
        if len(turns) >= 4:
            convo = "\n".join(f"{'the user' if h['role']=='user' else 'Julius'}: {h['content']}"
                              for h in turns[-40:])
            known = "\n".join(f"- {f['text']}" for f in jm._load("facts.json")["facts"])
            kproj = "\n".join(f"- {p['name']}" for p in jm._load("projects.json")["projects"])
            prompt = f"""Extract ONLY what the user explicitly stated about himself or explicitly
committed to doing. Be extremely conservative. Most conversations yield nothing.

ALREADY KNOWN — never repeat or rephrase these:
FACTS:
{known or "(none)"}
PROJECTS:
{kproj or "(none)"}

NEVER record any of the following:
- anything about Julius failing, struggling, or not working
- anything about the user's mood, frustration, or tone
- any task that did not succeed
- any project invented from a question rather than stated by the user

A fact qualifies ONLY if the user stated it directly about himself, it will still be
true in a month, and it is not already known.

A project qualifies ONLY if the user explicitly said he is building it.

When in doubt, leave it out. Empty lists are the correct answer most of the time.

Return ONLY JSON:
{{"summary": "one or two sentences on what was discussed",
  "facts": [],
  "projects": []}}

CONVERSATION:
{convo}"""
            try:
                r = llm_chat(messages=[{"role": "user", "content": prompt}], think=False,
                             options={"temperature": 0.1, "num_predict": 600})
                raw = re.sub(r"<think>.*?</think>", "", r.message.content or "",
                             flags=re.DOTALL)
                mt = re.search(r"\{.*\}", raw, re.DOTALL)
                data = _json.loads(mt.group()) if mt else {}
                staged = ([{"kind": "fact", "text": f} for f in data.get("facts", [])] +
                          [{**p, "kind": "project"} for p in data.get("projects", [])])
                if staged: jm.stage(staged)
                if data.get("summary"): jm.set_last_session(data["summary"])
                print(f"  staged {len(staged)} item(s)")
            except Exception as e:
                print(f"  extraction failed ({type(e).__name__}) — transcript saved")
        print("  goodbye")
        if jm.staged():
            try:
                import subprocess, sys
                subprocess.run([sys.executable, "julius_review.py"])
            except Exception:
                print("  run: python julius_review.py")