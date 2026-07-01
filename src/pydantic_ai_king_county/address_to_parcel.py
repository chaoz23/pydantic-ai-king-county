"""King County address → parcel number tool for pydantic-ai agents."""

from __future__ import annotations

import functools
import json
import re
import urllib.parse
import urllib.request
from dataclasses import KW_ONLY, dataclass
from typing import Literal

import anyio.to_thread
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import Tool
from typing_extensions import Any, TypedDict

__all__ = ('ParcelCandidate', 'ParcelResult', 'AddressToParcelTool', 'address_to_parcel_tool')

_GEOCODER_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/KingCo_ParcelAddress_locator/GeocodeServer/findAddressCandidates"
)
_UA = "Mozilla/5.0"


class ParcelCandidate(TypedDict):
    """A candidate parcel match when the geocoder is ambiguous."""

    address: str
    """Geocoder's canonical address string."""
    parcel_number: str
    """10-digit King County parcel number."""
    score: float
    """Match confidence 0–100."""


class ParcelResult(TypedDict):
    """Result of an address-to-parcel lookup."""

    action: Literal["use", "pick", "refine", "reject"]
    """
    use    — parcel_number is valid, consume it directly.
    pick   — multiple candidates; present to user or pick highest score.
    refine — no match; try a different address.
    reject — bad input (wrong county, no house number); do not retry.
    """
    parcel_number: str | None
    """10-digit King County parcel number. Present when action is 'use' or 'pick'."""
    matched_address: str | None
    """Geocoder's canonical form of the matched address."""
    score: float | None
    """Match confidence 0–100. Scores ≥90 are reliable."""
    message: str
    """Human-readable explanation."""
    candidates: list[ParcelCandidate]
    """Ranked alternatives when action is 'pick'."""


def _lookup_sync(address: str, max_locations: int) -> ParcelResult:
    address = address.strip()
    if not address:
        return ParcelResult(action="reject", parcel_number=None, matched_address=None,
                            score=None, message="Address is required.", candidates=[])

    if re.fullmatch(r"\d{10}", address):
        return ParcelResult(action="use", parcel_number=address, matched_address=None,
                            score=100.0, message=f"Input is already a parcel number: {address}", candidates=[])

    params = urllib.parse.urlencode({
        "SingleLine": address, "outFields": "*",
        "maxLocations": max_locations, "f": "json",
    })
    req = urllib.request.Request(f"{_GEOCODER_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise ModelRetry(f"King County geocoder unavailable: {e}") from e

    candidates = data.get("candidates", [])
    if not candidates:
        return ParcelResult(action="refine", parcel_number=None, matched_address=None,
                            score=None, message=f"No match found for '{address}'.", candidates=[])

    best = candidates[0]
    score = float(best.get("score", 0))
    attrs = best.get("attributes", {})
    pin = attrs.get("PIN", "")
    matched = attrs.get("Match_addr", "")

    if score >= 90 and pin and len(pin) == 10:
        return ParcelResult(action="use", parcel_number=pin, matched_address=matched,
                            score=score, message=f"Matched: {matched} → parcel {pin}", candidates=[])

    alts: list[ParcelCandidate] = [
        ParcelCandidate(address=c.get("attributes", {}).get("Match_addr", ""),
                        parcel_number=c.get("attributes", {}).get("PIN", ""),
                        score=float(c.get("score", 0)))
        for c in candidates
        if len(c.get("attributes", {}).get("PIN", "")) == 10 and float(c.get("score", 0)) >= 50
    ]

    if alts:
        return ParcelResult(action="pick", parcel_number=alts[0]["parcel_number"],
                            matched_address=alts[0]["address"], score=alts[0]["score"],
                            message=f"Low confidence. Best guess: {alts[0]['address']}. Verify this is correct.",
                            candidates=alts)

    return ParcelResult(action="refine", parcel_number=None, matched_address=None,
                        score=None, message=f"Could not resolve '{address}' to a parcel.", candidates=[])


@dataclass
class AddressToParcelTool:
    """Converts a King County, WA street address to its 10-digit parcel number (PIN)."""

    _: KW_ONLY

    max_locations: int = 5
    """Maximum geocoder candidates to consider (1–10)."""

    async def __call__(self, address: str) -> ParcelResult:
        """Convert a King County, WA street address to its 10-digit parcel number (PIN).

        Uses the King County ArcGIS geocoder. No API key required.

        Args:
            address: Street address in King County, WA. Include house number and city.
                     Also accepts a bare 10-digit parcel number as pass-through.
                     Examples: "1817 Morris Ave S, Renton WA", "600 Grady Way, Renton", "7222000353"

        Returns:
            A ParcelResult with action, parcel_number, matched_address, score, and candidates.
            Branch on action: use → consume parcel_number; pick → show candidates;
            refine → no match, try different input; reject → bad input.
        """
        return await anyio.to_thread.run_sync(
            functools.partial(_lookup_sync, address, self.max_locations)
        )


def address_to_parcel_tool(*, max_locations: int = 5) -> Tool[Any]:
    """Create an address-to-parcel-number tool for King County, WA.

    Uses the King County ArcGIS geocoder. No API key required.

    Args:
        max_locations: Maximum geocoder candidates to return (1–10). Defaults to 5.
    """
    return Tool[Any](
        AddressToParcelTool(max_locations=max_locations).__call__,
        name="king_county_address_to_parcel",
        description=(
            "Convert a King County, WA street address to its 10-digit parcel number (PIN). "
            "Use this before any tool that requires a parcel number. "
            "Also accepts a bare 10-digit parcel number as pass-through."
        ),
    )
