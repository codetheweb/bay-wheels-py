"""Pydantic models for Bay Wheels API responses."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field, computed_field


class Station(BaseModel):
    """A Bay Wheels bike station."""

    id: str = Field(description="Unique station identifier")
    name: str | None = Field(default=None, description="Human-readable station name")
    coordinates: tuple[float, float] = Field(
        description="Station coordinates as (longitude, latitude)"
    )
    ebikes_available: int = Field(default=0, description="Number of e-bikes available")
    bikes_available: int = Field(
        default=0, description="Number of regular bikes available"
    )
    docks_available: int = Field(default=0, description="Number of empty docks")
    scooters_available: int = Field(default=0, description="Number of scooters available")
    is_offline: bool = Field(default=False, description="Whether the station is offline")
    is_valet: bool = Field(default=False, description="Whether this is a valet station")

    @classmethod
    def from_geojson_feature(cls, feature: dict) -> Station:
        """Create a Station from a GeoJSON feature."""
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", [0, 0])

        return cls(
            id=props.get("map_item_id", ""),
            name=props.get("name"),
            coordinates=(coords[0], coords[1]),
            ebikes_available=props.get("ebikes_available", 0),
            bikes_available=props.get("bikes_available", 0),
            docks_available=props.get("docks_available", 0),
            scooters_available=props.get("scooters_available", 0),
            is_offline=props.get("is_offline", False),
            is_valet=props.get("is_valet", False),
        )


class Reservation(BaseModel):
    """A bike reservation."""

    ride_id: str = Field(description="Unique ride/reservation identifier")
    status: str = Field(description="Reservation status (e.g., 'reserved', 'cancelled')")
    station_id: str | None = Field(
        default=None, description="ID of the station where the bike is reserved"
    )
    bike_id: str | None = Field(
        default=None, description="ID of the reserved bike, if assigned"
    )


class TokenInfo(BaseModel):
    """OAuth2 token information."""

    access_token: str = Field(description="OAuth2 access token")
    refresh_token: str | None = Field(
        default=None, description="OAuth2 refresh token"
    )
    expires_at: float | None = Field(
        default=None, description="Token expiration timestamp"
    )
    token_type: str = Field(default="Bearer", description="Token type")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expires_in_seconds(self) -> int | None:
        """Get the number of seconds until the token expires."""
        if self.expires_at is None:
            return None
        remaining = self.expires_at - time.time()
        return max(0, int(remaining))


class StationBike(BaseModel):
    """An e-bike at a station with range info."""

    bike_id: str = Field(description="Bike identifier (e.g., '677-6512')")
    estimated_range_raw: str = Field(description="Estimated range as returned by API (e.g., '30 mi')")
    estimated_range_miles: int | None = Field(
        default=None, description="Estimated range in miles, parsed from raw value"
    )
