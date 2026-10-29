from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GeoLocation(BaseModel):
    """Geographic coordinates of a meter or asset."""
    latitude: float = Field(description="Latitude in decimal degrees", examples=[26.938961])
    longitude: float = Field(description="Longitude in decimal degrees", examples=[75.830957])


class HierarchyLevel(BaseModel):
    """A node inside the utility distribution hierarchy."""
    name: str = Field(description="Name of the administrative or technical division", examples=["Jaipur Zone 1"])
    code: str = Field(description="Standardized code for the division", examples=["Z-01"])


class NetworkHierarchy(BaseModel):
    """Complete 7-tier distribution hierarchy path for a meter."""
    zone: Optional[HierarchyLevel] = None
    circle: Optional[HierarchyLevel] = None
    division: Optional[HierarchyLevel] = None
    subdivision: Optional[HierarchyLevel] = None
    substation: Optional[HierarchyLevel] = None
    feeder: Optional[HierarchyLevel] = None
    dt: Optional[HierarchyLevel] = Field(default=None, description="Distribution Transformer level")


class MeterSummary(BaseModel):
    """Concise representation of a smart meter for search and listing."""
    meter_id: str = Field(description="Unique utility identifier for the meter", examples=["J100000"])
    serial_no: str = Field(description="Manufacturer hardware serial number", examples=["SE33962"])
    make: str = Field(description="Meter hardware manufacturer", examples=["HPL"])
    phase_type: str = Field(description="Single-phase or three-phase electrical supply", examples=["single"])
    install_status: str = Field(description="Operational status", examples=["Installed"])
    dt_code: str = Field(description="Code of the parent Distribution Transformer", examples=["DT-001"])
    install_type: Optional[str] = Field(default=None, description="Whole Current or CT Operated", examples=["Whole Current"])
    build: Optional[str] = Field(default=None, description="Hardware build/firmware version tier", examples=["legacy"])


class MeterDetail(MeterSummary):
    """Complete meter details including geolocation and network hierarchy."""
    geo: Optional[GeoLocation] = None
    hierarchy: Optional[NetworkHierarchy] = None
    extra_attributes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Best-effort nameplate values from bulk-export classData/detail.data; null when absent (meter detail pages are not fetched)",
    )


class MeterListResponse(BaseModel):
    """Paginated list of smart meters."""
    items: List[MeterSummary]
    total: int = Field(description="Total matching meters across all pages", examples=[403])
    page: int = Field(description="Current 1-based page number", examples=[1])
    page_size: int = Field(description="Number of items per page", examples=[20])
    pages: int = Field(description="Total number of pages", examples=[21])


class ConsumptionReading(BaseModel):
    """Single energy meter reading at a specific timestamp."""
    timestamp_iso: str = Field(description="Normalized ISO-8601 timestamp with timezone", examples=["2026-06-23T23:30:00+05:30"])
    timestamp_raw: str = Field(description="Original timestamp string from upstream portal", examples=["23/06/2026 23:30"])
    kwh: Optional[float] = Field(default=None, description="Cumulative active-energy register in kilowatt-hours (kWh)", examples=[48438.74])
    kvah: Optional[float] = Field(default=None, description="Cumulative apparent-energy register in kilovolt-ampere-hours (kVAh)", examples=[52313.84])
    interval_kwh: Optional[float] = Field(default=None, description="Active energy since the preceding usable cumulative register; may span a data gap", examples=[4.5])
    interval_duration_minutes: Optional[float] = Field(default=None, description="Elapsed time between the two cumulative registers used for interval_kwh")
    voltage_r: Optional[float] = Field(default=None, description="R-phase voltage in volts (V)", examples=[226.0])
    interval_power_factor: Optional[float] = Field(default=None, description="Estimated interval energy ratio (delta kWh / delta kVAh), only for a valid nominal interval", examples=[0.926])
    interval_status: str = Field(description="Interval classification: baseline, valid, missing_reading, meter_reset, time_gap, irregular_interval, or invalid_timestamp")


