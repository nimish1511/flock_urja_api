from datetime import datetime, timezone, timedelta
import logging
import math
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.cache import store
from app.core.upstream_client import UpstreamPortalClient
from app.models.errors import ErrorResponse, UpstreamNotFoundError
from app.models.schemas import (
    ConsumptionReading,
    ConsumptionSummary,
    MeterConsumptionResponse,
    MeterDetail,
    MeterListResponse,
    NearbyMetersResponse,
)

logger = logging.getLogger("flock_urja.routers.meters")
router = APIRouter(prefix="/meters", tags=["Meters"])

# IST timezone (+05:30) standard for Indian power distribution utilities
IST = timezone(timedelta(hours=5, minutes=30))


def get_upstream_client() -> UpstreamPortalClient:
    from app.main import portal_client
    return portal_client


def parse_timestamp_to_iso(raw_ts: str) -> str:
    """Parse upstream timestamp format (DD/MM/YYYY HH:MM) into ISO-8601 with IST offset."""
    dt = parse_timestamp(raw_ts)
    return dt.isoformat() if dt else str(raw_ts or "").strip()


def parse_timestamp(raw_ts: str) -> Optional[datetime]:
    """Parse upstream or ISO timestamps into timezone-aware datetimes."""
    raw_ts = str(raw_ts or "").strip()
    try:
        return datetime.strptime(raw_ts, "%d/%m/%Y %H:%M").replace(tzinfo=IST)
    except ValueError:
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt


def safe_float(val: Any) -> Optional[float]:
    """Parse string or number into float, returning None if missing, empty, or placeholder."""
    if val is None or val == "" or val == "—" or val == "-":
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


