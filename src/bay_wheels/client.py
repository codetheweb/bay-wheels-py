"""Bay Wheels API client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from curl_cffi.requests import AsyncSession

if TYPE_CHECKING:
    from typing_extensions import Self

from .auth import DEFAULT_TOKEN_PATH, USER_AGENT, AuthManager
from .exceptions import AuthenticationError, BayWheelsError, ReservationError
from .models import Reservation, Station, TokenInfo

BASE_URL = "https://api.lyft.com"


class BayWheelsClient:
    """Async client for the Bay Wheels bike-share API."""

    def __init__(
        self,
        access_token: str | None = None,
        token_path: Path | None = DEFAULT_TOKEN_PATH,
    ) -> None:
        """Initialize the client.

        Args:
            access_token: Optional access token for authenticated requests.
            token_path: Path to store/load tokens. Set to None to disable persistence.
        """
        self._session = AsyncSession(impersonate="chrome")
        self._auth = AuthManager(self._session, token_path=token_path)
        self._owns_session = True

        if access_token is not None:
            self._auth.set_token(TokenInfo(access_token=access_token))

    async def __aenter__(self) -> Self:
        """Enter async context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context and close the HTTP session."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._owns_session:
            await self._session.close()

    @property
    def access_token(self) -> str | None:
        """Get the current access token."""
        return self._auth.access_token

    @property
    def is_authenticated(self) -> bool:
        """Check if the client has an access token."""
        return self._auth.access_token is not None

    def load_token(self) -> TokenInfo | None:
        """Load a saved token from disk.

        Returns:
            The loaded token info, or None if not found.
        """
        return self._auth.load_token()

    def clear_token(self) -> None:
        """Clear the saved token file."""
        self._auth.clear_token()

    def _get_headers(self, authenticated: bool = True) -> dict[str, str]:
        """Get common request headers.

        Args:
            authenticated: Whether to include the Authorization header.

        Returns:
            Dictionary of headers.
        """
        headers = self._auth._get_common_headers()
        headers.update({
            "content-type": "application/json",
        })

        if authenticated and self._auth.access_token:
            headers["authorization"] = f"Bearer {self._auth.access_token}"

        return headers

    # Authentication methods

    async def request_code(self, phone_number: str) -> None:
        """Request an SMS verification code.

        Args:
            phone_number: Phone number in E.164 format (e.g., +14155551234).

        Raises:
            AuthenticationError: If the request fails.
        """
        await self._auth.request_code(phone_number)

    async def login(
        self,
        phone_number: str,
        code: str,
        email: str | None = None,
    ) -> str:
        """Exchange a verification code for an access token.

        Args:
            phone_number: Phone number in E.164 format.
            code: The SMS verification code.
            email: Email address for account verification (if required by API).

        Returns:
            The access token.

        Raises:
            AuthenticationError: If login fails or email verification is needed.
        """
        token_info = await self._auth.login(phone_number, code, email=email)
        return token_info.access_token

    # Station methods

    async def list_stations(self) -> list[Station]:
        """Get all stations with current availability.

        Returns:
            List of stations with bike availability info.

        Raises:
            BayWheelsError: If the request fails.
            AuthenticationError: If not authenticated.
        """
        if not self.is_authenticated:
            raise AuthenticationError("Must be authenticated to list stations")

        response = await self._session.post(
            f"{BASE_URL}/v1/lbsbff/map/inventory",
            json={},
            headers=self._get_headers(),
        )

        if response.status_code == 403:
            raise AuthenticationError("Access denied - token may be expired")

        if response.status_code != 200:
            raise BayWheelsError(f"Failed to get stations: {response.status_code}")

        # Parse response - GeoJSON may be nested in map_inventory_json field
        try:
            outer_data = response.json()

            # Check if GeoJSON is nested inside map_inventory_json
            if "map_inventory_json" in outer_data:
                # It's an escaped JSON string, parse it
                data = json.loads(outer_data["map_inventory_json"])
            else:
                data = outer_data
        except (json.JSONDecodeError, ValueError) as e:
            raise BayWheelsError(f"Failed to parse station data: {e}")

        if data.get("type") != "FeatureCollection":
            raise BayWheelsError(f"Unexpected response format: {data.get('type')}")

        stations = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            # map_item_type=1 are stations, map_item_type=2 are individual bikes
            if props.get("map_item_type") == 1:
                stations.append(Station.from_geojson_feature(feature))

        return stations

    async def get_station(self, station_id: str) -> Station | None:
        """Get a specific station by ID.

        Args:
            station_id: The station ID.

        Returns:
            The station, or None if not found.

        Raises:
            BayWheelsError: If the request fails.
            AuthenticationError: If not authenticated.
        """
        stations = await self.list_stations()
        for station in stations:
            if station.id == station_id:
                return station
        return None

    # Reservation methods

    async def create_reservation(
        self,
        station_id: str,
        bike_type: str = "ebike",
    ) -> Reservation:
        """Create a bike reservation at a station.

        Args:
            station_id: The station ID to reserve a bike from.
            bike_type: Type of bike to reserve ("ebike" or "bike").

        Returns:
            The reservation details.

        Raises:
            ReservationError: If the reservation fails.
            AuthenticationError: If not authenticated.
        """
        if not self.is_authenticated:
            raise AuthenticationError("Must be authenticated to create reservations")

        response = await self._session.post(
            f"{BASE_URL}/v1/last-mile/stations/reserve/v2",
            json={
                "station_id": station_id,
                "reservation_item_key": bike_type,
                "is_apple_pay_authorization_needed": False,
            },
            headers=self._get_headers(),
        )

        if response.status_code == 403:
            raise AuthenticationError("Access denied - token may be expired")

        if response.status_code != 200:
            raise ReservationError(f"Failed to create reservation: {response.status_code}")

        # Parse response - it may be protobuf-wrapped
        content = response.content
        try:
            # Try to extract ride_id from response
            # The response format is protobuf, but we can extract key info
            # Look for the ride_id pattern in the response
            ride_id = None
            status = "reserved"

            # Try JSON first
            try:
                data = response.json()
                ride_id = str(data.get("ride_id", ""))
                status = data.get("status", "reserved")
            except json.JSONDecodeError:
                # Parse protobuf response - ride_id is typically the first field
                # It appears as a string of digits in the response
                import re

                text = content.decode("utf-8", errors="ignore")
                # Look for a long number that could be a ride_id
                match = re.search(r"\b(\d{15,20})\b", text)
                if match:
                    ride_id = match.group(1)

            if not ride_id:
                raise ReservationError("Could not parse reservation response")

            return Reservation(
                ride_id=ride_id,
                status=status,
                station_id=station_id,
            )

        except Exception as e:
            raise ReservationError(f"Failed to parse reservation response: {e}")

    async def cancel_reservation(self, ride_id: str) -> None:
        """Cancel an active reservation.

        Args:
            ride_id: The ride/reservation ID to cancel.

        Raises:
            ReservationError: If cancellation fails.
            AuthenticationError: If not authenticated.
        """
        if not self.is_authenticated:
            raise AuthenticationError("Must be authenticated to cancel reservations")

        response = await self._session.post(
            f"{BASE_URL}/v1/last-mile/rides/cancel",
            json={"ride_id": ride_id},
            headers=self._get_headers(),
        )

        if response.status_code == 403:
            raise AuthenticationError("Access denied - token may be expired")

        if response.status_code != 200:
            raise ReservationError(f"Failed to cancel reservation: {response.status_code}")
