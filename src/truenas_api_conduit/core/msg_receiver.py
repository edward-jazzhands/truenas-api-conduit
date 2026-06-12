import asyncio


class MessageReceiver:

    def __init__(self) -> None:
        self.event: asyncio.Event = asyncio.Event()
        self.messages: list[str] = []
        self.lock = asyncio.Lock()

    async def __call__(self, message: str) -> None:
        self.messages.append(message)
        self.event.set()

    def __await__(self):
        return self.event.wait().__await__()

    def drain(self) -> list[str]:
        msgs = self.messages.copy()
        self.event.clear()
        self.messages.clear()
        return msgs