@router.get(
    "",
    response_model=MeterListResponse,
    summary="List and Search Smart Meters",
    description="Retrieve a paginated list of smart meters with optional query search and multi-attribute filters.",
    responses={
        200: {"description": "List of matching meters."},
    },
)
async def list_meters(
    q: Optional[str] = Query(None, description="Search term for meter ID or hardware serial number"),
    make: Optional[str] = Query(None, description="Filter by manufacturer (e.g. HPL, Secure, Genus, L&T, Allied)"),
    phase_type: Optional[str] = Query(None, description="Filter by phase type ('single' or 'three')"),
    install_status: Optional[str] = Query(None, description="Filter by operational status ('Installed', 'Decommissioned', 'Faulty')"),
    dt_code: Optional[str] = Query(None, description="Filter by Distribution Transformer code (e.g. 'DT-001')"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> MeterListResponse:
    # Ensure cache is populated
    await store.sync(client)

    items, total = store.query_meters(
        q=q,
        make=make,
        phase_type=phase_type,
        install_status=install_status,
        dt_code=dt_code,
        page=page,
        page_size=page_size,
    )

    pages = math.ceil(total / page_size) if total > 0 else 1

    return MeterListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/nearby",
    response_model=NearbyMetersResponse,
    summary="Find Meters Near Location",
    description="Spatial query returning smart meters within radius_km of specified latitude and longitude.",
)
async def get_nearby_meters(
    latitude: float = Query(..., description="Target latitude in decimal degrees", ge=-90.0, le=90.0),
    longitude: float = Query(..., description="Target longitude in decimal degrees", ge=-180.0, le=180.0),
    radius_km: float = Query(1.0, ge=0.01, le=50.0, description="Search radius in kilometers (max 50km)"),
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> NearbyMetersResponse:
    await store.sync(client)
    nearby_meters = store.find_nearby(latitude, longitude, radius_km)

    return NearbyMetersResponse(
        center_latitude=latitude,
        center_longitude=longitude,
        radius_km=radius_km,
        total=len(nearby_meters),
        meters=nearby_meters,
    )


@router.get(
    "/{meter_id}",
    response_model=MeterDetail,
    summary="Get Meter Details",
    description="Retrieve comprehensive details for a specific smart meter, including nameplate attributes, hierarchy, and coordinates.",
    responses={
        200: {"description": "Detailed meter information."},
        404: {"model": ErrorResponse, "description": "Meter not found."},
    },
)
async def get_meter(
    meter_id: str,
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> MeterDetail:
    await store.sync(client)
    meter = store.get_meter(meter_id)

    if not meter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "METER_NOT_FOUND", "message": f"Meter with ID '{meter_id}' was not found in the utility system."},
        )

    return meter


@router.get(
    "/{meter_id}/consumption",
    response_model=MeterConsumptionResponse,
    summary="Get Meter Consumption Readings",
    description="Fetch historical 30-minute interval readings for a meter with normalized timestamps and summary statistics.",
    responses={
        200: {"description": "Historical readings and computed statistics."},
        404: {"model": ErrorResponse, "description": "Meter not found."},
    },
)
async def get_meter_consumption(
    meter_id: str,
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> MeterConsumptionResponse:
    # First verify meter exists
    await store.sync(client)
    meter = store.get_meter(meter_id)
    if not meter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "METER_NOT_FOUND", "message": f"Meter with ID '{meter_id}' was not found."},
        )

    try:
        raw_readings = await client.fetch_meter_energy(meter.meter_id)
    except UpstreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "METER_NOT_FOUND", "message": f"Meter with ID '{meter_id}' was not found."},
        )

    timestamped_readings: List[tuple[Optional[datetime], int, ConsumptionReading]] = []
    voltage_values: List[float] = []

    for source_index, r in enumerate(raw_readings):
        raw_ts = r.get("timestamp") or ""
        timestamp = parse_timestamp(raw_ts)
        ts_iso = timestamp.isoformat() if timestamp else str(raw_ts).strip()

        kwh = safe_float(r.get("kwh"))
        kvah = safe_float(r.get("kvah"))
        volt_r = safe_float(r.get("voltR"))

        if volt_r is not None:
            voltage_values.append(volt_r)

        timestamped_readings.append((
            timestamp,
            source_index,
            ConsumptionReading(
                timestamp_iso=ts_iso,
                timestamp_raw=str(raw_ts),
                kwh=kwh,
                kvah=kvah,
                voltage_r=volt_r,
                interval_status="baseline",
            ),
        ))

    unique_by_timestamp: dict[datetime, tuple[datetime, int, ConsumptionReading]] = {}
    invalid_timestamps: List[tuple[Optional[datetime], int, ConsumptionReading]] = []
    duplicate_reading_count = 0
    for entry in timestamped_readings:
        timestamp, source_index, reading = entry
        if timestamp is None:
            invalid_timestamps.append(entry)
            continue
        existing = unique_by_timestamp.get(timestamp)
        if existing is None:
            unique_by_timestamp[timestamp] = entry
            continue
        duplicate_reading_count += 1
        existing_reading = existing[2]
        existing_score = sum(value is not None for value in (
            existing_reading.kwh, existing_reading.kvah, existing_reading.voltage_r
        ))
        current_score = sum(value is not None for value in (
            reading.kwh, reading.kvah, reading.voltage_r
        ))
        if current_score >= existing_score:
            unique_by_timestamp[timestamp] = entry

    timestamped_readings = sorted(unique_by_timestamp.values(), key=lambda entry: entry[0])
    timestamped_readings.extend(invalid_timestamps)
    normalized_readings = [entry[2] for entry in timestamped_readings]

    interval_values: List[float] = []
    total_deltas: List[float] = []
    gap_interval_count = 0
    previous: Optional[tuple[datetime, ConsumptionReading]] = None
    for timestamp, _, reading in timestamped_readings:
        if timestamp is None:
            reading.interval_status = "invalid_timestamp"
            continue
        if reading.kwh is None:
            reading.interval_status = "missing_reading"
            continue
        if previous is None:
            reading.interval_status = "baseline"
            previous = (timestamp, reading)
            continue
        previous_timestamp, previous_reading = previous
        if reading.kwh < previous_reading.kwh:
            reading.interval_status = "meter_reset"
            previous = (timestamp, reading)
        else:
            interval_kwh = round(reading.kwh - previous_reading.kwh, 3)
            reading.interval_kwh = interval_kwh
            duration_minutes = (timestamp - previous_timestamp).total_seconds() / 60
            reading.interval_duration_minutes = round(duration_minutes, 3)
            total_deltas.append(interval_kwh)

            if math.isclose(duration_minutes, 30.0, abs_tol=0.01):
                reading.interval_status = "valid"
                interval_values.append(interval_kwh)
                if previous_reading.kvah is not None and reading.kvah is not None:
                    interval_kvah = reading.kvah - previous_reading.kvah
                    if interval_kvah > 0 and 0 <= interval_kwh <= interval_kvah:
                        reading.interval_power_factor = round(interval_kwh / interval_kvah, 3)
            else:
                reading.interval_status = "time_gap" if duration_minutes > 30 else "irregular_interval"
                gap_interval_count += 1
            previous = (timestamp, reading)

    reading_count = len(normalized_readings)
    valid_timestamps = [timestamp for timestamp, _, _ in timestamped_readings if timestamp is not None]
    period_start = valid_timestamps[0].isoformat() if valid_timestamps else None
    period_end = valid_timestamps[-1].isoformat() if valid_timestamps else None

    min_interval_kwh = min(interval_values) if interval_values else None
    max_interval_kwh = max(interval_values) if interval_values else None
    avg_interval_kwh = round(sum(interval_values) / len(interval_values), 2) if interval_values else None
    total_kwh = round(sum(total_deltas), 2) if total_deltas else None
    avg_volt_r = round(sum(voltage_values) / len(voltage_values), 1) if voltage_values else None

    summary = ConsumptionSummary(
        reading_count=reading_count,
        interval_count=len(interval_values),
        gap_interval_count=gap_interval_count,
        duplicate_reading_count=duplicate_reading_count,
        period_start=period_start,
        period_end=period_end,
        total_kwh=total_kwh,
        avg_interval_kwh=avg_interval_kwh,
        min_interval_kwh=min_interval_kwh,
        max_interval_kwh=max_interval_kwh,
        avg_voltage_r=avg_volt_r,
    )

    return MeterConsumptionResponse(
        meter_id=meter.meter_id,
        summary=summary,
        readings=normalized_readings,
    )
