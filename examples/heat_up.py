"""Set a temperature, start heating and wait until the device is ready."""

import asyncio

from storz_bickel_ble import connect


async def main():
    async with connect() as vap:
        await vap.set_temperature(190)
        await vap.heater_on()
        print(f"heating {vap.family.display_name} to 190°C...")
        state = await vap.wait_for(lambda s: s.setpoint_reached, timeout=300)
        print(f"ready, battery {state.battery}%")


asyncio.run(main())
