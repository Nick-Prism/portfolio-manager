import os
from kiteconnect import KiteConnect

_kite: KiteConnect | None = None
_last_token: str = ""


def _get_kite() -> KiteConnect:
    global _kite, _last_token
    api_key = os.environ.get("ZERODHA_API_KEY", "")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN", "")
    if _kite is None or access_token != _last_token:
        _kite = KiteConnect(api_key=api_key)
        if access_token:
            _kite.set_access_token(access_token)
        _last_token = access_token
    return _kite


def get_holdings() -> list[dict]:
    """Fetch live portfolio holdings from Zerodha."""
    return _get_kite().holdings()


def get_positions() -> dict:
    """Fetch current day positions."""
    return _get_kite().positions()


def get_quote(symbol: str) -> dict:
    """Get live quote for a symbol."""
    return _get_kite().quote(f"NSE:{symbol}")


def place_gtt(
    symbol: str,
    trigger_price: float,
    limit_price: float,
    quantity: int,
    transaction_type: str = "SELL",
    exchange: str = "NSE",
) -> dict:
    """Place a GTT order on the specified exchange (NSE or BSE)."""
    if os.environ.get("ZERODHA_ENABLE_ORDER_PLACEMENT", "false").lower() != "true":
        raise RuntimeError(
            "GTT order placement is disabled. "
            "Set ZERODHA_ENABLE_ORDER_PLACEMENT=true in .env only when running "
            "from a SEBI-registered static IP (required from April 1, 2026)."
        )
    kite = _get_kite()
    return kite.place_gtt(
        trigger_type=kite.GTT_TYPE_SINGLE,
        tradingsymbol=symbol,
        exchange=exchange.upper(),
        trigger_values=[trigger_price],
        last_price=limit_price,
        orders=[{
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_LIMIT,
            "product": kite.PRODUCT_CNC,
            "price": limit_price,
        }],
    )
