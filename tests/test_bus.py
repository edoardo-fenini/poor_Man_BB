import asyncio

from terminal.bus import Bus
from terminal.events import Tick


async def test_subscriber_receives_published_event():
    bus = Bus()
    sub = bus.subscribe()
    t = asyncio.create_task(anext(sub))
    await asyncio.sleep(0)
    bus.publish(Tick("BTC-USD", 1.0, 2.0, 3.0, "x"))
    assert (await t).symbol == "BTC-USD"


async def test_full_subscriber_drops_oldest_not_blocks():
    bus = Bus()
    sub = bus.subscribe(maxsize=2)
    t = asyncio.create_task(anext(sub))
    await asyncio.sleep(0)
    for i in range(5):
        bus.publish(Tick("A", float(i), 0, 0, "x"))  # must not raise or block
    # only the newest two survive
    assert (await t).price == 3.0
    assert (await anext(sub)).price == 4.0
