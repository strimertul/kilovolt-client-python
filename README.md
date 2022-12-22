# Kilovolt client

Python client for Kilovolt servers, supports Kilovolt Protocol v9+

## Getting started

`pip install kilovolt` (on [PyPI](https://pypi.org/project/kilovolt/))

## Example usage

```python
import asyncio
import os
from kilovolt import KilovoltClient


async def connect():
    kv = KilovoltClient("ws://localhost:4337/ws", os.getenv("KILOVOLT_PASSWORD"))
    await kv.connect()

    # Subscribe to key
    await kv.subscribe("twitch/ev/message", lambda key, val: print(val))
    
    # List all keys that begin with "twitch"
    print(await kv.list("twitch"))

asyncio.run(connect())
```

## LICENSE

Kilovolt client is licensed under ISC, see `LICENSE` for more details.