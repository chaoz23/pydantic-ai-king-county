"""King County, WA civic data tools for pydantic-ai agents.

No API keys required. All data from public King County and WA State sources.

Usage::

    from pydantic_ai import Agent
    from pydantic_ai_king_county import address_to_parcel_tool, contractor_license_tool

    agent = Agent(
        "anthropic:claude-sonnet-4-5",
        toolsets=[address_to_parcel_tool(), contractor_license_tool()],
    )

Tools
-----
address_to_parcel_tool()
    Street address → 10-digit King County parcel number (PIN).
    Source: King County ArcGIS geocoder.

contractor_license_tool()
    Verify WA contractor license status, type, and violations.
    Source: WA L&I Verify portal (secure.lni.wa.gov/verify/).
"""

from .address_to_parcel import (
    AddressToParcelTool,
    ParcelCandidate,
    ParcelResult,
    address_to_parcel_tool,
)
from .contractor_license import (
    ContractorLicenseTool,
    ContractorRecord,
    ContractorResult,
    contractor_license_tool,
)

__all__ = (
    # Address → parcel
    "address_to_parcel_tool",
    "AddressToParcelTool",
    "ParcelResult",
    "ParcelCandidate",
    # Contractor license
    "contractor_license_tool",
    "ContractorLicenseTool",
    "ContractorResult",
    "ContractorRecord",
)
