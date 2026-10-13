"""Abstract flight provider interface + shared HTTP resilience helpers."""

from __future__ import annotations

import abc
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base_types import (
    FlightOffer,
    FlightSearchQuery,
    OrderResult,
    PassengerInfo,
)

logger = get_logger(__name__)


class FlightProvider(abc.ABC):
    """Contract every flight inventory provider must satisfy."""

    name: str = "abstract"

    @abc.abstractmethod
    async def search(self, query: FlightSearchQuery) -> list[FlightOffer]:
        """Search available itineraries."""

    @abc.abstractmethod
    async def price(self, offer: FlightOffer) -> FlightOffer:
        """(Re)price an offer, confirming availability and final taxes."""

    @abc.abstractmethod
    async def order(
        self,
        offer: FlightOffer,
        passengers: list[PassengerInfo],
        *,
        contact_email: str,
        contact_phone: str | None = None,
    ) -> OrderResult:
        """Create a booking/order from a priced offer."""

    async def cancel(self, order_id: str) -> OrderResult:  # pragma: no cover - optional
        raise ProviderError(f"{self.name} does not support cancellation.")

    @property
    def configured(self) -> bool:  # pragma: no cover - overridden
        """Whether credentials/endpoint are present for this provider."""
        return True


class HttpFlightProvider(FlightProvider):
    """Base for HTTP providers: shared client, retries and error mapping."""

    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = get_settings()

    def _client(self, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._settings.provider_timeout_seconds,
            headers=headers,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
        params: dict | None = None,
        expected_error_codes: tuple[int, ...] = (),
    ) -> Any:
        """Perform an HTTP request, mapping failures to ProviderError."""
        async with self._client(headers) as client:
            response = await client.request(method, url, json=json, params=params)
        if response.status_code >= 400 and response.status_code not in expected_error_codes:
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text[:500]
            logger.warning(
                "provider_http_error",
                provider=self.name,
                status=response.status_code,
                body=body,
            )
            raise ProviderError(
                f"{self.name} returned HTTP {response.status_code}.",
                details={"status": response.status_code, "body": body},
            )
        if not response.content:
            return None
        return response.json()
