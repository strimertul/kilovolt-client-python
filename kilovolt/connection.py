import asyncio
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
        self.tasks = set()
        self.pending = dict()
        self.version = None
        self.connected = False

    async def __read_task(self):
        async for message in self.websocket:
            data = json.loads(message)
            if "request_id" in data:
                if data["request_id"] in self.pending:
                    self.pending[data["request_id"]].set_result(data)
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
                case _:
                    # Better logging
                    print(data)

    async def connect(self):
        self.websocket = await websockets.connect("ws://localhost:4337/ws")
        read_task = asyncio.create_task(self.__read_task())
        self.tasks.add(read_task)
        read_task.add_done_callback(self.tasks.discard)
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
        self.pending[request_id] = fut
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

    async def get_json(self, key: str) -> str:
        """
        Read key from kilovolt as JSON object
        :param key: key to read
        :return: key contents as dictionary, or error if it's not a valid object
        """
        return json.loads(await self.get(key))

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
