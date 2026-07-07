"""Config-first mapping + the reconciliation gate.

A mapping pins WHICH series carries each business role — deterministic where
label heuristics are probabilistic. Before any mapping is trusted the engine
re-derives the identities the file itself carries (margin = revenue − opex)
and rejects mappings that contradict the data. Verified mappings are
inherited by later uploads that carry the same series.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# labels chosen so NO role heuristic fires: without a mapping there is no
# model at all — the mapping is what lights the sockets
HEAD = "Year,Encaissements_USD,Sorties_USD,Resultat_USD,Remarque_N"


def _csv(scale: float = 1.0) -> bytes:
    rows = [HEAD]
    for i, (rev, opx) in enumerate([(100000, 60000), (220000, 90000),
                                    (400000, 130000), (750000, 180000)]):
        r, o = rev * scale, opx * scale
        rows.append(f"{2025 + i},{r:.0f},{o:.0f},{r - o:.0f},{i + 1}")
    return ("\n".join(rows) + "\n").encode()


def _analyze(content: bytes, name: str) -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "text/csv")}).json()


GOOD = {"revenue": {"label": "Encaissements_USD"},
        "opex": {"label": "Sorties_USD"},
        "margin": {"label": "Resultat_USD"}}


def test_unmapped_file_has_no_model():
    rep = _analyze(_csv(), "tresorerie_a.csv")
    assert rep["model"] is None          # heuristics honestly find nothing


def test_manual_mapping_lights_the_model_when_it_reconciles():
    rep = _analyze(_csv(), "tresorerie_a.csv")
    r = client.post(f"/analyses/{rep['id']}/mapping",
                    json={"mapping": GOOD})
    assert r.status_code == 200
    out = r.json()
    assert out["reconciliation"]["ok"] is True
    names = [c["name"] for c in out["reconciliation"]["checks"]]
    assert "margin_identity" in names
    m = out["model"]
    assert m["metrics"]["revenue"]["label"] == "Encaissements_USD"
    assert m["metrics"]["opex"]["label"] == "Sorties_USD"
    assert m["derived"]["margin"][-1] == 750000 - 180000
    assert m["scenario_ready"] is True

    # persisted: the stored report now carries model + mapping provenance
    stored = client.get(f"/analyses/{rep['id']}").json()
    assert stored["model"] is not None
    assert stored["mapping"]["provenance"]["kind"] == "manual"

    # and the mapping endpoint reports it back with the available series
    g = client.get(f"/analyses/{rep['id']}/mapping").json()
    assert g["mapping"]["roles"]["revenue"]["label"] == "Encaissements_USD"
    assert any(a["label"] == "Resultat_USD" for a in g["available"])


def test_contradicted_mapping_is_rejected():
    rep = _analyze(_csv(), "tresorerie_b_bad.csv")
    swapped = {"revenue": {"label": "Sorties_USD"},
               "opex": {"label": "Encaissements_USD"},
               "margin": {"label": "Resultat_USD"}}
    r = client.post(f"/analyses/{rep['id']}/mapping",
                    json={"mapping": swapped})
    assert r.status_code == 422
    assert "margin_identity" in r.text
    # nothing was stored — the analysis still has no model
    stored = client.get(f"/analyses/{rep['id']}").json()
    assert stored["model"] is None
    assert stored.get("mapping") in (None, {})


def test_unknown_role_and_unresolvable_series_are_rejected():
    rep = _analyze(_csv(), "tresorerie_c.csv")
    r = client.post(f"/analyses/{rep['id']}/mapping",
                    json={"mapping": {"profits": {"label": "Resultat_USD"}}})
    assert r.status_code == 422
    r = client.post(f"/analyses/{rep['id']}/mapping",
                    json={"mapping": {"revenue": {"label": "Nope_USD"}}})
    assert r.status_code == 422


def test_verified_mapping_is_inherited_by_the_next_upload():
    rep_a = _analyze(_csv(), "tresorerie_avril.csv")
    ok = client.post(f"/analyses/{rep_a['id']}/mapping",
                     json={"mapping": GOOD})
    assert ok.status_code == 200

    # same client, next month's export: same series, new values, new file
    rep_b = _analyze(_csv(scale=1.3), "tresorerie_mai.csv")
    assert rep_b["model"] is not None
    assert rep_b["model"]["metrics"]["revenue"]["label"] == "Encaissements_USD"
    assert rep_b["mapping"]["provenance"]["kind"] == "inherited"
    assert rep_b["mapping"]["reconciliation"]["ok"] is True


def test_inheritance_survives_sheet_rename():
    """The UI pins sheet+label, but a CSV's sheet name IS its filename stem
    — next month's export must still inherit via the stable labels."""
    rep_a = _analyze(_csv(), "tresor_juin.csv")
    pinned = {role: {"sheet": "tresor_juin", **ref}
              for role, ref in GOOD.items()}
    ok = client.post(f"/analyses/{rep_a['id']}/mapping",
                     json={"mapping": pinned})
    assert ok.status_code == 200
    rep_b = _analyze(_csv(scale=0.8), "tresor_juillet.csv")
    assert rep_b["model"] is not None
    assert rep_b["mapping"]["provenance"]["kind"] == "inherited"


def test_mapping_survives_rerun():
    rep = _analyze(_csv(), "tresorerie_rerun.csv")
    client.post(f"/analyses/{rep['id']}/mapping", json={"mapping": GOOD})
    rr = client.post(f"/analyses/{rep['id']}/rerun").json()
    assert rr["model"] is not None
    assert rr["model"]["metrics"]["revenue"]["label"] == "Encaissements_USD"
    assert rr["mapping"]["provenance"]["kind"] == "manual"


def test_unverifiable_mapping_is_kept_manual_but_never_inherited():
    # no margin column -> no identity to check: the analyst may pin it
    # (ok is None, honest), but it must not silently spread to new uploads
    head = "Year,Encaissements_USD,Sorties_USD"
    body = "\n".join(f"{2025 + i},{v},{v // 2}"
                     for i, v in enumerate([100, 220, 400, 750]))
    content = (head + "\n" + body + "\n").encode()
    rep = _analyze(content, "sans_temoin.csv")
    r = client.post(f"/analyses/{rep['id']}/mapping",
                    json={"mapping": {"revenue": {"label": "Encaissements_USD"},
                                      "opex": {"label": "Sorties_USD"}}})
    assert r.status_code == 200
    assert r.json()["reconciliation"]["ok"] is None      # nothing checkable

    body2 = "\n".join(f"{2025 + i},{v},{v // 3}"
                      for i, v in enumerate([110, 230, 410, 760]))
    rep2 = _analyze((head + "\n" + body2 + "\n").encode(), "sans_temoin2.csv")
    assert rep2.get("mapping") is None                   # not inherited
