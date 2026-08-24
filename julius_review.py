"""Review what Julius staged. Run after a session."""
import julius_memory as jm

items = jm.staged()
if not items:
    print("nothing staged."); raise SystemExit

print(f"{len(items)} item(s) staged — [k]eep, [d]iscard, [s]kip, [q]uit\n")
for it in list(items):
    if it.get("kind") == "project":
        print(f"  PROJECT: {it['name']}")
        print(f"    state: {it.get('state','')}")
        print(f"    next : {it.get('next','')}")
    else:
        print(f"  FACT: {it['text']}")
    while True:
        c = input("  > ").strip().lower()
        if c in ("k","d","s","q"): break
    if   c == "k": jm.promote(it["id"]); print("  kept\n")
    elif c == "d": jm.reject(it["id"]);  print("  discarded\n")
    elif c == "s": print("  skipped\n")
    else: break

print("\n--- current memory ---")
print(jm.build_context() or "(empty)")