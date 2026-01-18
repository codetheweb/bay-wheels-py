# bay-wheels

An unofficial Python client for the Bay Wheels bike-share API. Supports authentication, station lookup, bike availability, and reservations. Mostly vibe coded; use at your own risk.

## Installation

```bash
pip install bay-wheels
```

## Usage

```python
import asyncio
from bay_wheels import BayWheelsClient

async def main():
    async with BayWheelsClient() as client:
        # Authenticate via SMS
        await client.request_code("+14155551234")
        code = input("Enter code: ")
        await client.login("+14155551234", code)

        # List stations
        stations = await client.list_stations()
        for station in stations[:5]:
            print(f"{station.name}: {station.ebikes_available} e-bikes")

        # Get bikes at a station
        bikes = await client.get_station_bikes(stations[0].id)
        for bike in bikes:
            print(f"  Bike {bike.bike_id}: {bike.estimated_range_miles} mi")

        # Reserve a bike
        reservation = await client.create_reservation(stations[0].id)
        print(f"Reserved: {reservation.ride_id}")

        # Cancel reservation
        await client.cancel_reservation(reservation.ride_id)

asyncio.run(main())
```

## License

MIT
