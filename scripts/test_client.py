#!/usr/bin/env python3
"""CLI for the Bay Wheels client."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from bay_wheels import AuthenticationError, BayWheelsClient, TokenInfo

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "bay_wheels" / "config.json"


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Calculate distance between two points in miles using Haversine formula."""
    R = 3959  # Earth's radius in miles

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def load_token(config_path: Path) -> TokenInfo | None:
    """Load token from config file.

    Args:
        config_path: Path to the config file.

    Returns:
        The loaded token info, or None if not found or invalid.
    """
    if not config_path.exists():
        return None

    try:
        data = json.loads(config_path.read_text())
        return TokenInfo(**data)
    except (json.JSONDecodeError, ValueError):
        return None


def save_token(token_info: TokenInfo, config_path: Path) -> None:
    """Save token to config file.

    Args:
        token_info: The token info to save.
        config_path: Path to the config file.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(token_info.model_dump(), indent=2))


def clear_token(config_path: Path) -> None:
    """Clear the saved token file.

    Args:
        config_path: Path to the config file.
    """
    if config_path.exists():
        config_path.unlink()


async def cmd_login(args: argparse.Namespace) -> int:
    """Handle the login command."""
    config_path = Path(args.config)

    # Check for existing token
    token_info = load_token(config_path)
    if token_info is not None:
        print(f"Already logged in (token: {token_info.access_token[:20]}...)")
        print("Use --force to re-authenticate.")
        if not getattr(args, "force", False):
            return 0
        print("Re-authenticating...")

    async with BayWheelsClient() as client:
        phone = input("Enter phone number (E.164 format, e.g., +14155551234): ").strip()

        if not phone.startswith("+"):
            print("Phone number must start with + and include country code")
            return 1

        try:
            print(f"Requesting verification code for {phone}...")
            await client.request_code(phone)
            print("Verification code sent!")

            code = input("Enter verification code: ").strip()

            # First attempt without email
            try:
                token_info = await client.login(phone, code)
            except AuthenticationError as e:
                # Check if email verification is required
                if "Email verification required" in str(e):
                    print(f"\n{e}")
                    email = input("Enter your email address: ").strip()
                    token_info = await client.login(phone, code, email=email)
                else:
                    raise

            save_token(token_info, config_path)
            print(f"Logged in successfully! Token saved to {config_path}")
            return 0

        except AuthenticationError as e:
            print(f"Authentication failed: {e}")
            return 1


async def cmd_list_stations(args: argparse.Namespace) -> int:
    """Handle the list-stations command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in. Run 'login' first.")
        return 1

    # Check --nearby requires both --lat and --lon
    nearby_mode = args.lat is not None and args.lon is not None
    if (args.lat is None) != (args.lon is None):
        print("Error: --lat and --lon must be provided together.")
        return 1

    async with BayWheelsClient(token_info=token_info) as client:
        try:
            print("Fetching stations...")
            stations = await client.list_stations()
            print(f"Found {len(stations)} stations\n")

            if nearby_mode:
                user_lat, user_lon = args.lat, args.lon
                # Calculate distance for each station and sort by distance
                stations_with_dist = []
                for s in stations:
                    # Station coordinates are (lon, lat)
                    dist = haversine_distance(user_lat, user_lon, s.coordinates[1], s.coordinates[0])
                    stations_with_dist.append((s, dist))
                stations_with_dist.sort(key=lambda x: x[1])

                limit = args.n if args.n else 20
                print(f"Nearest {limit} stations:")
                print("-" * 135)
                print(f"{'Name':<40} {'ID':<52} {'Dist':>7} {'E-Bikes':>8} {'Bikes':>8} {'Docks':>8}")
                print("-" * 135)

                for station, dist in stations_with_dist[:limit]:
                    name = (station.name or "Unknown")[:39]
                    print(
                        f"{name:<40} {station.id:<52} {dist:>6.2f}m {station.ebikes_available:>8} "
                        f"{station.bikes_available:>8} {station.docks_available:>8}"
                    )
            else:
                # Sort by total bikes available
                stations.sort(
                    key=lambda s: s.ebikes_available + s.bikes_available, reverse=True
                )

                limit = args.n if args.n else 20
                print(f"Top {limit} stations with bikes available:")
                print("-" * 127)
                print(f"{'Name':<40} {'ID':<52} {'E-Bikes':>8} {'Bikes':>8} {'Docks':>8}")
                print("-" * 127)

                for station in stations[:limit]:
                    name = (station.name or "Unknown")[:39]
                    print(
                        f"{name:<40} {station.id:<52} {station.ebikes_available:>8} "
                        f"{station.bikes_available:>8} {station.docks_available:>8}"
                    )

            return 0

        except AuthenticationError as e:
            print(f"Authentication error: {e}")
            print("Your token may have expired. Run 'login' again.")
            return 1
        except Exception as e:
            print(f"Failed to list stations: {e}")
            return 1


async def cmd_reserve(args: argparse.Namespace) -> int:
    """Handle the reserve command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in. Run 'login' first.")
        return 1

    async with BayWheelsClient(token_info=token_info) as client:
        try:
            print(f"Creating reservation at station {args.station_id}...")
            reservation = await client.create_reservation(
                args.station_id, bike_type=args.type
            )
            print(f"Reservation created!")
            print(f"  Ride ID: {reservation.ride_id}")
            print(f"  Status: {reservation.status}")
            print(f"  Station: {reservation.station_id}")
            if reservation.bike_id:
                print(f"  Bike: {reservation.bike_id}")
            return 0

        except AuthenticationError as e:
            print(f"Authentication error: {e}")
            print("Your token may have expired. Run 'login' again.")
            return 1
        except Exception as e:
            print(f"Failed to create reservation: {e}")
            return 1


async def cmd_cancel(args: argparse.Namespace) -> int:
    """Handle the cancel command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in. Run 'login' first.")
        return 1

    async with BayWheelsClient(token_info=token_info) as client:
        try:
            print(f"Cancelling reservation {args.ride_id}...")
            await client.cancel_reservation(args.ride_id)
            print("Reservation cancelled.")
            return 0

        except AuthenticationError as e:
            print(f"Authentication error: {e}")
            print("Your token may have expired. Run 'login' again.")
            return 1
        except Exception as e:
            print(f"Failed to cancel reservation: {e}")
            return 1


