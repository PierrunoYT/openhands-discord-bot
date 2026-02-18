import asyncio
import time
import logging
from typing import Optional

import aiohttp

log = logging.getLogger("context7")

BASE_URL = "https://context7.com/api/v2"
MAX_RETRIES = 3


class Context7Client:
    """Async wrapper around the Context7 REST API."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> dict:
        h = {}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def _request(self, path: str, params: dict) -> aiohttp.ClientResponse:
        session = await self._get_session()
        url = f"{BASE_URL}{path}"

        for attempt in range(MAX_RETRIES):
            resp = await session.get(url, params=params)

            if resp.status == 429:
                wait = 2 ** attempt
                log.warning("Rate-limited by Context7, retrying in %ds…", wait)
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        raise RuntimeError("Context7 rate limit exceeded after retries")

    async def search_library(self, library_name: str, query: str) -> list[dict]:
        resp = await self._request(
            "/libs/search",
            params={"libraryName": library_name, "query": query},
        )
        data = await resp.json()
        if isinstance(data, list):
            return data
        return data.get("results", data.get("libraries", []))

    async def get_context(
        self,
        library_id: str,
        query: str,
        response_type: str = "json",
    ) -> list[dict] | str:
        resp = await self._request(
            "/context",
            params={"libraryId": library_id, "query": query, "type": response_type},
        )
        if response_type == "txt":
            return await resp.text()
        return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
