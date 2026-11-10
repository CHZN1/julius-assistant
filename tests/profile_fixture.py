"""Test helper: run a test with a personal [profile] (the shipped defaults are neutral)."""

import os
import tempfile
from pathlib import Path

PERSONAL = '[profile]\nname = "Alex"\nhome_city = "Springfield, Illinois"\nvocabulary = ["AcmeCorp", "Pat Example", "FER 2013", "Ethereum", "ETH", "Bitcoin"]\n[asr]\nnhl_teams = ["Sharks"]\nnfl_teams = ["Chiefs"]\n'


def personal(testcase, text=PERSONAL):
    d = tempfile.mkdtemp()
    p = Path(d) / "local.toml"
    p.write_text(text)
    old = os.environ.get("JULIUS_LOCAL_CONFIG")
    os.environ["JULIUS_LOCAL_CONFIG"] = str(p)

    def restore():
        if old is None:
            os.environ.pop("JULIUS_LOCAL_CONFIG", None)
        else:
            os.environ["JULIUS_LOCAL_CONFIG"] = old

    testcase.addCleanup(restore)
