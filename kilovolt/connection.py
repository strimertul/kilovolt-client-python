import asyncio
from typing import Callable
import websockets
import json
import os
import hmac
import hashlib
import base64

tbl = bytes.maketrans(bytearray(range(256)), bytearray([ord(b'A') + b % 50 for b in range(256)]))


def generate_rid():
    return os.urandom(16).translate(tbl).decode("utf-8")


class KilovoltClient:
    def __init__(self, url: str = "ws://localhost:4337/ws", password: str = None):
        """
        Create new Kilovolt client
        :param url: Kilovolt server endpoint
        :param password: Optional password for password-protected instances
        """

        self.url = url
        self.password = password
        self.websocket = None
        self.__tasks = set[asyncio.Task]()
        self.__pending = dict[str, asyncio.Future]()
        self.__key_subscriptions = dict[str, set[Callable]]()
        self.__prefix_subscriptions = dict[str, set[Callable]]()
        self.version = None
        self.connected = False

    async def __read_task(self):
        async for message in self.websocket:
            data = json.loads(message)
            if "request_id" in data:
                if data["request_id"] in self.__pending:
                    self.__pending[data["request_id"]].set_result(data)
                else:
                    # Better logging
                    print("received response for a weird request_id!")
                continue
            if not data["type"]:
                continue
            match data["type"]:
                case "hello":
                    self.version = data["version"]
                    self.connected = True
                case "push":
                    key: str = data["key"]
                    # Check for key subscribers
                    if key in self.__key_subscriptions:
                        for func in self.__key_subscriptions[key]:
                            func(key, data["new_value"])
                    # Check for prefix subscribers
                    for prefix in self.__prefix_subscriptions.keys():
                        if key.startswith(prefix):
                            for func in self.__prefix_subscriptions[prefix]:
                                func(key, data["new_value"])
                case _:
                    # Better logging
                    print(data)

    async def connect(self):
        self.websocket = await websockets.connect("ws://localhost:4337/ws")
        read_task = asyncio.create_task(self.__read_task())
        self.__tasks.add(read_task)
        read_task.add_done_callback(self.__tasks.discard)
        if self.password is not None:
            await self.__auth()

    async def send(self, data: dict) -> dict:
        """
        Send a command to the server
        :param data: command to send
        :return: received response or error
        """

        # Add request id
        request_id = generate_rid()
        message = data.copy()
        message["request_id"] = request_id

        # Encode to JSON and send over the wire
        await self.websocket.send(json.dumps(message))

        # Create future for when we get the reply
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.__pending[request_id] = fut
        response = await fut
        return response

    async def get(self, key: str) -> str:
        """
        Read key from kilovolt as bare string
        :param key: key to read
        :return: key contents as string, or empty string if unset
        """
        response = await self.send({"command": "kget", "data": {"key": key}})
        if not response["ok"]:
            raise ValueError(response["error"])
        return response["data"]

    async def get_multiple(self, keys: list[str]) -> dict[str, str]:
        """
        Read keys from kilovolt as bare string
        :param keys: list of keys to read
        :return: dictionary of the requested keys as key=value
        """
        response = await self.send({"command": "kget-bulk", "data": {"keys": keys}})
        if not response["ok"]:
            raise ValueError(response["error"])
        return response["data"]

    async def get_prefix(self, prefix: str) -> dict[str, str]:
        """
        Get all keys with a given prefix
        :param prefix: prefix of keys to read
        :return: dictionary of key/values with the given prefix
        """
        response = await self.send({"command": "kget-all", "data": {"prefix": prefix}})
        if not response["ok"]:
            raise ValueError(response["error"])
        return response["data"]

    async def get_json(self, key: str) -> str:
        """
        Read key from kilovolt as JSON object
        :param key: key to read
        :return: key contents as dictionary, or error if it's not a valid object
        """
        return json.loads(await self.get(key))

    async def list(self, prefix: str = "") -> list[str]:
        """
        List all keys (with optional prefix)
        :param prefix: optional prefix to filter keys
        :return: list of keys
        """
        response = await self.send({"command": "klist", "data": {"prefix": prefix}})
        if not response["ok"]:
            raise ValueError(response["error"])
        return response["data"]

    async def set(self, key: str, value: str):
        """
        Write key to kilovolt as bare string
        :param key: key to write
        :param value: string value to write
        """
        response = await self.send({"command": "kset", "data": {"key": key, "data": value}})
        if not response["ok"]:
            raise ValueError(response["error"])

    async def set_multiple(self, values: dict[str, str]):
        """
        Write multiple keys at once
        :param values: dictionary as key=value of values to set
        """
        response = await self.send({"command": "kset-bulk", "data": values})
        if not response["ok"]:
            raise ValueError(response["error"])

    async def set_json(self, key: str, value: object):
        """
        Write key to kilovolt as JSON object
        :param key: key to write
        :param value: object to save as JSON value
        """
        await self.set(key, json.dumps(value))

    async def subscribe(self, key: str, callback: Callable[[str, str], None]):
        """
        Subscribe to key changes by providing a callback to be called when
        the key changes
        :param key: key to watch for changes
        :param callback: callback to call (`lambda key,value:`)
        """
        # If we don't have the subscription already, subscribe on server
        if key not in self.__key_subscriptions:
            await self.send({"command": "ksub", "data": {"key": key}})
            self.__key_subscriptions[key] = set()
        # Add to soft subscription
        self.__key_subscriptions[key].add(callback)

    async def unsubscribe(self, key: str, callback: Callable[[str, str], None]):
        """
        Remove subscription to key
        :param key: key to stop watching for changes
        :param callback: callback to unsubscribe
        :return:
        """
        # Return error is key is not subscribed
        if key not in self.__key_subscriptions:
            raise ValueError("key not subscribed")
        # Remove key from soft subscription
        self.__key_subscriptions[key].remove(callback)
        # If last subscriber, unsub from server
        if len(self.__key_subscriptions) < 1:
            await self.send({"command": "kunsub", "data": {"key": key}})
            del self.__key_subscriptions[key]

    async def subscribe_prefix(self, prefix: str, callback: Callable[[str, str], None]):
        """
        Subscribe to changes of keys with a given prefix by providing a callback
        to be called when one of those keys changes
        :param prefix: prefix of keys to watch for changes
        :param callback: callback to call (`lambda key,value:`)
        """
        # If we don't have the subscription already, subscribe on server
        if prefix not in self.__prefix_subscriptions:
            await self.send({"command": "ksub-prefix", "data": {"prefix": prefix}})
            self.__prefix_subscriptions[prefix] = set()
        # Add to soft subscription
        self.__prefix_subscriptions[prefix].add(callback)

    async def unsubscribe_prefix(self, prefix: str, callback: Callable[[str, str], None]):
        """
        Remove subscription to prefix
        :param prefix: prefix of keys to stop watching for changes
        :param callback: callback to unsubscribe
        :return:
        """
        # Return error is prefix is not subscribed
        if prefix not in self.__prefix_subscriptions:
            raise ValueError("prefix not subscribed")
        # Remove prefix from soft subscription
        self.__prefix_subscriptions[prefix].remove(callback)
        # If last subscriber, unsub from server
        if len(self.__prefix_subscriptions) < 1:
            await self.send({"command": "kunsub-prefix", "data": {"prefix": prefix}})
            del self.__prefix_subscriptions[prefix]

    async def __auth(self):
        auth_challenge = await self.send({"command": "klogin"})
        # Decode challenge and salt
        salt = base64.b64decode(auth_challenge["data"]["salt"])
        challenge = base64.b64decode(auth_challenge["data"]["challenge"])
        # Sign with HMAC-256
        signed = hmac.new(bytes(self.password, "utf-8") + salt, msg=challenge, digestmod=hashlib.sha256).digest()
        # Send challenge response
        response = await self.send({"command": "kauth", "data": {"hash": base64.b64encode(signed).decode("utf-8")}})
        if not response["ok"]:
            raise ValueError(response["error"])
