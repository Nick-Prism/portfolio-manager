import os
from kiteconnect import KiteConnect
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("zerodha")
kite = KiteConnect(api_key=os.environ.get("ZERODHA_API_KEY", ""))

@mcp.tool()
def get_holdings() -> list[dict]:
    """Fetch live portfolio holdings from Zerodha."""
    return kite.holdings()

@mcp.tool()
def get_positions() -> dict:
    """Fetch current day positions."""
    return kite.positions()

@mcp.tool()
def place_gtt(
    symbol: str,
    trigger_price: float,
    limit_price: float,
    quantity: int,
    transaction_type: str
) -> dict:
    """Place a GTT (Good Till Triggered) order on Zerodha."""
    return kite.place_gtt(
        trigger_type=kite.GTT_TYPE_SINGLE,
        tradingsymbol=symbol,
        exchange="NSE",
        trigger_values=[trigger_price],
        last_price=limit_price,
        orders=[{
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_LIMIT,
            "product": kite.PRODUCT_CNC,
            "price": limit_price,
        }]
    )

@mcp.tool()
def get_quote(symbol: str) -> dict:
    """Get live quote for a symbol."""
    return kite.quote(f"NSE:{symbol}")

if __name__ == "__main__":
    mcp.run(transport="stdio")