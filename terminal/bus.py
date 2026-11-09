"""In-process publish/subscribe."""
import asyncio


class _Subscription:
    def __init__(self, bus: "Bus", maxsize: int):
        self._bus = bus
        self.queue: asyncio.Queue = asyncio.Queue(maxsize)
        bus._subs.add(self)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    async def aclose(self):
        self._bus._subs.discard(self)


class Bus:
    def __init__(self):
        self._subs: set[_Subscription] = set()

    def publish(self, event) -> None:
        for sub in self._subs:
            if sub.queue.full():
                sub.queue.get_nowait()  # slow reader loses its oldest event
            sub.queue.put_nowait(event)

    def subscribe(self, maxsize: int = 1000) -> _Subscription:
        return _Subscription(self, maxsize)
