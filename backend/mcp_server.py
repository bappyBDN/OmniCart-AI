"""Minimal MCP stdio server exposing the shop's read-only database tools.

Run directly:
    python -m backend.mcp_server

MCP clients speak newline-delimited JSON-RPC 2.0 over stdin/stdout. The server
does not write business data; admin/customer writes remain in the authenticated
FastAPI service. Every query is scoped to MCP_SHOP_ID (or SHOP_ID).
"""
import json
import os
import sys
import traceback

from backend.gateway import LocalGateway, TOOL_SCHEMAS, call_tool

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "omnichat-database", "version": "1.0.0"}


def _response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_request(request, gateway=None):
    """Pure dispatcher used by the stdio loop and by protocol tests."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC request")
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    gateway = gateway or LocalGateway(os.getenv("MCP_SHOP_ID") or os.getenv("SHOP_ID", "shop_01"))
    if method == "initialize":
        return _response(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": TOOL_SCHEMAS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments, gateway=gateway)
            return _response(request_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            })
        except Exception as exc:
            # Return a safe tool error; never include credentials or SQL.
            message = str(exc) if isinstance(exc, ValueError) else "Tool execution failed safely."
            return _response(request_id, {
                "content": [{"type": "text", "text": message}],
                "isError": True,
            })
    return _error(request_id, -32601, "Method not found")


def main():
    gateway = LocalGateway(os.getenv("MCP_SHOP_ID") or os.getenv("SHOP_ID", "shop_01"))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request, gateway=gateway)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            error = _error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            error = _error(None, -32603, "Internal error")
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
