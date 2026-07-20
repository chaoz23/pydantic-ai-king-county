# pydantic-ai-king-county

King County, WA civic data tools for [pydantic-ai](https://github.com/pydantic/pydantic-ai) agents. No API keys required.

```bash
pip install pydantic-ai-king-county
```

## Tools

| Factory | Source | What it does |
|---|---|---|
| `address_to_parcel_tool()` | King County ArcGIS geocoder | Street address → 10-digit parcel PIN |
| `contractor_license_tool()` | WA L&I Verify portal | Contractor license status + violations |

## Quick start

```python
from pydantic_ai import Agent
from pydantic_ai_king_county import address_to_parcel_tool, contractor_license_tool

agent = Agent(
    "anthropic:claude-sonnet-4-5",
    toolsets=[address_to_parcel_tool(), contractor_license_tool()],
)

result = agent.run_sync(
    "Is there an active contractor license for 'Acme Plumbing' in WA, "
    "and what is the parcel number for 1817 Morris Ave S, Renton WA?"
)
print(result.output)
```

## Tool reference

### `address_to_parcel_tool(*, max_locations=5)`

Converts a King County, WA street address to its 10-digit parcel number (PIN) using the King County ArcGIS geocoder.

**Input:** `address: str` — street address, city optional but recommended. Also accepts a bare 10-digit PIN as pass-through.

**Returns:** `ParcelResult` TypedDict:

| Field | Type | Description |
|---|---|---|
| `action` | `"use" \| "pick" \| "refine" \| "reject"` | `use` = consume parcel_number; `pick` = show candidates; `refine` = try different input; `reject` = bad input |
| `parcel_number` | `str \| None` | 10-digit PIN when `action` is `"use"` or `"pick"` |
| `matched_address` | `str \| None` | Geocoder's canonical address |
| `score` | `float \| None` | Match confidence 0–100. ≥90 is reliable |
| `candidates` | `list[ParcelCandidate]` | Ranked alternatives when `action` is `"pick"` |
| `message` | `str` | Human-readable explanation |

### `contractor_license_tool(*, page_size=25)`

Verifies Washington State contractor registration and license status via the [WA L&I Verify portal](https://secure.lni.wa.gov/verify/).

**Input:** `query: str` — business name, license ID (e.g. `MORTESL763NR`), or 9-digit UBI number.

**Returns:** `ContractorResult` TypedDict:

| Field | Type | Description |
|---|---|---|
| `action` | `"found" \| "pick" \| "none" \| "reject"` | `found` = license verified; `pick` = multiple matches; `none` = not in L&I; `reject` = bad input |
| `total_found` | `int` | Total records in L&I database (may exceed `len(results)`) |
| `results` | `list[ContractorRecord]` | Up to 25 matching records |
| `message` | `str` | Human-readable summary |

Each `ContractorRecord`: `license_id`, `business_name`, `contractor_type`, `contractor_group`, `status` (`Active`/`Expired`/`Inactive`), `city`, `state`, `ubi`, `violations` (list of `"safety"` / `"contractor"`), `detail_url`.

## Coverage

**Address to parcel:** All King County, WA addresses. Returns the 10-digit parcel number used by county systems.

**Contractor license:** All WA-licensed contractor types — construction contractors, electricians, plumbers, HVAC, roofers, and more.

## Related tools

Also available as standalone CLI tools with `--pipe`, `--schema`, and `tool.json`:

- [`king-county-address-to-parcel-number`](https://github.com/chaoz23/king-county-address-to-parcel-number)
- [`king-county-permit-status`](https://github.com/chaoz23/king-county-permit-status)
- [`wa-contractor-license`](https://github.com/chaoz23/wa-contractor-license)

## License

[MIT](LICENSE)
