"""Ceska Posta / Balikovna public tracking API client.

Two keyless, unauthenticated JSON surfaces on the same backend:

* the ParcelHistory backbone (``b2c.cpost.cz``) — batched, up to
  :data:`~.const.BACKBONE_CHUNK_SIZE` tracking codes per call, joined with
  ``;``. Carries the event history and the weight.
* the Balikovna enrichment endpoint (``www.balikovna.cz``) — one call per
  parcel (``;`` silently drops everything after the first code, so it cannot
  batch). Adds the sender, the pickup point and the storage deadline.

Both answer HTTP 200 for an unknown tracking code — "not found" is a state
inside the body (event id ``-3``/``-4`` on the backbone, ``sender == "-"`` on
the enrichment call), never an HTTP error; :mod:`.parcels` is what branches on
that. This client's own error contract is narrower: :meth:`async_get_parcels`
never raises for a single code's fetch failure — a failed chunk or a failed
enrichment call is logged and that code's half of the result is ``None``, so
the coordinator can fall back to cached data. Only a bug elsewhere (an
exception that is neither :class:`CeskaPostaApiError` nor
``aiohttp.ClientError``) propagates.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import BACKBONE_CHUNK_SIZE, PACKAGE_URL, PARCEL_HISTORY_URL

_LOGGER = logging.getLogger(__name__)


class CeskaPostaApiError(Exception):
    """Raised when a Ceska Posta / Balikovna request returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the detail that triggered the error."""
        super().__init__(f"Ceska Posta API request failed: {detail}")
        self.detail = detail


class CeskaPostaApiClient:
    """Client for the two keyless Ceska Posta / Balikovna tracking surfaces."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcels(
        self, tracking_codes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch the backbone + enrichment payload for every tracked code.

        Returns one entry per requested code:
        ``{"backbone": {...} | None, "enrichment": {...} | None}``. A code
        that only one surface recognises still gets a dict — a failure is
        only ``None``. Both halves are ``None` only when both requests for
        that code failed outright; :mod:`.coordinator` decides whether to
        fall back to cached data in that case.
        """
        if not tracking_codes:
            return {}

        backbone = await self._async_get_backbone(tracking_codes)

        enrichment_results = await asyncio.gather(
            *(self._async_get_enrichment(code) for code in tracking_codes),
            return_exceptions=True,
        )

        merged: dict[str, dict[str, Any]] = {}
        for code, enrichment in zip(tracking_codes, enrichment_results):
            if isinstance(enrichment, BaseException):
                if not isinstance(
                    enrichment, (CeskaPostaApiError, aiohttp.ClientError)
                ):
                    raise enrichment
                _LOGGER.warning(
                    "Balikovna enrichment fetch failed for %s: %s", code, enrichment
                )
                enrichment = None
            merged[code] = {"backbone": backbone.get(code), "enrichment": enrichment}
        return merged

    async def _async_get_backbone(
        self, tracking_codes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch the ParcelHistory backbone for every code, chunked in tens."""
        results: dict[str, dict[str, Any]] = {}
        for start in range(0, len(tracking_codes), BACKBONE_CHUNK_SIZE):
            chunk = tracking_codes[start : start + BACKBONE_CHUNK_SIZE]
            try:
                objects = await self._async_get_backbone_chunk(chunk)
            except (CeskaPostaApiError, aiohttp.ClientError) as err:
                _LOGGER.warning(
                    "Ceska Posta ParcelHistory fetch failed for %s: %s", chunk, err
                )
                continue
            # The endpoint documents the array as one object per id, in
            # request order; zip rather than trust an echoed id so a chunk
            # that comes back short can't shift every later code's mapping.
            for code, obj in zip(chunk, objects):
                if isinstance(obj, dict):
                    results[code] = obj
        return results

    async def _async_get_backbone_chunk(self, codes: list[str]) -> list[Any]:
        """Fetch one ParcelHistory chunk (up to ten codes)."""
        url = PARCEL_HISTORY_URL.format(ids=";".join(codes))
        async with self._session.get(url) as response:
            if response.status != 200:
                raise CeskaPostaApiError(f"HTTP {response.status} from ParcelHistory")
            try:
                # content_type=None: consumer endpoints routinely serve JSON
                # as text/plain, and aiohttp would otherwise refuse to parse
                # it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise CeskaPostaApiError(
                    f"unparseable ParcelHistory body ({err})"
                ) from err
        if not isinstance(payload, list):
            raise CeskaPostaApiError("unexpected ParcelHistory body (not a JSON array)")
        return payload

    async def _async_get_enrichment(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch the Balikovna enrichment payload for one code."""
        url = PACKAGE_URL.format(tracking_code=tracking_code)
        async with self._session.get(url) as response:
            if response.status != 200:
                raise CeskaPostaApiError(
                    f"HTTP {response.status} from Balikovna package"
                )
            try:
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise CeskaPostaApiError(
                    f"unparseable Balikovna body ({err})"
                ) from err
        if not isinstance(payload, dict):
            raise CeskaPostaApiError("unexpected Balikovna body (not a JSON object)")
        return payload
