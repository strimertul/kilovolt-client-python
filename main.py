#!/usr/bin/env python

import asyncio
import os
from kilovolt.connection import KilovoltClient

async def connect():
    kv = KilovoltClient("ws://localhost:4337/ws", os.getenv("KILOVOLT_PASSWORD"))
    await kv.connect()

asyncio.run(connect())