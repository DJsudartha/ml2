"""Focused source-replay and fail-closed supplement tests."""

from copy import deepcopy
import json
from pathlib import Path
import pytest
from verify import normalize, sha, verify

ROOT = Path(__file__).resolve().parent
PIN = "bbaa2e318b32b9e25c594f0bca7c72cedb1bece824321bc3a6441dc1939309a7"


def source():
    row = json.loads((ROOT / "scoped-source.json").read_bytes())["games"][0]
    return {
        "metadata": row["metadata"],
        "game": json.loads((ROOT / row["game_file"]).read_bytes()),
    }


def test_scoped_integrity():
    assert verify(ROOT, PIN)["games"] == 3


def test_receipt_pin():
    with pytest.raises(ValueError, match="receipt pin"):
        verify(ROOT, "0" * 64)


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "subgroup",
        "side",
        "missing_slot",
        "duplicate_ban",
        "date",
        "patch",
        "wrong_tournament",
    ],
)
def test_bad_source(change):
    s = source()
    g, m = s["game"], s["metadata"]
    if change == "identity":
        m["match2id"] = "PROTECTED"
    elif change == "subgroup":
        g["subgroup"] = "9"
    elif change == "side":
        g["extradata"]["team1side"] = g["extradata"]["team2side"]
    elif change == "missing_slot":
        del g["extradata"]["team1ban5"]
    elif change == "duplicate_ban":
        g["extradata"]["team1ban5"] = g["extradata"]["team1ban1"]
    elif change == "date":
        g["date"] = "2099-01-01"
    elif change == "patch":
        g["patch"] = "unverified"
    elif change == "wrong_tournament":
        m["pagename"] = "other"
    with pytest.raises((ValueError, KeyError)):
        normalize(s)


def test_roster_order_has_no_pick_chronology():
    s = source()
    altered = deepcopy(s)
    e = altered["game"]["extradata"]
    e["team1champion1"], e["team1champion2"] = e["team1champion2"], e["team1champion1"]
    assert normalize(s) == normalize(altered)


def test_ban_positions_preserved():
    s = source()
    altered = deepcopy(s)
    e = altered["game"]["extradata"]
    e["team1ban1"], e["team1ban4"] = e["team1ban4"], e["team1ban1"]
    assert normalize(s)["bans"] != normalize(altered)["bans"]


@pytest.mark.parametrize(
    "change",
    ["bytes", "normalized_ban", "normalized_roster", "candidate_sides", "approval"],
)
def test_tampered_artifacts(tmp_path, change):
    receipt = json.loads((ROOT / "receipt.json").read_bytes())
    for name in list(receipt["files"]) + ["receipt.json"]:
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    if change == "bytes":
        (tmp_path / "supplement.json").write_bytes(b"{}")
    else:
        path = tmp_path / "supplement.json"
        supplement = json.loads(path.read_bytes())
        row = supplement["games"][0]
        if change == "normalized_ban":
            row["bans"]["blue"][0]["hero"] = "Wrong"
        elif change == "normalized_roster":
            row["rosters"]["blue"][0] = "Wrong"
        elif change == "candidate_sides":
            row["candidate_baseline"]["teams"]["blue"] = "Wrong"
        elif change == "approval":
            supplement["training_approved"] = True
        path.write_text(json.dumps(supplement), encoding="utf-8")
        receipt["files"]["supplement.json"] = sha(path.read_bytes())
        (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError):
        verify(tmp_path)

def test_public_receipt_omits_private_source_references():
    receipt_bytes = (ROOT / "receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert "source_refs" not in receipt["prior_checks"]
    assert b"private/" not in receipt_bytes
    assert b"holdout/" not in receipt_bytes


@pytest.mark.parametrize(
    "home_path",
    [
        r"C:\Users\example\source.json",
        "C:/Users/example/source.json",
        "/home/example/source.json",
        "/Users/example/source.json",
    ],
)
def test_public_receipt_rejects_machine_specific_home_paths(tmp_path, home_path):
    receipt = json.loads((ROOT / "receipt.json").read_bytes())
    receipt["original_response"]["source_id"] = home_path
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="machine-specific home path"):
        verify(tmp_path)
