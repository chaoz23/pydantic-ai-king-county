"""WA L&I contractor license verification tool for pydantic-ai agents."""

from __future__ import annotations

import functools
import http.cookiejar
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

__all__ = ('ContractorRecord', 'ContractorResult', 'ContractorLicenseTool', 'contractor_license_tool')

_VERIFY_BASE = "https://secure.lni.wa.gov/verify"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class ContractorRecord(TypedDict):
    """A single contractor license record from WA L&I."""

    license_id: str
    """WA L&I license number (e.g. 'MORTESL763NR')."""
    business_name: str
    """Registered business or person name."""
    contractor_type: str
    """Specific license type (e.g. 'Construction Contractor', 'Electrician')."""
    contractor_group: str
    """License group (e.g. 'Construction Contractor', 'Electrical')."""
    status: str
    """License status: 'Active', 'Expired', or 'Inactive'."""
    city: str
    """City of record."""
    state: str
    """State of record."""
    ubi: str | None
    """WA Unified Business Identifier (9 digits), if available."""
    violations: list[str]
    """Violation types on record. May include 'safety' and/or 'contractor'."""
    detail_url: str
    """URL to full license detail on WA L&I."""


class ContractorResult(TypedDict):
    """Result of a WA contractor license lookup."""

    action: Literal["found", "pick", "none", "reject"]
    """
    found  — license verified; results contains the match(es).
    pick   — multiple matches; narrow the query or use a license ID.
    none   — no WA contractor found for this query.
    reject — bad input; do not retry without changing the query.
    """
    total_found: int
    """Total matching records in the L&I database (may exceed len(results))."""
    results: list[ContractorRecord]
    """Up to 25 matching contractor records."""
    message: str
    """Human-readable summary."""


def _detect_input_type(query: str) -> tuple[str, str]:
    digits_only = re.sub(r"[\s\-]", "", query)
    if re.fullmatch(r"\d{9}", digits_only):
        return "Ubi", digits_only
    if re.fullmatch(r"[A-Z0-9*]{6,15}", query.upper()):
        return "LicenseId", query.upper()
    return "Name", query


def _normalize_status(row: dict[str, Any]) -> str:
    code = row.get("IrlStatusCode", "") or ""
    status = row.get("Status", "") or ""
    if code == "A" or status == "View Details":
        return "Active"
    if code in ("E", "X"):
        return "Expired"
    return "Inactive" if status.lower() == "inactive" else status or "Unknown"