async def cmd_station_bikes(args: argparse.Namespace) -> int:
    """Handle the station-bikes command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in. Run 'login' first.")
        return 1

    async with BayWheelsClient(token_info=token_info) as client:
        try:
            print(f"Fetching bikes at station {args.station_id}...")
            bikes = await client.get_station_bikes(args.station_id)

            if not bikes:
                print("No e-bikes found at this station.")
                return 0

            print(f"\nFound {len(bikes)} e-bike(s):\n")
            print(f"{'Bike ID':<12} | {'Est. Range':<10}")
            print("-" * 25)
            for bike in bikes:
                print(f"{bike.bike_id:<12} | {bike.estimated_range_raw:<10}")

            return 0

        except AuthenticationError as e:
            print(f"Authentication error: {e}")
            print("Your token may have expired. Run 'login' again.")
            return 1
        except Exception as e:
            print(f"Failed to get station bikes: {e}")
            return 1


async def cmd_refresh(args: argparse.Namespace) -> int:
    """Handle the refresh command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in. Run 'login' first.")
        return 1

    if token_info.refresh_token is None:
        print("No refresh token available. Run 'login' again.")
        return 1

    # Show current token status
    print(f"Current token: {token_info.access_token[:20]}...")
    if token_info.expires_in_seconds is not None:
        hours = token_info.expires_in_seconds // 3600
        minutes = (token_info.expires_in_seconds % 3600) // 60
        print(f"Expires in: {hours}h {minutes}m")
    print(f"Is expired: {token_info.is_expired}")

    async with BayWheelsClient(token_info=token_info) as client:
        try:
            print("\nRefreshing token...")
            new_token_info = await client.refresh_token()
            save_token(new_token_info, config_path)
            print(f"Token refreshed successfully!")
            print(f"New token: {new_token_info.access_token[:20]}...")
            if new_token_info.expires_in_seconds is not None:
                hours = new_token_info.expires_in_seconds // 3600
                minutes = (new_token_info.expires_in_seconds % 3600) // 60
                print(f"Expires in: {hours}h {minutes}m")
            return 0

        except AuthenticationError as e:
            print(f"Token refresh failed: {e}")
            print("You may need to run 'login' again.")
            return 1


async def cmd_status(args: argparse.Namespace) -> int:
    """Handle the status command."""
    config_path = Path(args.config)
    token_info = load_token(config_path)

    if token_info is None:
        print("Not logged in.")
        return 0

    print(f"Access token: {token_info.access_token[:20]}...")
    print(f"Refresh token: {'Yes' if token_info.refresh_token else 'No'}")

    if token_info.expires_in_seconds is not None:
        hours = token_info.expires_in_seconds // 3600
        minutes = (token_info.expires_in_seconds % 3600) // 60
        print(f"Expires in: {hours}h {minutes}m")
        print(f"Is expired: {token_info.is_expired}")
    else:
        print("Expiration: Unknown")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Bay Wheels CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # login command
    login_parser = subparsers.add_parser("login", help="Interactive login flow")
    login_parser.add_argument(
        "--force", action="store_true", help="Force re-authentication"
    )

    # list-stations command
    list_parser = subparsers.add_parser(
        "list-stations", help="List stations with availability"
    )
    list_parser.add_argument(
        "-n", type=int, default=20, help="Number of stations to show (default: 20)"
    )
    list_parser.add_argument(
        "--lat", type=float, help="Latitude for nearby search"
    )
    list_parser.add_argument(
        "--lon", type=float, help="Longitude for nearby search"
    )

    # reserve command
    reserve_parser = subparsers.add_parser(
        "reserve", help="Create a reservation at a station"
    )
    reserve_parser.add_argument("station_id", help="Station ID to reserve from")
    reserve_parser.add_argument(
        "--type",
        choices=["ebike", "bike"],
        default="ebike",
        help="Type of bike to reserve (default: ebike)",
    )

    # cancel command
    cancel_parser = subparsers.add_parser("cancel", help="Cancel a reservation")
    cancel_parser.add_argument("ride_id", help="Ride ID to cancel")

    # station-bikes command
    bikes_parser = subparsers.add_parser(
        "station-bikes", help="List e-bikes at a station with range"
    )
    bikes_parser.add_argument("station_id", help="Station ID")

    # refresh command
    subparsers.add_parser("refresh", help="Refresh the access token")

    # status command
    subparsers.add_parser("status", help="Show current token status")

    args = parser.parse_args()

    # Dispatch to appropriate command handler
    if args.command == "login":
        return asyncio.run(cmd_login(args))
    elif args.command == "list-stations":
        return asyncio.run(cmd_list_stations(args))
    elif args.command == "reserve":
        return asyncio.run(cmd_reserve(args))
    elif args.command == "cancel":
        return asyncio.run(cmd_cancel(args))
    elif args.command == "station-bikes":
        return asyncio.run(cmd_station_bikes(args))
    elif args.command == "refresh":
        return asyncio.run(cmd_refresh(args))
    elif args.command == "status":
        return asyncio.run(cmd_status(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
