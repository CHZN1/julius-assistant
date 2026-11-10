"""Review what Julius staged after a session: keep, discard or skip each item.

Run with `python -m julius.memory.review`.
"""

import julius.memory.sessions as jm


def describe(item: dict) -> None:
    """Print one staged item."""
    if item.get("kind") == "project":
        print(f"  PROJECT: {item['name']}")
        print(f"    state: {item.get('state', '')}")
        print(f"    next : {item.get('next', '')}")
    else:
        print(f"  FACT: {item['text']}")


def ask() -> str:
    """Prompt until the answer is k, d, s or q."""
    while True:
        choice = input("  > ").strip().lower()
        if choice in ("k", "d", "s", "q"):
            return choice


def main() -> None:
    """Walk through the staged items, then show the resulting memory."""
    items = jm.staged()
    if not items:
        print("nothing staged.")
        return
    print(f"{len(items)} item(s) staged — [k]eep, [d]iscard, [s]kip, [q]uit\n")
    for item in list(items):
        describe(item)
        choice = ask()
        if choice == "k":
            jm.promote(item["id"])
            print("  kept\n")
        elif choice == "d":
            jm.reject(item["id"])
            print("  discarded\n")
        elif choice == "s":
            print("  skipped\n")
        else:
            break
    print("\n--- current memory ---")
    print(jm.build_context() or "(empty)")


if __name__ == "__main__":
    main()
