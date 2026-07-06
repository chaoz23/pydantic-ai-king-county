"""Offline unit tests for contractor_license.py.

Pure helpers (_detect_input_type, _normalize_status) are tested directly.
_lookup_sync is exercised with a fake urllib opener so there are no network
calls: the opener returns three empty ASP.NET warmup responses followed by a
canned Controller.aspx/Search JSON body, letting us assert the full decision
table (reject / ModelRetry / none / found / pick).

Requires Python 3.10+ and pydantic-ai (module imports it at top level).
"""

import io
import json

import pytest

from pydantic_ai_king_county import contractor_license as mod


# --- pure helpers -----------------------------------------------------------

@pytest.mark.parametrize("query,cat,text", [
    ("603 011 456", "Ubi", "603011456"),      # 9 digits, separators stripped
    ("603011456", "Ubi", "603011456"),
    ("BOBSPL123", "LicenseId", "BOBSPL123"),   # 6-15 [A-Z0-9*], uppercased
    ("bobspl*99", "LicenseId", "BOBSPL*99"),   # wildcard allowed, lowercased in
    ("Bob's Plumbing", "Name", "Bob's Plumbing"),  # space/apostrophe -> Name
    ("AB", "Name", "AB"),                      # too short for LicenseId regex
])
def test_detect_input_type(query, cat, text):
    assert mod._detect_input_type(query) == (cat, text)


@pytest.mark.parametrize("row,expected", [
    ({"IrlStatusCode": "A"}, "Active"),
    ({"Status": "View Details"}, "Active"),
    ({"IrlStatusCode": "E"}, "Expired"),
    ({"IrlStatusCode": "X"}, "Expired"),
    ({"Status": "Inactive"}, "Inactive"),
    ({"Status": "Suspended"}, "Suspended"),   # passthrough
    ({}, "Unknown"),                          # nothing -> Unknown
])
def test_normalize_status(row, expected):
    assert mod._normalize_status(row) == expected


# --- _lookup_sync via fake opener ------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class _FakeOpener:
    """Returns queued byte payloads, one per .open() call."""
    def __init__(self, payloads): self._q = list(payloads)
    def open(self, req, timeout=None): return _Resp(self._q.pop(0))


def _install_opener(monkeypatch, search_payload, warmups=3):
    payloads = [b""] * warmups + [json.dumps({"d": search_payload}).encode()]
    monkeypatch.setattr(mod.urllib.request, "build_opener",
                        lambda *a, **k: _FakeOpener(payloads))


def _row(**kw):
    base = {"LicenseId": "BOBSPL123", "BusinessName": "BOB'S PLUMBING",
            "ContractorType": "General", "ContractorGroup": "01",
            "City": "Renton", "State": "WA", "Ubi": "603011456",
            "IrlStatusCode": "A"}
    base.update(kw)
    return base


def test_short_query_rejects():
    r = mod._lookup_sync("x", 25)
    assert r["action"] == "reject"


def test_warmup_transport_error_raises_model_retry(monkeypatch):
    class _Boom:
        def open(self, req, timeout=None): raise OSError("dns fail")
    monkeypatch.setattr(mod.urllib.request, "build_opener", lambda *a, **k: _Boom())
    with pytest.raises(mod.ModelRetry):
        mod._lookup_sync("BOBSPL123", 25)


def test_no_results_returns_none(monkeypatch):
    _install_opener(monkeypatch, {"TotalCount": 0, "SearchResult": []})
    r = mod._lookup_sync("BOBSPL123", 25)
    assert r["action"] == "none"
    assert r["total_found"] == 0


def test_single_license_match_is_found(monkeypatch):
    _install_opener(monkeypatch, {"TotalCount": 1, "SearchResult": [
        _row(HasSafetyViolation=True, HasContractorViolation=False)]})
    r = mod._lookup_sync("BOBSPL123", 25)   # LicenseId + total 1 -> found
    assert r["action"] == "found"
    rec = r["results"][0]
    assert rec["status"] == "Active"
    assert rec["violations"] == ["safety"]
    assert rec["license_id"] == "BOBSPL123"
    assert "Detail.aspx" in rec["detail_url"]


def test_name_multiple_matches_is_pick(monkeypatch):
    _install_opener(monkeypatch, {"TotalCount": 2, "SearchResult": [
        _row(BusinessName="BOB'S PLUMBING LLC"),
        _row(BusinessName="BOBBY PLUMBING")]})
    r = mod._lookup_sync("plumbing", 25)    # Name, no single exact -> pick
    assert r["action"] == "pick"
    assert r["total_found"] == 2