def _lookup_sync(query: str, page_size: int) -> ContractorResult:
    query = query.strip()
    if len(query) < 2:
        return ContractorResult(action="reject", total_found=0, results=[],
                                message="Query must be at least 2 characters.")

    search_cat, search_text = _detect_input_type(query)

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": _UA}

    # Establish ASP.NET session (required — searches return 0 without it)
    warmup_url = f"{_VERIFY_BASE}/Results.aspx#init"
    for url, body in [
        (f"{_VERIFY_BASE}/default.aspx", None),
        (warmup_url, None),
        (f"{_VERIFY_BASE}/SessionHandler.aspx", json.dumps({"hash": warmup_url}).encode()),
    ]:
        req_headers = dict(headers)
        if body:
            req_headers.update({"Content-Type": "application/json; charset=UTF-8",
                                 "X-Requested-With": "XMLHttpRequest"})
        req = urllib.request.Request(url, data=body, headers=req_headers)
        try:
            with opener.open(req, timeout=15) as r:
                r.read()
        except Exception as e:
            raise ModelRetry(f"Could not reach WA L&I: {e}") from e

    search_dto = {
        "pageNumber": 0, "SearchType": 2, "SortColumn": "Rank", "SortOrder": "desc",
        "pageSize": page_size, "ContractorTypeFilter": [], "SessionID": "", "SAW": "",
        "searchCat": search_cat, "searchText": search_text,
        search_cat: search_text,  # required: named field must match searchCat
        "firstSearch": 1,
    }
    results_url = f"{_VERIFY_BASE}/Results.aspx#{urllib.parse.quote(json.dumps(search_dto))}"
    req = urllib.request.Request(
        f"{_VERIFY_BASE}/Controller.aspx/Search",
        data=json.dumps({"dtoSrch": search_dto}).encode(),
        headers={**headers, "Content-Type": "application/json; charset=UTF-8",
                 "X-Requested-With": "XMLHttpRequest", "Referer": results_url,
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
    )
    try:
        with opener.open(req, timeout=15) as r:
            data = json.loads(r.read())["d"]
    except Exception as e:
        raise ModelRetry(f"WA L&I search failed: {e}") from e

    total = data.get("TotalCount", 0)
    if total == 0:
        return ContractorResult(action="none", total_found=0, results=[],
                                message=f"No WA contractor/license found for '{query}'.")

    records: list[ContractorRecord] = [
        ContractorRecord(
            license_id=r.get("LicenseId", "") or "",
            business_name=r.get("BusinessName", "") or "",
            contractor_type=r.get("ContractorType", "") or "",
            contractor_group=r.get("ContractorGroup", "") or "",
            status=_normalize_status(r),
            city=r.get("City", "") or "",
            state=r.get("State", "") or "",
            ubi=r.get("Ubi", "") or None,
            violations=(["safety"] if r.get("HasSafetyViolation") else []) +
                       (["contractor"] if r.get("HasContractorViolation") else []),
            detail_url=(
                f"https://secure.lni.wa.gov/verify/Detail.aspx"
                f"?LicenseType={urllib.parse.quote(str(r.get('ContractorGroup') or ''))}"
                f"&LicenseNumber={urllib.parse.quote(str(r.get('LicenseId') or ''))}"
            ),
        )
        for r in data.get("SearchResult", [])
    ]

    if search_cat in ("LicenseId", "Ubi") and total == 1:
        rec = records[0]
        return ContractorResult(action="found", total_found=total, results=records,
                                message=f"{rec['business_name']} — {rec['contractor_type']} — {rec['status']}")

    if search_cat == "Name":
        exact = [rec for rec in records if rec["business_name"].upper() == query.upper()]
        if len(exact) == 1:
            rec = exact[0]
            return ContractorResult(action="found", total_found=total, results=exact,
                                    message=f"{rec['business_name']} — {rec['contractor_type']} — {rec['status']}")

    return ContractorResult(
        action="pick", total_found=total, results=records,
        message=f"Found {total} matches for '{query}'. Showing {len(records)}. Use a license ID for an exact match.",
    )


@dataclass
class ContractorLicenseTool:
    """Verifies Washington State contractor license status via WA L&I."""

    _: KW_ONLY

    page_size: int = 25
    """Maximum results to return per search (1–25)."""

    async def __call__(self, query: str) -> ContractorResult:
        """Verify a Washington State contractor's license status via WA L&I Verify.

        Checks contractor registration, license type, workers' comp status, and any
        safety or contractor violations on record. No API key required.

        Args:
            query: Business name, license ID, or 9-digit UBI number.
                   Examples: "Acme Plumbing", "MORTESL763NR", "605417027"
                   Tip: use a license ID for an exact match; name searches may return many results.

        Returns:
            A ContractorResult with action, total_found, results, and message.
            Branch on action: found → license verified; pick → multiple matches,
            narrow the query; none → not in L&I database; reject → bad input.
        """
        return await anyio.to_thread.run_sync(
            functools.partial(_lookup_sync, query, self.page_size)
        )


def contractor_license_tool(*, page_size: int = 25) -> Tool[Any]:
    """Create a WA contractor license verification tool.

    Searches the WA L&I Verify portal. No API key required.
    Covers all WA-licensed contractor types: construction, electrical,
    plumbing, HVAC, roofing, and more.

    Args:
        page_size: Maximum results per search (1–25). Defaults to 25.
    """
    return Tool[Any](
        ContractorLicenseTool(page_size=page_size).__call__,
        name="wa_contractor_license",
        description=(
            "Verify a Washington State contractor's license status, type, and violation history "
            "via WA L&I. Accepts a business name, license ID (e.g. 'MORTESL763NR'), or 9-digit UBI. "
            "Use to confirm a contractor is licensed before hiring them for work in Washington State."
        ),
    )