class ConsumptionSummary(BaseModel):
    """Summary statistics for a series of meter consumption readings."""
    reading_count: int = Field(description="Total count of readings in time window", examples=[337])
    interval_count: int = Field(description="Number of valid consecutive intervals used in the consumption summary", examples=[336])
    gap_interval_count: int = Field(default=0, description="Number of register deltas spanning a non-standard time interval; these contribute to total_kwh but not average/min/max")
    duplicate_reading_count: int = Field(default=0, description="Number of duplicate-timestamp readings omitted; the most complete, latest duplicate is retained")
    period_start: Optional[str] = Field(default=None, description="Earliest reading timestamp in ISO-8601 format")
    period_end: Optional[str] = Field(default=None, description="Latest reading timestamp in ISO-8601 format")
    total_kwh: Optional[float] = Field(default=None, description="Sum of consecutive non-reset register deltas, including deltas spanning missing/irregular timestamps; reset-crossing deltas are excluded")
    avg_interval_kwh: Optional[float] = Field(default=None, description="Average active energy per valid 30-minute interval; longer/shorter gaps are excluded")
    min_interval_kwh: Optional[float] = Field(default=None, description="Minimum valid 30-minute interval consumption in kWh")
    max_interval_kwh: Optional[float] = Field(default=None, description="Maximum valid 30-minute interval consumption in kWh")
    avg_voltage_r: Optional[float] = Field(default=None, description="Average R-phase voltage")


class MeterConsumptionResponse(BaseModel):
    """Complete consumption response for a meter."""
    meter_id: str = Field(description="Unique utility identifier for the meter", examples=["J100000"])
    summary: ConsumptionSummary
    readings: List[ConsumptionReading]


class NearbyMeter(MeterSummary):
    """Meter returned from a geospatial proximity query."""
    distance_km: float = Field(description="Distance in kilometers from query coordinates", examples=[0.45])
    geo: GeoLocation


class NearbyMetersResponse(BaseModel):
    """List of meters within geographic radius."""
    center_latitude: float
    center_longitude: float
    radius_km: float
    total: int
    meters: List[NearbyMeter]


class Transformer(BaseModel):
    """Distribution Transformer (DT) summary."""
    code: str = Field(description="Unique DT code", examples=["DT-001"])
    name: str = Field(description="DT human-readable name", examples=["Malviya Nagar DT 1"])
    feeder_code: str = Field(description="Parent 11kV feeder code", examples=["F-001"])
    capacity_kva: int = Field(description="Rated transformer capacity in kVA", examples=[100])
    connected_meters_count: Optional[int] = Field(default=None, description="Number of active meters connected to this DT")


class TransformerDetail(Transformer):
    """Detailed DT info including all connected smart meters."""
    meters: List[MeterSummary] = Field(default_factory=list, description="All smart meters connected to this transformer")


class TransformerListResponse(BaseModel):
    """Paginated list of Distribution Transformers."""
    items: List[Transformer]
    total: int
    page: int
    page_size: int
    pages: int


class HierarchyNode(BaseModel):
    """Recursive node representing a level in the electrical grid hierarchy."""
    name: str = Field(description="Name of division or asset", examples=["Jaipur Zone 1"])
    code: str = Field(description="Identifier or code", examples=["Z-01"])
    level: str = Field(description="Hierarchy tier", examples=["zone"])
    meter_count: int = Field(default=0, description="Total meters under this subtree")
    children: List["HierarchyNode"] = Field(default_factory=list, description="Subordinate hierarchy nodes")


class HierarchyDataQualityIssue(BaseModel):
    """A summary of source hierarchy inconsistencies detected during reconstruction."""
    issue_code: str = Field(description="Stable identifier for the detected data-quality issue")
    count: int = Field(description="Number of affected meters or conflicting hierarchy codes")
    examples: List[str] = Field(default_factory=list, description="Up to five affected meter IDs or conflicting hierarchy codes")


class HierarchyResponse(BaseModel):
    """Full grid distribution hierarchy tree."""
    total_meters: int
    total_transformers: int
    tree: List[HierarchyNode]
    data_quality: List[HierarchyDataQualityIssue] = Field(
        default_factory=list,
        description="Detected gaps and inconsistencies in source hierarchy assignments; the tree is reconstructed, not independently verified",
    )


class SyncStatusResponse(BaseModel):
    """Status of upstream metadata cache and synchronization."""
    status: str = Field(description="Cache synchronization status", examples=["synced"])
    last_synced_at: Optional[str] = Field(default=None, description="Timestamp of last successful sync")
    meters_cached: int = Field(default=0, description="Number of meters stored in memory cache")
    transformers_cached: int = Field(default=0, description="Number of DTs stored in memory cache")
    cache_ttl_seconds: int = Field(description="Configured TTL in seconds")
    is_stale: bool = Field(description="Whether cached data has exceeded TTL")


class HealthResponse(BaseModel):
    """Health status and diagnostic info."""
    status: str = Field(description="Overall service health", examples=["ok"])
    service: str = Field(description="Service identifier", examples=["flock-urja-api"])
    version: str = Field(description="Service version", examples=["1.0.0"])
    portal_connected: bool = Field(description="Whether upstream Urja Meter Ops portal is reachable", examples=[True])
    cache_synced: bool = Field(description="Whether in-memory cache has synced data", examples=[True])
    timestamp: str = Field(description="Current server timestamp")
