"""Turn what the user types into one canonical symbol plus each provider's spelling."""
import re
from dataclasses import dataclass

CRYPTO = frozenset(
    "BTC ETH USDT BNB SOL XRP USDC DOGE ADA TRX AVAX SHIB TON LINK DOT BCH LTC NEAR MATIC POL "
    "UNI ICP APT ETC XLM XMR ATOM FIL HBAR ARB OP INJ SUI AAVE MKR ALGO VET RNDR GRT STX IMX "
    "FTM SAND MANA EOS XTZ THETA FLOW PEPE TIA SEI HYPE".split()
)
FIAT = frozenset("USD EUR GBP JPY CHF AUD CAD NZD CNY SEK NOK MXN".split())
_INDEX_PASSTHROUGH = frozenset({"DX-Y.NYB"})
_VALID = re.compile(r"^[A-Z0-9.^=\-/]{1,15}$")
_QUOTES = ("USDT", "USDC", "USD")


@dataclass(frozen=True)
class Symbol:
    id: str
    asset: str  # stock | crypto | fx | index | future
    yahoo: str
    binance: str = ""
    coinbase: str = ""
    kraken: str = ""
    finnhub: str = ""


def _crypto_base(text: str) -> str | None:
    flat = text.replace("-", "").replace("/", "")
    if flat in CRYPTO:
        return flat
    for quote in _QUOTES:
        if flat.endswith(quote) and flat[: -len(quote)] in CRYPTO:
            return flat[: -len(quote)]
    return None


def resolve(text: str) -> Symbol | None:
    text = text.strip().upper().removeprefix("$")
    if not _VALID.match(text):
        return None
    if text.startswith("^") or text in _INDEX_PASSTHROUGH:
        return Symbol(text, "index", text)
    if text.endswith("=F"):
        return Symbol(text, "future", text)
    flat = text.replace("/", "").removesuffix("=X")
    if len(flat) == 6 and flat[:3] in FIAT and flat[3:] in FIAT:
        return Symbol(flat, "fx", flat + "=X")
    if base := _crypto_base(text):
        pair = f"{base}-USD"
        kraken = "XBT/USD" if base == "BTC" else f"{base}/USD"
        return Symbol(pair, "crypto", pair, f"{base}USDT", pair, kraken)
    if "/" in text or "=" in text or "^" in text:
        return None
    # Yahoo writes share classes with a dash (BRK-B) but keeps exchange suffixes (ENI.MI)
    yahoo = re.sub(r"\.([A-Z])$", r"-\1", text)
    return Symbol(text, "stock", yahoo, finnhub=text)
