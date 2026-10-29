import asyncio
from contextlib import closing
import json
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.config import settings
from app.core.upstream_client import UpstreamPortalClient
from app.models.errors import UpstreamError
from app.models.schemas import (
    GeoLocation,
    HierarchyLevel,
    HierarchyNode,
    HierarchyDataQualityIssue,
    HierarchyResponse,
    MeterDetail,
    MeterSummary,
    NearbyMeter,
    NearbyMetersResponse,
    NetworkHierarchy,
    Transformer,
    TransformerDetail,
)

logger = logging.getLogger("flock_urja.cache")


class ForcedSyncCooldownError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("A forced synchronization was requested too recently")


def _extract_extra_attributes(raw_meter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attributes: Dict[str, Any] = {}
    class_data = raw_meter.get("classData")
    if isinstance(class_data, str):
        try:
            class_data = json.loads(class_data)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed classData for meter %s", raw_meter.get("meterId"))
            class_data = None
    if isinstance(class_data, dict):
        installed_meter = class_data.get("installed_meter", class_data)
        if isinstance(installed_meter, dict):
            attributes.update(installed_meter)

    detail = raw_meter.get("detail")
    detail_data = detail.get("data") if isinstance(detail, dict) else None
    if isinstance(detail_data, list):
        for parameter in detail_data:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("parameterName")
            if name:
                attributes[str(name)] = parameter.get("parameterValue")

    return attributes or None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two geographic coordinates in kilometers."""
    radius_earth_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(radius_earth_km * c, 3)


class DataStore:
    """In-memory index and cache for Urja Meter Ops data.
    
    Provides:
    - Fast O(1) lookups by meterId, serialNo, and DT code
    - Complex multi-field filtering across makes, phases, statuses, and DTs
    - Reconstructed 7-tier electrical grid hierarchy tree
    - Geospatial proximity searches (meters within radius_km)
    - Staleness tracking and thread-safe atomic snapshot swapping
    """

    def __init__(self):
        self._meters_by_id: Dict[str, MeterDetail] = {}
        self._meters_by_serial: Dict[str, str] = {}
        self._transformers_by_code: Dict[str, Transformer] = {}
        self._meters_by_dt: Dict[str, List[str]] = {}
        self._last_synced_at: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()
        self._forced_sync_lock = asyncio.Lock()
        self._is_syncing = False

    @property
    def last_synced_at(self) -> Optional[datetime]:
        return self._last_synced_at

    @property
    def total_meters(self) -> int:
        return len(self._meters_by_id)

    @property
    def total_transformers(self) -> int:
        return len(self._transformers_by_code)

    def is_stale(self) -> bool:
        """Check if cached data has exceeded configured TTL."""
        if self._last_synced_at is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_synced_at).total_seconds()
        return elapsed > settings.cache_ttl_seconds

    async def sync(self, client: UpstreamPortalClient, force: bool = False):
        """Fetch fresh metadata from upstream portal and rebuild in-memory indexes."""
        if not force and not self.is_stale() and len(self._meters_by_id) > 0:
            logger.debug("Cache is fresh (synced at %s), skipping sync", self._last_synced_at)
            return

        async with self._sync_lock:
            # Recheck after acquiring lock to prevent stampedes
            if not force and not self.is_stale() and len(self._meters_by_id) > 0:
                return

            self._is_syncing = True
            logger.info("Initiating metadata synchronization from upstream portal")

            try:
                # 1. Fetch DTs
                raw_dts = await client.fetch_all_dts()
                # 2. Fetch all meters via signed export
                raw_export = await client.fetch_bulk_export()
                raw_meters = raw_export.get("data")
                export_total = raw_export.get("total")
                if (
                    not isinstance(raw_meters, list)
                    or not isinstance(export_total, int)
                    or isinstance(export_total, bool)
                    or len(raw_meters) != export_total
                ):
                    raise UpstreamError(
                        "Bulk export is incomplete or invalid: "
                        f"received {len(raw_meters) if isinstance(raw_meters, list) else 'invalid'} "
                        f"records for declared total {export_total!r}"
                    )

                new_meters_by_id: Dict[str, MeterDetail] = {}
                new_meters_by_serial: Dict[str, str] = {}
                new_meters_by_dt: Dict[str, List[str]] = {}

                for m in raw_meters:
                    mid = m.get("meterId")
                    if not mid:
                        continue

                    # Parse geo coordinates
                    geo_obj = None
                    raw_geo = m.get("geo")
                    if raw_geo and "lat" in raw_geo and "lng" in raw_geo:
                        try:
                            geo_obj = GeoLocation(
                                latitude=float(raw_geo["lat"]),
                                longitude=float(raw_geo["lng"]),
                            )
                        except (ValueError, TypeError):
                            geo_obj = None

                    # Parse hierarchy
                    raw_h = m.get("hierarchy") or {}
                    hierarchy_obj = NetworkHierarchy(
                        zone=HierarchyLevel(**raw_h["zone"]) if raw_h.get("zone") else None,
                        circle=HierarchyLevel(**raw_h["circle"]) if raw_h.get("circle") else None,
                        division=HierarchyLevel(**raw_h["division"]) if raw_h.get("division") else None,
                        subdivision=HierarchyLevel(**raw_h["subdivision"]) if raw_h.get("subdivision") else None,
                        substation=HierarchyLevel(**raw_h["substation"]) if raw_h.get("substation") else None,
                        feeder=HierarchyLevel(**raw_h["feeder"]) if raw_h.get("feeder") else None,
                        dt=HierarchyLevel(**raw_h["dt"]) if raw_h.get("dt") else None,
                    )

                    dt_code = m.get("dtCode", "")

                    meter = MeterDetail(
                        meter_id=mid,
                        serial_no=m.get("serialNo", ""),
                        make=m.get("make", ""),
                        phase_type=m.get("phaseType", ""),
                        install_status=m.get("installStatus", ""),
                        dt_code=dt_code,
                        install_type=m.get("installType"),
                        build=m.get("build"),
                        extra_attributes=_extract_extra_attributes(m),
                        geo=geo_obj,
                        hierarchy=hierarchy_obj,
                    )

                    new_meters_by_id[mid] = meter
                    if meter.serial_no:
                        new_meters_by_serial[meter.serial_no] = mid
                    if dt_code:
                        new_meters_by_dt.setdefault(dt_code, []).append(mid)

                if len(new_meters_by_id) != export_total:
                    raise UpstreamError(
                        "Bulk export meter IDs are incomplete or duplicated: "
                        f"indexed {len(new_meters_by_id)} unique meters for declared total {export_total}"
                    )

                # Process transformers and compute connected meters count
                new_transformers_by_code: Dict[str, Transformer] = {}
                for dt in raw_dts:
                    code = dt.get("code")
                    if not code:
                        continue
                    count = len(new_meters_by_dt.get(code, []))
                    new_transformers_by_code[code] = Transformer(
                        code=code,
                        name=dt.get("name", ""),
                        feeder_code=dt.get("feederCode", ""),
                        capacity_kva=int(dt.get("capacityKva", 0)),
                        connected_meters_count=count,
                    )

                # Atomic swap
                self._meters_by_id = new_meters_by_id
                self._meters_by_serial = new_meters_by_serial
                self._meters_by_dt = new_meters_by_dt
                self._transformers_by_code = new_transformers_by_code
                self._last_synced_at = datetime.now(timezone.utc)

                logger.info(
                    "Successfully synced cache: %d meters, %d transformers",
                    len(new_meters_by_id),
                    len(new_transformers_by_code),
                )

            except Exception as exc:
                logger.error("Failed to sync cache from upstream: %s", exc)
                raise
            finally:
                self._is_syncing = False

    async def force_sync(self, client: UpstreamPortalClient) -> None:
        """Force a sync under a persistent, cross-process cooldown/lock."""
        async with self._forced_sync_lock:
            reservation_id = self._reserve_forced_sync()
            try:
                await self.sync(client, force=True)
            except BaseException:
                self._finish_forced_sync(reservation_id, succeeded=False)
                raise
            else:
                self._finish_forced_sync(reservation_id, succeeded=True)

    def _connect_sync_state(self) -> sqlite3.Connection:
        db_path = Path(settings.sync_state_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path, timeout=5)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forced_sync_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                last_success_epoch REAL,
                reservation_id TEXT,
                reservation_expires_epoch REAL
            )
            """
        )
        return connection

    def _reserve_forced_sync(self) -> str:
        reservation_id = uuid4().hex
        now = time.time()
        cooldown = settings.forced_sync_cooldown_seconds
        lease_seconds = max(300, cooldown)

        with closing(self._connect_sync_state()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO forced_sync_state (singleton_id) VALUES (1)"
                )
                last_success, active_id, active_expires = connection.execute(
                    """
                    SELECT last_success_epoch, reservation_id, reservation_expires_epoch
                    FROM forced_sync_state WHERE singleton_id = 1
                    """
                ).fetchone()

                if active_id and active_expires and active_expires > now:
                    retry_after = math.ceil(active_expires - now)
                    raise ForcedSyncCooldownError(retry_after)
                if last_success is not None and cooldown > 0:
                    remaining = cooldown - (now - last_success)
                    if remaining > 0:
                        raise ForcedSyncCooldownError(math.ceil(remaining))

                connection.execute(
                    """
                    UPDATE forced_sync_state
                    SET reservation_id = ?, reservation_expires_epoch = ?
                    WHERE singleton_id = 1
                    """,
                    (reservation_id, now + lease_seconds),
                )
        return reservation_id

    def _finish_forced_sync(self, reservation_id: str, succeeded: bool) -> None:
        with closing(self._connect_sync_state()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO forced_sync_state (singleton_id) VALUES (1)"
                )
                if succeeded:
                    connection.execute(
                        """
                        UPDATE forced_sync_state
                        SET last_success_epoch = ?, reservation_id = NULL,
                            reservation_expires_epoch = NULL
                        WHERE singleton_id = 1 AND reservation_id = ?
                        """,
                        (time.time(), reservation_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE forced_sync_state
                        SET reservation_id = NULL, reservation_expires_epoch = NULL
                        WHERE singleton_id = 1 AND reservation_id = ?
                        """,
                        (reservation_id,),
                    )

    def get_meter(self, meter_id: str) -> Optional[MeterDetail]:
        """Lookup meter by ID (or fallback hardware serial number)."""
        if meter_id in self._meters_by_id:
            return self._meters_by_id[meter_id]
        if meter_id in self._meters_by_serial:
            return self._meters_by_id[self._meters_by_serial[meter_id]]
        return None

    def query_meters(
        self,
        q: Optional[str] = None,
        make: Optional[str] = None,
        phase_type: Optional[str] = None,
        install_status: Optional[str] = None,
        dt_code: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MeterSummary], int]:
        """Perform search and multi-attribute filtering on cached meters."""
        results = list(self._meters_by_id.values())

        # Substring search over meterId or serialNo
        if q:
            query_lower = q.strip().lower()
            results = [
                m for m in results
                if query_lower in m.meter_id.lower() or query_lower in m.serial_no.lower()
            ]

        # Attribute filters
        if make:
            make_lower = make.strip().lower()
            results = [m for m in results if m.make.lower() == make_lower]

        if phase_type:
            phase_lower = phase_type.strip().lower()
            results = [m for m in results if m.phase_type.lower() == phase_lower]

        if install_status:
            status_lower = install_status.strip().lower()
            results = [m for m in results if m.install_status.lower() == status_lower]

        if dt_code:
            dt_upper = dt_code.strip().upper()
            results = [m for m in results if m.dt_code.upper() == dt_upper]

        # Sort predictably by meter_id
        results.sort(key=lambda m: m.meter_id)

        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = results[start:end]

        # Convert to summaries
        summaries = [
            MeterSummary(
                meter_id=m.meter_id,
                serial_no=m.serial_no,
                make=m.make,
                phase_type=m.phase_type,
                install_status=m.install_status,
                dt_code=m.dt_code,
                install_type=m.install_type,
                build=m.build,
            )
            for m in page_items
        ]

        return summaries, total

    def find_nearby(self, latitude: float, longitude: float, radius_km: float) -> List[NearbyMeter]:
        """Find meters within radius_km of coordinates sorted by ascending distance."""
        nearby = []
        for m in self._meters_by_id.values():
            if not m.geo:
                continue
            dist = haversine_km(latitude, longitude, m.geo.latitude, m.geo.longitude)
            if dist <= radius_km:
                nearby.append(
                    NearbyMeter(
                        meter_id=m.meter_id,
                        serial_no=m.serial_no,
                        make=m.make,
                        phase_type=m.phase_type,
                        install_status=m.install_status,
                        dt_code=m.dt_code,
                        install_type=m.install_type,
                        build=m.build,
                        distance_km=dist,
                        geo=m.geo,
                    )
                )

        nearby.sort(key=lambda item: item.distance_km)
        return nearby

    def get_transformer(self, code: str) -> Optional[TransformerDetail]:
        """Lookup transformer and attach all connected meter summaries."""
        code_upper = code.strip().upper()
        # Case-insensitive lookup
        matched_key = None
        for k in self._transformers_by_code:
            if k.upper() == code_upper:
                matched_key = k
                break

        if not matched_key:
            return None

        dt = self._transformers_by_code[matched_key]
        connected_ids = self._meters_by_dt.get(matched_key, [])
        connected_meters = []
        for mid in connected_ids:
            m = self._meters_by_id.get(mid)
            if m:
                connected_meters.append(
                    MeterSummary(
                        meter_id=m.meter_id,
                        serial_no=m.serial_no,
                        make=m.make,
                        phase_type=m.phase_type,
                        install_status=m.install_status,
                        dt_code=m.dt_code,
                        install_type=m.install_type,
                        build=m.build,
                    )
                )

        connected_meters.sort(key=lambda m: m.meter_id)

        return TransformerDetail(
            code=dt.code,
            name=dt.name,
            feeder_code=dt.feeder_code,
            capacity_kva=dt.capacity_kva,
            connected_meters_count=len(connected_meters),
            meters=connected_meters,
        )

    def query_transformers(
        self,
        feeder_code: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Transformer], int]:
        """List transformers with optional feeder filtering and pagination."""
        items = list(self._transformers_by_code.values())
        if feeder_code:
            f_upper = feeder_code.strip().upper()
            items = [t for t in items if t.feeder_code.upper() == f_upper]

        items.sort(key=lambda t: t.code)
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return items[start:end], total

    def build_hierarchy_tree(self) -> HierarchyResponse:
        """Reconstruct full 7-tier distribution tree with aggregated meter counts."""
        # Levels: Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT
        root_nodes: Dict[str, Dict[str, Any]] = {}
        level_names: Dict[str, Dict[str, set[str]]] = {}
        incomplete_meter_ids: List[str] = []
        missing_zone_meter_ids: List[str] = []
        feeder_mismatch_meter_ids: List[str] = []
        dt_mismatch_meter_ids: List[str] = []
        missing_transformer_meter_ids: List[str] = []
        transformers_by_code = {
            code.upper(): transformer for code, transformer in self._transformers_by_code.items()
        }

        for meter in self._meters_by_id.values():
            h = meter.hierarchy
            levels = ("zone", "circle", "division", "subdivision", "substation", "feeder", "dt")
            populated_levels = [getattr(h, level) if h else None for level in levels]
            if any(level is None for level in populated_levels):
                incomplete_meter_ids.append(meter.meter_id)
            if not h or not h.zone:
                missing_zone_meter_ids.append(meter.meter_id)
            if h:
                for level_name, level in zip(levels, populated_levels):
                    if level and level.code:
                        names_by_code = level_names.setdefault(level_name, {}).setdefault(level.code, set())
                        names_by_code.add(level.name)
                if h.dt and h.dt.code and meter.dt_code and h.dt.code.upper() != meter.dt_code.upper():
                    dt_mismatch_meter_ids.append(meter.meter_id)
                transformer = transformers_by_code.get(meter.dt_code.upper())
                if meter.dt_code and not transformer:
                    missing_transformer_meter_ids.append(meter.meter_id)
                if h.feeder and transformer and transformer.feeder_code:
                    if h.feeder.code.upper() != transformer.feeder_code.upper():
                        feeder_mismatch_meter_ids.append(meter.meter_id)
            if not h or not h.zone:
                continue

            z_key = h.zone.code or h.zone.name
            z_node = root_nodes.setdefault(z_key, {
                "name": h.zone.name, "code": h.zone.code, "level": "zone",
                "count": 0, "children": {}
            })
            z_node["count"] += 1

            if not h.circle:
                continue
            c_key = h.circle.code or h.circle.name
            c_node = z_node["children"].setdefault(c_key, {
                "name": h.circle.name, "code": h.circle.code, "level": "circle",
                "count": 0, "children": {}
            })
            c_node["count"] += 1

            if not h.division:
                continue
            d_key = h.division.code or h.division.name
            d_node = c_node["children"].setdefault(d_key, {
                "name": h.division.name, "code": h.division.code, "level": "division",
                "count": 0, "children": {}
            })
            d_node["count"] += 1

            if not h.subdivision:
                continue
            sd_key = h.subdivision.code or h.subdivision.name
            sd_node = d_node["children"].setdefault(sd_key, {
                "name": h.subdivision.name, "code": h.subdivision.code, "level": "subdivision",
                "count": 0, "children": {}
            })
            sd_node["count"] += 1

            if not h.substation:
                continue
            ss_key = h.substation.code or h.substation.name
            ss_node = sd_node["children"].setdefault(ss_key, {
                "name": h.substation.name, "code": h.substation.code, "level": "substation",
                "count": 0, "children": {}
            })
            ss_node["count"] += 1

            if not h.feeder:
                continue
            f_key = h.feeder.code or h.feeder.name
            f_node = ss_node["children"].setdefault(f_key, {
                "name": h.feeder.name, "code": h.feeder.code, "level": "feeder",
                "count": 0, "children": {}
            })
            f_node["count"] += 1

            if not h.dt:
                continue
            dt_key = h.dt.code or h.dt.name
            dt_node = f_node["children"].setdefault(dt_key, {
                "name": h.dt.name, "code": h.dt.code, "level": "dt",
                "count": 0, "children": {}
            })
            dt_node["count"] += 1

        def dict_to_node(data: Dict[str, Any]) -> HierarchyNode:
            children_nodes = [
                dict_to_node(child_data)
                for child_data in sorted(data["children"].values(), key=lambda x: x["code"] or x["name"])
            ]
            return HierarchyNode(
                name=data["name"],
                code=data["code"],
                level=data["level"],
                meter_count=data["count"],
                children=children_nodes,
            )

        tree = [
            dict_to_node(data)
            for data in sorted(root_nodes.values(), key=lambda x: x["code"] or x["name"])
        ]

        issues: List[HierarchyDataQualityIssue] = []
        if incomplete_meter_ids:
            issues.append(HierarchyDataQualityIssue(
                issue_code="incomplete_hierarchy_path",
                count=len(incomplete_meter_ids),
                examples=incomplete_meter_ids[:5],
            ))
        if missing_zone_meter_ids:
            issues.append(HierarchyDataQualityIssue(
                issue_code="meters_missing_zone",
                count=len(missing_zone_meter_ids),
                examples=missing_zone_meter_ids[:5],
            ))
        conflicting_codes = [
            f"{level}:{code}"
            for level, codes in level_names.items()
            for code, names in codes.items()
            if len(names) > 1
        ]
        if conflicting_codes:
            issues.append(HierarchyDataQualityIssue(
                issue_code="hierarchy_code_name_conflict",
                count=len(conflicting_codes),
                examples=conflicting_codes[:5],
            ))
        if dt_mismatch_meter_ids:
            issues.append(HierarchyDataQualityIssue(
                issue_code="meter_transformer_code_mismatch",
                count=len(dt_mismatch_meter_ids),
                examples=dt_mismatch_meter_ids[:5],
            ))
        if missing_transformer_meter_ids:
            issues.append(HierarchyDataQualityIssue(
                issue_code="meter_transformer_not_found",
                count=len(missing_transformer_meter_ids),
                examples=missing_transformer_meter_ids[:5],
            ))
        if feeder_mismatch_meter_ids:
            issues.append(HierarchyDataQualityIssue(
                issue_code="feeder_assignment_mismatch",
                count=len(feeder_mismatch_meter_ids),
                examples=feeder_mismatch_meter_ids[:5],
            ))

        return HierarchyResponse(
            total_meters=len(self._meters_by_id),
            total_transformers=len(self._transformers_by_code),
            tree=tree,
            data_quality=issues,
        )


# Global singleton instance
store = DataStore()
