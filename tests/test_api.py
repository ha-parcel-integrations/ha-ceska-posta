"""Tests for the Ceska Posta / Balikovna API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.ceska_posta.api import (
    CeskaPostaApiClient,
)

CODE_A = "AB1234567890C"
CODE_B = "CD2345678901D"


def _session(responses: dict[str, tuple[int, object]]) -> MagicMock:
    """A session whose ``get(url)`` result depends on a substring of the URL.

    ``responses`` maps a substring to match in the requested URL to
    ``(status, body)``. The first match (in insertion order) wins.
    """

    def _get(url, *args, **kwargs):
        for needle, (status, body) in responses.items():
            if needle in url:
                response = AsyncMock()
                response.status = status
                if isinstance(body, str):
                    response.json = AsyncMock(
                        side_effect=json.JSONDecodeError("x", body, 0)
                    )
                else:
                    response.json = AsyncMock(return_value=body)
                ctx = MagicMock()
                ctx.__aenter__ = AsyncMock(return_value=response)
                ctx.__aexit__ = AsyncMock(return_value=False)
                return ctx
        raise AssertionError(f"unexpected URL: {url}")

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    return session


def _backbone_object(code: str) -> dict:
    return {
        "id": code,
        "attributes": {"weight": 1.0},
        "states": {"state": [{"id": "91", "date": "2026-01-01"}]},
    }


def _enrichment_object(code: str) -> dict:
    return {"packageId": code, "sender": "Example Shop", "packageStates": []}


# ---------------------------------------------------------------------------
# async_get_parcels — happy path
# ---------------------------------------------------------------------------


async def test_get_parcels_returns_backbone_and_enrichment_per_code():
    session = _session(
        {
            "ParcelHistory": (200, [_backbone_object(CODE_A)]),
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
        }
    )
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A])

    assert result[CODE_A]["backbone"]["id"] == CODE_A
    assert result[CODE_A]["enrichment"]["sender"] == "Example Shop"


async def test_get_parcels_empty_input_short_circuits():
    client = CeskaPostaApiClient(MagicMock())
    assert await client.async_get_parcels([]) == {}


async def test_get_parcels_chunks_backbone_in_tens():
    codes = [f"AB{i:010d}C" for i in range(12)]
    calls = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if "ParcelHistory" in url:
            ids = url.split("idParcel=")[1].split("&")[0].split(";")
            body = [_backbone_object(code) for code in ids]
        else:
            code = url.split("/package/")[1].split("?")[0]
            body = _enrichment_object(code)
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(return_value=body)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels(codes)

    backbone_calls = [url for url in calls if "ParcelHistory" in url]
    assert len(backbone_calls) == 2  # 12 codes -> chunks of 10 + 2
    assert all(result[code]["backbone"] is not None for code in codes)


# ---------------------------------------------------------------------------
# per-code failure handling — never raises, always fills the gap with None
# ---------------------------------------------------------------------------


async def test_get_parcels_backbone_failure_leaves_backbone_none(caplog):
    session = _session(
        {
            "ParcelHistory": (500, {}),
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
        }
    )
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A])

    assert result[CODE_A]["backbone"] is None
    assert result[CODE_A]["enrichment"] is not None
    assert "ParcelHistory fetch failed" in caplog.text


async def test_get_parcels_enrichment_failure_leaves_enrichment_none(caplog):
    session = _session(
        {
            "ParcelHistory": (200, [_backbone_object(CODE_A)]),
            f"package/{CODE_A}": (500, {}),
        }
    )
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A])

    assert result[CODE_A]["backbone"] is not None
    assert result[CODE_A]["enrichment"] is None
    assert "enrichment fetch failed" in caplog.text


async def test_get_parcels_both_fail_for_one_code_among_many():
    session = _session(
        {
            "ParcelHistory": (200, [_backbone_object(CODE_A)]),  # CODE_B absent
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
            f"package/{CODE_B}": (500, {}),
        }
    )
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A, CODE_B])

    assert result[CODE_A]["backbone"] is not None
    assert result[CODE_B]["backbone"] is None
    assert result[CODE_B]["enrichment"] is None


async def test_get_parcels_enrichment_network_error_is_caught():
    session = MagicMock()

    def _get(url, *args, **kwargs):
        if "ParcelHistory" in url:
            response = AsyncMock()
            response.status = 200
            response.json = AsyncMock(return_value=[_backbone_object(CODE_A)])
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=response)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx
        raise aiohttp.ClientError("boom")

    session.get = MagicMock(side_effect=_get)
    client = CeskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["enrichment"] is None


async def test_get_parcels_reraises_unexpected_exception():
    """Only API and network errors are tolerated; a bug must not be swallowed."""
    session = MagicMock()

    def _get(url, *args, **kwargs):
        if "ParcelHistory" in url:
            response = AsyncMock()
            response.status = 200
            response.json = AsyncMock(return_value=[_backbone_object(CODE_A)])
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=response)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx
        raise ValueError("boom")

    session.get = MagicMock(side_effect=_get)
    client = CeskaPostaApiClient(session)

    with pytest.raises(ValueError):
        await client.async_get_parcels([CODE_A])


# ---------------------------------------------------------------------------
# body-shape errors
# ---------------------------------------------------------------------------


async def test_backbone_non_array_body_raises():
    session = _session(
        {
            "ParcelHistory": (200, {"not": "an array"}),
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
        }
    )
    client = CeskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["backbone"] is None  # caught internally, logged


async def test_backbone_unparseable_body_raises():
    session = _session(
        {
            "ParcelHistory": (200, "not json"),
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
        }
    )
    client = CeskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["backbone"] is None


async def test_enrichment_non_object_body_raises():
    session = _session(
        {
            "ParcelHistory": (200, [_backbone_object(CODE_A)]),
            f"package/{CODE_A}": (200, ["not", "a", "dict"]),
        }
    )
    client = CeskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["enrichment"] is None


async def test_enrichment_unparseable_body_raises():
    session = _session(
        {
            "ParcelHistory": (200, [_backbone_object(CODE_A)]),
            f"package/{CODE_A}": (200, "not json"),
        }
    )
    client = CeskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["enrichment"] is None


async def test_backbone_object_that_is_not_a_dict_is_skipped():
    session = _session(
        {
            "ParcelHistory": (200, ["not-a-dict"]),
            f"package/{CODE_A}": (200, _enrichment_object(CODE_A)),
        }
    )
    client = CeskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result[CODE_A]["backbone"] is None
