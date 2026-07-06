"""Offline unit tests for _lookup_sync's branch logic.

The King County ArcGIS geocoder is stubbed via urllib.request.urlopen, so
these tests make no network calls and assert the full decision table:
empty → reject, bare PIN → use, high-confidence match → use, ambiguous →
pick, no/low match → refine, transport error → ModelRetry.

Requires Python 3.10+ and pydantic-ai (the module imports it at top level).
Run: pytest tests/test_address_to_parcel.py
"""

import io
import json
from contextlib import contextmanager

import pytest

from pydantic_ai_king_county import address_to_parcel as mod


@contextmanager
def _fake_urlopen_factory(payload):
    """Return a urlopen replacement that yields `payload` (dict) as JSON bytes."""
    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()
    def _urlopen(req, timeout=None):
        return _Resp(json.dumps(payload).encode())
    yield _urlopen


def _run(monkeypatch, payload, address="123 Main St, Seattle WA", max_locations=5):
    with _fake_urlopen_factory(payload) as fake:
        monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
        return mod._lookup_sync(address, max_locations)


def _cand(score, pin, addr="600 Grady Way, Renton, WA"):
    return {"score": score, "attributes": {"PIN": pin, "Match_addr": addr}}


def test_empty_address_rejects(monkeypatch):
    r = mod._lookup_sync("   ", 5)
    assert r["action"] == "reject"
    assert r["parcel_number"] is None


def test_bare_parcel_number_is_used_directly(monkeypatch):
    r = mod._lookup_sync("7222000353", 5)
    assert r["action"] == "use"
    assert r["parcel_number"] == "7222000353"
    assert r["score"] == 100.0


def test_high_confidence_match_uses_pin(monkeypatch):
    r = _run(monkeypatch, {"candidates": [_cand(97.5, "1234567890")]})
    assert r["action"] == "use"
    assert r["parcel_number"] == "1234567890"
    assert r["score"] == 97.5


def test_no_candidates_refines(monkeypatch):
    r = _run(monkeypatch, {"candidates": []})
    assert r["action"] == "refine"
    assert r["parcel_number"] is None


def test_low_confidence_offers_pick(monkeypatch):
    r = _run(monkeypatch, {"candidates": [_cand(72.0, "1111111111"), _cand(60.0, "2222222222")]})
    assert r["action"] == "pick"
    assert r["parcel_number"] == "1111111111"
    assert len(r["candidates"]) == 2


def test_high_score_but_bad_pin_length_falls_through(monkeypatch):
    # score >= 90 but PIN is not 10 digits -> not a direct 'use'
    r = _run(monkeypatch, {"candidates": [_cand(95.0, "123")]})
    assert r["action"] in ("pick", "refine")
    assert r["action"] != "use"


def test_candidates_below_threshold_refine(monkeypatch):
    # all alts below score 50 -> refine, none picked
    r = _run(monkeypatch, {"candidates": [_cand(40.0, "1111111111")]})
    assert r["action"] == "refine"


def test_transport_error_raises_model_retry(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection reset")
    monkeypatch.setattr(mod.urllib.request, "urlopen", _boom)
    with pytest.raises(mod.ModelRetry):
        mod._lookup_sync("123 Main St", 5)
