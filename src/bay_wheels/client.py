"""Bay Wheels API client."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from curl_cffi.requests import AsyncSession

if TYPE_CHECKING:
    from typing_extensions import Self

from .auth import USER_AGENT, AuthManager
from .exceptions import AuthenticationError, BayWheelsError, ReservationError
from .models import Reservation, Station, StationBike, TokenInfo

BASE_URL = "https://api.lyft.com"


class BayWheelsClient:
    """Async client for the Bay Wheels bike-share API."""

    def __init__(
        self,
        access_token: str | None = None,
        token_info: TokenInfo | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            access_token: Optional access token for authenticated requests.
            token_info: Optional full token info (takes precedence over access_token).
        """
        self._session = AsyncSession(impersonate="chrome")
        self._auth = AuthManager(self._session)
        self._owns_session = True

        if token_info is not None:
            self._auth.set_token(token_info)
        elif access_token is not None:
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

    def set_token(self, token_info: TokenInfo) -> None:
        """Set the authentication token."""
        self._auth.set_token(token_info)

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
    ) -> TokenInfo:
        """Exchange a verification code for an access token.

        Args:
            phone_number: Phone number in E.164 format.
            code: The SMS verification code.
            email: Email address for account verification (if required by API).

        Returns:
            The token info containing the access token.

        Raises:
            AuthenticationError: If login fails or email verification is needed.
        """
        return await self._auth.login(phone_number, code, email=email)

    # Station methods

    async def _fetch_gbfs_station_names(self) -> dict[str, str]:
        """Fetch station names from the public GBFS feed.

        Returns:
            Dict mapping station UUID to station name.
        """
        try:
            response = await self._session.get(
                "https://gbfs.lyft.com/gbfs/2.3/bay/en/station_information.json"
            )
            if response.status_code != 200:
                return {}
            data = response.json()
            return {
                s["station_id"]: s["name"]
                for s in data.get("data", {}).get("stations", [])
            }
        except Exception:
            return {}

    def _extract_station_uuid(self, station_id: str) -> str | None:
        """Extract the UUID from a station ID.

        Station IDs are in format 'motivate_XXX_<uuid>'.
        """
        parts = station_id.split("_", 2)
        if len(parts) >= 3:
            return parts[2]
        return None

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

        # Fetch station names from GBFS
        gbfs_names = await self._fetch_gbfs_station_names()

        stations = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            # map_item_type=1 are stations, map_item_type=2 are individual bikes
            if props.get("map_item_type") == 1:
                station = Station.from_geojson_feature(feature)
                # Look up name from GBFS using the UUID portion of the ID
                uuid = self._extract_station_uuid(station.id)
                if uuid and uuid in gbfs_names:
                    station.name = gbfs_names[uuid]
                stations.append(station)

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

    async def get_station_bikes(self, station_id: str) -> list[StationBike]:
        """Get e-bikes at a station with their estimated range.

        Args:
            station_id: The station ID.

        Returns:
            List of bikes with range info.

        Raises:
            BayWheelsError: If the request fails.
            AuthenticationError: If not authenticated.
        """
        if not self.is_authenticated:
            raise AuthenticationError("Must be authenticated to get station bikes")

        # The API requires a panel_request with UI capabilities
        request_body = {
            "station_id": station_id,
            "lastmile_rewards_user_education_messages_enabled": True,
            "panel_request": {
                "panel_specification": {
                    "canvas_capabilities": {
                        "label_capabilities": {"rich_text": True},
                    },
                    "native_components_inside_canvas_supported": True,
                },
                "server_actions": {},
            },
        }

        response = await self._session.post(
            f"{BASE_URL}/v1/lbsbff/panel/pre-ride-station",
            json=request_body,
            headers=self._get_headers(),
        )

        if response.status_code == 403:
            raise AuthenticationError("Access denied - token may be expired")

        if response.status_code != 200:
            raise BayWheelsError(f"Failed to get station bikes: {response.status_code}")

        # Parse JSON response
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise BayWheelsError(f"Failed to parse response: {e}")

        # Extract bike info from response
        bikes: list[StationBike] = []
        try:
            ebike_list = data["panel"]["component_map"]["EbikeListComponent_0"]["ebike_list"]
            for ebike in ebike_list.get("ebikes", []):
                bike_id = ebike["bike_id"]["text"]["strings"][0]["content"]
                est_range = ebike["est_range"]["text"]["strings"][0]["content"]
                bikes.append(StationBike(bike_id=bike_id, estimated_range=est_range))
        except (KeyError, IndexError):
            # No ebikes at this station or different response structure
            pass

        return bikes

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
