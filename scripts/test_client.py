#!/usr/bin/env python3
"""Interactive test script for the Bay Wheels client."""

from __future__ import annotations

import argparse
import asyncio
import sys

from bay_wheels import BayWheelsClient, AuthenticationError


async def authenticate(client: BayWheelsClient) -> bool:
    """Handle authentication flow.

    Returns:
        True if authentication succeeded, False otherwise.
    """
    # Try to load existing token
    token_info = client.load_token()
    if token_info is not None:
        print(f"Loaded saved token: {token_info.access_token[:20]}...")
        return True

    # Need to authenticate
    print("\nNo saved token found. Starting authentication flow...")
    phone = input("Enter phone number (E.164 format, e.g., +14155551234): ").strip()

    if not phone.startswith("+"):
        print("Phone number must start with + and include country code")
        return False

    try:
        print(f"Requesting verification code for {phone}...")
        await client.request_code(phone)
        print("Verification code sent!")

        code = input("Enter verification code: ").strip()

        # First attempt without email
        try:
            access_token = await client.login(phone, code)
            print(f"Logged in successfully! Token: {access_token[:20]}...")
            return True
        except AuthenticationError as e:
            # Check if email verification is required
            if "Email verification required" in str(e):
                print(f"\n{e}")
                email = input("Enter your email address: ").strip()
                access_token = await client.login(phone, code, email=email)
                print(f"Logged in successfully! Token: {access_token[:20]}...")
                return True
            raise

    except AuthenticationError as e:
        print(f"Authentication failed: {e}")
        return False


async def list_stations(client: BayWheelsClient) -> None:
    """List all stations with availability."""
    print("\nFetching stations...")
    try:
        stations = await client.list_stations()
        print(f"Found {len(stations)} stations\n")

        # Sort by total bikes available
        stations.sort(
            key=lambda s: s.ebikes_available + s.bikes_available, reverse=True
        )

        # Show top 20 stations with bikes
        print("Top 20 stations with bikes available:")
        print("-" * 80)
        print(f"{'Station ID':<50} {'E-Bikes':>8} {'Bikes':>8} {'Docks':>8}")
        print("-" * 80)

        for station in stations[:20]:
            name = station.name or station.id[-30:]
            print(
                f"{name:<50} {station.ebikes_available:>8} "
                f"{station.bikes_available:>8} {station.docks_available:>8}"
            )

    except Exception as e:
        print(f"Failed to list stations: {e}")


async def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Test the Bay Wheels client")
    parser.add_argument(
        "--clear-token",
        action="store_true",
        help="Clear saved token and re-authenticate",
    )
    args = parser.parse_args()

    async with BayWheelsClient() as client:
        if args.clear_token:
            print("Clearing saved token...")
            client.clear_token()

        if not await authenticate(client):
            return 1

        await list_stations(client)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
