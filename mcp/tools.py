import subprocess
import json

def call_mcp_tool(tool_name: str, **kwargs) -> dict:
    payload = json.dumps({"tool": tool_name, "arguments": kwargs})
    result = subprocess.run(
        ["python", "mcp/zerodha_mcp.py"],
        input=payload, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"MCP tool error: {result.stderr}")
    return json.loads(result.stdout)

def get_holdings() -> list[dict]:
    return call_mcp_tool("get_holdings")

def get_positions() -> dict:
    return call_mcp_tool("get_positions")

def get_quote(symbol: str) -> dict:
    return call_mcp_tool("get_quote", symbol=symbol)

def place_gtt(symbol: str, trigger_price: float,
              limit_price: float, quantity: int,
              transaction_type: str = "SELL") -> dict:
    return call_mcp_tool("place_gtt", symbol=symbol,
        trigger_price=trigger_price, limit_price=limit_price,
        quantity=quantity, transaction_type=transaction_type)