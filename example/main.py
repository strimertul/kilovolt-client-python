#!/usr/bin/env python

import asyncio
import os
from kilovolt import KilovoltClient


async def connect():
    kv = KilovoltClient("ws://localhost:4337/ws", os.getenv("KILOVOLT_PASSWORD"))
    await kv.connect()
    await kv.subscribe_prefix("twitch", lambda key, val: print(val))
    print(await kv.list("twitch"))

asyncio.run(connect())
