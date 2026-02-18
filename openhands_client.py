import asyncio
import time
import logging
from typing import Optional

import aiohttp

log = logging.getLogger("openhands.client")

MAX_RETRIES = 3


class OpenHandsClient:
    """Async wrapper around the OpenHands Cloud API."""

    def __init__(self, api_key: str, base_url: str = "https://app.all-hands.dev/api"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def create_conversation(
        self,
        task: str,
        repository: Optional[str] = None,
    ) -> dict:
        """
        Start a new OpenHands conversation/task.
        
        Args:
            task: The task description for OpenHands
            repository: Optional GitHub repository (e.g., "username/repo")
            
        Returns:
            dict with conversation_id and other metadata
        """
        session = await self._get_session()
        url = f"{self._base_url}/conversations"
        
        payload = {"initial_user_msg": task}
        if repository:
            payload["repository"] = repository

        for attempt in range(MAX_RETRIES):
            t0 = time.perf_counter()
            log.debug("POST %s payload=%s (attempt %d)", url, payload, attempt + 1)
            
            try:
                resp = await session.post(url, json=payload)
            except Exception as exc:
                log.error("Exception on POST %s: %s", url, exc)
                raise
            
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if resp.status == 429:
                wait = 2 ** attempt
                log.warning(
                    "Rate-limited (429) on %s, retry in %ds (%.0fms)",
                    url, wait, elapsed_ms,
                )
                await resp.release()
                await asyncio.sleep(wait)
                continue

            if resp.status >= 400:
                body = await resp.text()
                log.error(
                    "HTTP %d on %s (%.0fms): %s",
                    resp.status, url, elapsed_ms, body[:300],
                )
                await resp.release()
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=body,
                )

            log.info("POST %s — %d (%.0fms)", url, resp.status, elapsed_ms)
            
            try:
                data = await resp.json()
            finally:
                await resp.release()
            
            return data

        raise RuntimeError("OpenHands API rate limit exceeded after retries")

    async def get_conversation_status(self, conversation_id: str) -> dict:
        """
        Get the status of an OpenHands conversation.
        
        Args:
            conversation_id: The conversation ID returned from create_conversation
            
        Returns:
            dict with status information
        """
        session = await self._get_session()
        url = f"{self._base_url}/conversations/{conversation_id}"
        
        for attempt in range(MAX_RETRIES):
            t0 = time.perf_counter()
            log.debug("GET %s (attempt %d)", url, attempt + 1)
            
            try:
                resp = await session.get(url)
            except Exception as exc:
                log.error("Exception on GET %s: %s", url, exc)
                raise
            
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if resp.status == 429:
                wait = 2 ** attempt
                log.warning(
                    "Rate-limited (429) on %s, retry in %ds (%.0fms)",
                    url, wait, elapsed_ms,
                )
                await resp.release()
                await asyncio.sleep(wait)
                continue

            if resp.status >= 400:
                body = await resp.text()
                log.error(
                    "HTTP %d on %s (%.0fms): %s",
                    resp.status, url, elapsed_ms, body[:300],
                )
                await resp.release()
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=body,
                )

            log.info("GET %s — %d (%.0fms)", url, resp.status, elapsed_ms)
            
            try:
                data = await resp.json()
            finally:
                await resp.release()
            
            return data

        raise RuntimeError("OpenHands API rate limit exceeded after retries")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
