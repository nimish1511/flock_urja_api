import asyncio
import pytest
from app.config import settings
from app.core.upstream_client import UpstreamPortalClient


def test_live_portal_smoke():
    """Live read-only smoke test against the running Urja Meter Ops portal.
    
    Verifies live authentication, search, DT fetch, energy readings, and paged export completeness.
    """
    async def _run():
        client = UpstreamPortalClient()
        try:
            # 1. Test live health
            alive = await client.check_health()
            if not alive:
                pytest.skip("Upstream portal is currently unreachable")

            # 2. Test live authentication
            auth_success = await client.authenticate()
            assert auth_success is True

            # 3. Test live meter search
            search_res = await client.search_meters(query="J100000", page=1)
            assert search_res.get("total", 0) > 0
            assert len(search_res.get("data", [])) > 0
            assert search_res["data"][0]["meterId"] == "J100000"

            # 4. Test live DT fetch
            dts_res = await client.fetch_dts(page=1)
            assert dts_res.get("total", 0) >= 40
            assert len(dts_res.get("data", [])) > 0

            # 5. Test live energy consumption for sample meter J100000
            energy_res = await client.fetch_meter_energy("J100000")
            assert len(energy_res) > 0
            assert "timestamp" in energy_res[0]
            assert "kwh" in energy_res[0]

            # 6. Test live HMAC bulk export
            export_res = await client.fetch_bulk_export()
            assert export_res.get("total", 0) > 0
            assert len(export_res.get("data", [])) == export_res["total"]

        finally:
            await client.close()

    asyncio.run(_run())
