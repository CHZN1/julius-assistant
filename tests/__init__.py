"""Test package setup, run before any test module is imported.

Every test runs isolated from a Julius that may be running on the same Mac:
- Julius's state, memory, conversation list, cost ledger and local config point at a throwaway folder, so a test can never
  read or write ~/.julius, julius_memory or julius.local.toml (and local runs see the same config as CI).
- Binding one of the ports a running Julius listens on (the event feed, the UI channel and the panel file server) is
  refused, so every test server must ask for port 0 and take whatever free port it gets."""

import atexit
import os
import shutil
import socket
import tempfile

# Temp names drawn from hex only. The music deny-list refuses whole words such as "ni" (Native Instruments) anywhere in a
# path, and Python's default random names (tmp3ni8q...) sometimes contain one, which failed a music test at random.
tempfile._RandomNameSequence.characters = "0123456789abcdef"
ROOT = tempfile.mkdtemp(prefix="julius-tests-")
atexit.register(shutil.rmtree, ROOT, True)
os.makedirs(os.path.join(ROOT, "memory"), exist_ok=True)

ISOLATED = {
    "JULIUS_STATE_DIR": os.path.join(ROOT, "state"),
    "JULIUS_MEMORY_DIR": os.path.join(ROOT, "memory"),
    "JULIUS_CONVERSATIONS_DB": os.path.join(ROOT, "conversations.sqlite3"),
    "JULIUS_COSTS_FILE": os.path.join(ROOT, "memory", "costs.jsonl"),
    "JULIUS_LOCAL_CONFIG": os.path.join(ROOT, "no-local-config.toml"),
    "JULIUS_NO_KEYCHAIN": "1",  # never read the real Keychain (julius/keys.py)
}
os.environ.update(
    ISOLATED
)  # overrides, not defaults: a value from your shell must not leak your real folders in

SERVICE_PORTS = frozenset(
    {8765, 8775, 8785}
)  # [events] port, [ui] port and panel_port in julius.toml
_bind = socket.socket.bind


def _guarded_bind(self, address):
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    if port in SERVICE_PORTS:
        raise OSError(
            f"tests must not bind port {port}: a running Julius uses it. Use port=0 for an ephemeral port."
        )
    return _bind(self, address)


socket.socket.bind = _guarded_bind
