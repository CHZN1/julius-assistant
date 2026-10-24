"""Read-only brain benchmark using Julius's real prompt + settings (num_ctx 4096, keep_alive 5m)."""
import sys, time, csv, json, subprocess, re, os
sys.path.insert(0, os.path.expanduser('~/Documents/fer2013-emotion-cnn'))
from julius_conversation import conversation_messages
import julius_tools as jt
import ollama

PROMPTS = [
 ("chitchat", "Hey Julius, how's it going?"),
 ("factual", "What's the difference between overfitting and underfitting?"),
 ("multistep", "Give me a three step plan to learn guitar chords this week, then tell me which step is hardest."),
 ("followup", None),  # uses history
 ("tool_math", "What is 17.5 percent of 2480?"),
 ("tool_weather", "What's the weather like in Springfield right now?"),
 ("self_id", "Can you see me right now, or are you just listening?"),
]
HIST = [{"role":"user","content":"I'm building a CNN for emotion detection on FER2013 and it keeps getting 62 percent validation accuracy while training hits 95."},
        {"role":"assistant","content":"That gap is classic overfitting. Try stronger augmentation, dropout, and early stopping."}]
FOLLOW = "Okay, which of those would you try first, and why?"

def free_pct():
    o = subprocess.check_output(['memory_pressure','-Q'],text=True)
    return int(re.search(r'free percentage: (\d+)%',o).group(1))
def ps_size(model):
    for m in ollama.ps().models:
        if m.model == model: return round(m.size/1e9,2), round((m.size_vram or 0)/1e9,2)
    return None, None

def run(model, label):
    rows=[]
    ollama.generate(model=model, prompt="", keep_alive=0); time.sleep(2)
    for i,(kind,text) in enumerate(PROMPTS):
        if free_pct() < 25: print("STOP low memory", free_pct()); break
        msgs = (HIST+[{"role":"user","content":FOLLOW}]) if kind=="followup" else [{"role":"user","content":text}]
        msgs = conversation_messages(msgs)
        tools = jt.SCHEMAS if kind.startswith("tool") else None
        t0=time.perf_counter(); ttft=None; out=""; last=None
        for part in ollama.chat(model=model, messages=msgs, tools=tools, think=False, stream=True,
                                keep_alive="5m", options={"num_ctx":4096}):
            if ttft is None and (part.message.content or part.message.tool_calls): ttft=time.perf_counter()-t0
            out += part.message.content or ""
            if part.message.tool_calls: out += " [TOOL_CALL "+",".join(f"{c.function.name}({dict(c.function.arguments)})" for c in part.message.tool_calls)+"]"
            last=part
        tot=time.perf_counter()-t0
        ev=last.eval_count or 0; evd=(last.eval_duration or 1)/1e9
        ram,vram=ps_size(model)
        rows.append(dict(model=label,idx=i,kind=kind,cold=(i==0),load_s=round((last.load_duration or 0)/1e9,3),
            prompt_tokens=last.prompt_eval_count,prompt_eval_s=round((last.prompt_eval_duration or 0)/1e9,3),
            ttft_s=round(ttft or tot,3),gen_tokens=ev,tok_per_s=round(ev/evd,1) if ev else 0,total_s=round(tot,3),
            resident_gb=ram,gpu_gb=vram,free_pct=free_pct(),answer=out.strip().replace("\n"," ")[:400]))
        print(label,kind,rows[-1]['ttft_s'],rows[-1]['tok_per_s'],rows[-1]['total_s'],flush=True)
    # unload check
    ollama.generate(model=model, prompt="", keep_alive=0); time.sleep(1.5)
    print(label,"resident after keep_alive=0:",ps_size(model)); return rows

if __name__=="__main__":
    allrows=[]
    for m,l in [("llama3.2:3b","llama3.2:3b (default)"),("qwen3:4b-instruct-2507-q4_K_M","qwen3-4b-instruct q4km"),("qwen3:8b","qwen3:8b (balanced)")]:
        allrows+=run(m,l)
    with open(os.path.expanduser('~/Documents/fer2013-emotion-cnn/diagnostics/benchmarks.csv'),'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(allrows[0].keys())); w.writeheader(); w.writerows(allrows)
