import asyncio
import time
import logging
from typing import Optional

import aiohttp

log = logging.getLogger("context7.client")

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
            t0 = time.perf_counter()
            log.debug("GET %s params=%s (attempt %d)", url, params, attempt + 1)
            resp = await session.get(url, params=params)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if resp.status == 429:
                wait = 2 ** attempt
                log.warning(
                    "Rate-limited (429) on %s, retry in %ds (%.0fms)",
                    path, wait, elapsed_ms,
                )
                await asyncio.sleep(wait)
                continue

            if resp.status >= 400:
                body = await resp.text()
                log.error(
                    "HTTP %d on %s (%.0fms): %s",
                    resp.status, path, elapsed_ms, body[:300],
                )
                resp.raise_for_status()

            log.info("GET %s — %d (%.0fms)", path, resp.status, elapsed_ms)
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

        data = await resp.json()
        return _normalize_snippets(data)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


def _normalize_snippets(data) -> list[dict]:
    """Convert any Context7 response shape into a flat list of
    ``{title, content, source}`` dicts the bot can render."""

    if isinstance(data, list):
        if data and "codeTitle" in data[0]:
            return [_convert_code_snippet(s) for s in data]
        return data

    if not isinstance(data, dict):
        return []

    if "codeSnippets" in data and isinstance(data["codeSnippets"], list):
        return [_convert_code_snippet(s) for s in data["codeSnippets"]]

    for key in ("results", "snippets", "context", "data", "items"):
        if key in data and isinstance(data[key], list):
            return data[key]

    if "content" in data or "title" in data:
        return [data]

    log.warning("Unknown response shape, keys: %s", list(data.keys()))
    return []


def _convert_code_snippet(s: dict) -> dict:
    """Map Context7's ``codeSnippets`` schema to the flat format the embed builder uses."""
    title = s.get("codeTitle") or s.get("pageTitle") or "Untitled"
    source = s.get("codeId", "")

    parts = []
    desc = s.get("codeDescription", "")
    if desc:
        parts.append(desc)

    code_list = s.get("codeList") or []
    for block in code_list:
        lang = block.get("language", "")
        code = block.get("code", "")
        if code:
            parts.append(f"```{lang}\n{code}\n```")

    return {
        "title": title,
        "content": "\n\n".join(parts) if parts else "(no content)",
        "source": source,
    }
