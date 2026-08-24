"""Julius memory: sessions, durable facts, project state, staging + review."""
import json, uuid
from pathlib import Path
from datetime import datetime, date

MEM  = Path.home()/"Documents/fer2013-emotion-cnn/julius_memory"
SESS = MEM/"sessions"

def _init():
    SESS.mkdir(parents=True, exist_ok=True)
    for f, d in [("facts.json", {"facts": []}), ("staging.json", {"items": []}),
                 ("projects.json", {"projects": []}), ("last_session.json", {})]:
        p = MEM/f
        if not p.exists(): p.write_text(json.dumps(d, indent=2))

def _load(name):  _init(); return json.loads((MEM/name).read_text())
def _save(name, d): _init(); (MEM/name).write_text(json.dumps(d, indent=2))

# ---------- session transcript ----------
def log_turn(user, assistant, meta=None):
    _init()
    rec = {"t": datetime.now().isoformat(timespec="seconds"),
           "user": user, "julius": assistant}
    if meta: rec["meta"] = meta
    with open(SESS/f"{date.today().isoformat()}.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")

def recent_turns(n=6):
    _init(); rows = []
    for f in reversed(sorted(SESS.glob("*.jsonl"))):
        for line in reversed(f.read_text().splitlines()):
            if line.strip():
                rows.append(json.loads(line))
                if len(rows) >= n: return list(reversed(rows))
    return list(reversed(rows))

# ---------- context injection ----------
def build_context():
    facts = _load("facts.json")["facts"]
    projs = _load("projects.json")["projects"]
    last  = _load("last_session.json")
    out = []
    if facts:
        out.append("WHAT YOU REMEMBER ABOUT THE USER")
        out += [f"- {f['text']}" for f in facts]
    active = [p for p in projs if p.get("status") != "done"]
    if active:
        out.append("\nPROJECTS IN FLIGHT")
        for p in active:
            line = f"- {p['name']}: {p.get('state','')}"
            if p.get("next"): line += f" Next: {p['next']}"
            out.append(line)
    if last.get("summary"):
        out.append(f"\nLAST SESSION ({last.get('date','?')})\n{last['summary']}")
    return "\n".join(out)

# ---------- staging ----------
def stage(items, session_date=None):
    s = _load("staging.json")
    for it in items:
        it.setdefault("id", uuid.uuid4().hex[:8])
        it.setdefault("added", session_date or date.today().isoformat())
        s["items"].append(it)
    _save("staging.json", s); return len(items)

def staged(): return _load("staging.json")["items"]

def promote(item_id):
    s = _load("staging.json")
    moved = next((it for it in s["items"] if it["id"] == item_id), None)
    if not moved: return False
    s["items"] = [it for it in s["items"] if it["id"] != item_id]
    _save("staging.json", s)
    if moved.get("kind") == "project":
        p = _load("projects.json")
        ex = next((x for x in p["projects"]
                   if x["name"].lower() == moved["name"].lower()), None)
        if ex:
            ex.update({k: v for k, v in moved.items() if k in ("state","next","status")})
            ex["updated"] = moved["added"]
        else:
            p["projects"].append({"name": moved["name"], "state": moved.get("state",""),
                                  "next": moved.get("next",""), "status": "active",
                                  "updated": moved["added"]})
        _save("projects.json", p)
    else:
        f = _load("facts.json")
        f["facts"].append({"id": moved["id"], "text": moved["text"],
                           "added": moved["added"]})
        _save("facts.json", f)
    return True

def reject(item_id):
    s = _load("staging.json"); n = len(s["items"])
    s["items"] = [it for it in s["items"] if it["id"] != item_id]
    _save("staging.json", s); return len(s["items"]) < n

def set_last_session(summary):
    _save("last_session.json", {"date": date.today().isoformat(), "summary": summary})