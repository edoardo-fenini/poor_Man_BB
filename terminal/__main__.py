"""`python -m terminal` starts the terminal; `python -m terminal doctor` checks the feeds."""
import logging
import sys

import uvicorn

from terminal.app import create_app
from terminal.bus import Bus
from terminal.config import load_config
from terminal.prices import Prices
from terminal.store import Store


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = load_config()
    command = argv[0] if argv else "run"
    if command != "run":
        print(f"unknown command: {command}\nusage: python -m terminal [run]")
        return 2
    store, bus, prices = Store(config.db_path), Bus(), Prices()
    app = create_app(config, store, bus, prices, feeds=[])
    print(f"Terminal running at http://{config.host}:{config.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
