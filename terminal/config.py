"""Settings from .env / environment. Missing keys are empty strings."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    finnhub_key: str
    etherscan_key: str
    sec_user_agent: str
    db_path: str = "terminal.db"
    host: str = "127.0.0.1"
    port: int = 8787


def load_config() -> Config:
    load_dotenv()
    env = os.environ.get
    return Config(
        finnhub_key=env("FINNHUB_KEY", "").strip(),
        etherscan_key=env("ETHERSCAN_KEY", "").strip(),
        sec_user_agent=env("SEC_USER_AGENT", "").strip(),
        db_path=env("TERMINAL_DB", "terminal.db"),
        port=int(env("TERMINAL_PORT", "8787")),
    )
