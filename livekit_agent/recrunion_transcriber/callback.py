import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalTranscriptSegment:
    room_name: str
    participant_identity: str
    segment_key: str
    started_at: datetime
    ended_at: datetime
    text: str
    language: str | None
    provider: str
    provider_segment_id: str | None = None


class AsyncHTTPClient(Protocol):
    async def post(self, url: str, **kwargs: object) -> httpx.Response: ...

    async def aclose(self) -> None: ...


class TranscriptionCallbackClient:
    """Persist final segments through a bounded, authenticated callback."""

    def __init__(
        self,
        *,
        callback_url: str,
        secret: str,
        interview_id: UUID,
        http_client: AsyncHTTPClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._callback_url = callback_url
        self._secret = secret
        self._interview_id = interview_id
        self._http_client = http_client if http_client is not None else httpx.AsyncClient()
        self._owns_http_client = http_client is None
        self._closed = False
        self._sleeper = sleeper

    async def aclose(self) -> None:
        """Close the reusable HTTP client when it is owned by this callback client."""
        if self._owns_http_client and not self._closed:
            self._closed = True
            await self._http_client.aclose()

    async def persist(self, segment: FinalTranscriptSegment) -> bool:
        payload = asdict(segment)
        payload["started_at"] = segment.started_at.isoformat()
        payload["ended_at"] = segment.ended_at.isoformat()
        delays = (0.0, 0.5, 1.0)
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await self._sleeper(delay)
            try:
                response = await self._post(payload)
                if response.is_success:
                    return True
                retryable = response.status_code >= 500 or response.status_code in {408, 429}
                if not retryable:
                    break
            except httpx.HTTPError:
                pass
            if attempt == len(delays):
                break
        logger.warning(
            "Transcript callback failed interview_id=%s segment_key=%s publisher=%s",
            self._interview_id,
            segment.segment_key,
            segment.participant_identity.split("_", 1)[0],
        )
        return False

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._secret}"}
        return await self._http_client.post(
            self._callback_url,
            json=payload,
            headers=headers,
            timeout=5.0,
        )
