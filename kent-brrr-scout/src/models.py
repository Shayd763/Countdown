"""Pydantic schemas shared across the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PropertyCategory(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    MIXED_USE = "mixed-use"


class Tenure(StrEnum):
    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    SHARE_OF_FREEHOLD = "share-of-freehold"
    UNKNOWN = "unknown"


RefurbTier = Literal["light", "medium", "heavy", "structural"]


def _now() -> datetime:
    return datetime.now(UTC)


class RawListing(BaseModel):
    listing_id: str
    source: str  # e.g. "propertydata:repossessed-properties", "rss:cliveemson"
    url: str | None = None
    address: str = ""
    postcode: str = ""
    outcode: str = ""
    asking_price: float
    property_type: str = "unknown"  # terraced, semi-detached, flat, commercial, ...
    category: PropertyCategory = PropertyCategory.RESIDENTIAL
    tenure: Tenure = Tenure.UNKNOWN
    lease_years_remaining: int | None = None
    bedrooms: int | None = None
    floor_area_sqft: float | None = None
    description: str = ""
    image_urls: list[str] = Field(default_factory=list)
    floorplan_urls: list[str] = Field(default_factory=list)
    days_on_market: int | None = None
    is_auction: bool = False
    lat: float | None = None
    lng: float | None = None
    search_area: str = ""  # the county the portal search covered
    fetched_at: datetime = Field(default_factory=_now)

    @property
    def postcode_area(self) -> str:
        from .geo import area_of

        return area_of(self.outcode)

    @property
    def region(self) -> str:
        from .geo import region_of

        return region_of(self.outcode)


class CompData(BaseModel):
    outcode: str
    property_type: str
    bedrooms: int | None = None
    floor_area_sqft: float
    floor_area_source: str = "default"
    sold_price_median: float | None = None
    sold_comp_count: int = 0
    sold_price_per_sqft: float | None = None
    asking_price_average: float | None = None
    monthly_rent_average: float | None = None
    monthly_rent_low: float | None = None
    epc_rating: str | None = None
    arv_estimates: dict[str, float] = Field(default_factory=dict)
    conservative_arv: float
    conservative_monthly_rent: float
    sources: list[str] = Field(default_factory=list)


class RefurbAssessment(BaseModel):
    """Structured output from Claude (or the offline heuristic)."""

    tier: RefurbTier
    confidence: Literal["low", "medium", "high"]
    key_works: list[str]
    red_flags: list[str]
    detected_tenure: Literal["freehold", "leasehold", "share-of-freehold", "unknown"]
    detected_lease_years: int | None
    is_excluded_type: bool
    exclusion_reason: str | None
    rationale: str


class GateResult(BaseModel):
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str


class UnderwritingResult(BaseModel):
    listing_id: str
    outcode: str
    category: PropertyCategory
    asking_price: float
    conservative_arv: float
    refurb_tier: RefurbTier
    refurb_cost: float
    sdlt: float
    fixed_acquisition_costs: float
    bridging_costs: float
    refi_costs: float
    all_in_cost: float  # gate-1 basis: price + refurb + SDLT + fixed
    total_cash_deployed: float  # all-in + bridging + refi costs
    refi_loan: float
    net_refi_proceeds: float
    money_left_in: float
    capital_recycled_pct: float
    annual_rent: float
    gross_yield: float
    annual_interest: float
    annual_opex: float
    pre_tax_profit: float
    corporation_tax: float
    net_annual_cashflow: float
    net_monthly_cashflow: float
    icr: float
    icr_pass: bool
    roce_pct: float | None  # None == infinite (no money left in)
    max_offer_price: float
    gates: list[GateResult]
    passed: bool
    eligible: bool = True
    eligibility_reasons: list[str] = Field(default_factory=list)
    # finance plan (defaults keep deals saved before plans existed loadable)
    plan_id: str = "bridge_btl_ltd"
    plan_label: str = "Bridge, then Ltd buy-to-let"
    ownership: str = "ltd"  # "ltd" | "personal"
    purchase_method: str = "bridging"  # "cash" | "bridging" | a mortgage product key
    purchase_loan: float = 0.0
    long_term_product: str = "Buy-to-let mortgage (Ltd)"
    annual_mortgage_payment: float = 0.0  # interest, plus capital on a repayment loan
    annual_capital_repaid: float = 0.0
    tax_label: str = "Corporation tax"
    underwritten_at: datetime = Field(default_factory=_now)

    @property
    def roce_display(self) -> str:
        return "∞" if self.roce_pct is None else f"{self.roce_pct:.1f}%"


class Deal(BaseModel):
    """Everything known about one opportunity — what storage and the UI consume."""

    listing: RawListing
    comps: CompData
    refurb: RefurbAssessment
    result: UnderwritingResult
    price_drop_pct: float | None = None
