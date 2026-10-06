"""Print a line for every puff taken on a Venty / Veazy, across reconnects."""

import asyncio

from storz_bickel_ble import monitor


async def main():
    total = 0   # puffs in the current heating session, across reconnects
    seen = 0    # puffs already counted on the current connection
    async for state in monitor():
        if state is None:
            seen = 0
            print("device offline, searching...")
            continue
        if state.puffs > seen:
            total += state.puffs - seen
            print(f"puff #{total} at {state.target_temp:g}°C, battery {state.battery}%")
        seen = state.puffs
        if not state.heater_on and total:
            print(f"session over: {total} puffs")
            total = 0


asyncio.run(main())
