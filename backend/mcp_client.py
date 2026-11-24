"""Optional MCP transport for Graph RAG.

The application uses the local gateway by default because it keeps one
transaction and avoids process overhead. Set MCP_ENABLED=true to route the
same typed tools through the stdio MCP server instead. A transport failure
falls back to the local gateway rather than failing a customer turn.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

from backend.config import ROOT, settings
from backend.gateway import LocalGateway

log = logging.getLogger(__name__)
_EOF = object()
_gateway = None
_gateway_lock = threading.Lock()


class MCPClient:
    def __init__(self, shop_id: str, timeout: float = 20):
        self.shop_id = shop_id
        self.timeout = timeout
        self._id = 0
        self._responses = queue.Queue()
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
            "DATABASE_URL": settings.database_url,
            "SHOP_ID": shop_id,
            "MCP_SHOP_ID": shop_id,
        })
        self.process = subprocess.Popen(
            [sys.executable, "-m", "backend.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=str(ROOT), env=env,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "omnichat-api", "version": "1"}})

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self._responses.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self._responses.put(_EOF)

    def request(self, method, params=None):
        self._id += 1
        request_id = self._id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                response = self._responses.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("MCP response timed out")
            if response is _EOF:
                raise RuntimeError("MCP server stopped")
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(response["error"].get("message", "MCP error"))
            return response.get("result", {})

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        if result.get("isError"):
            raise RuntimeError(content[0].get("text", "MCP tool error") if content else "MCP tool error")
        if not content:
            return None
        return json.loads(content[0]["text"])

    def close(self):
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except Exception:
            self.process.kill()


class MCPGateway:
    def __init__(self, shop_id: str):
        self.shop_id = shop_id
        self.client = MCPClient(shop_id)

    def _call(self, name, **arguments):
        return self.client.call(name, arguments)

    def catalog_summary(self, category=None):
        return self._call("catalog_summary", category=category)

    def search_products(self, query="", category=None, color=None, size=None, max_price=None,
                        min_price=None, in_stock=None, limit=10):
        return self._call("catalog_search", query=query, category=category, color=color, size=size,
                          max_price=max_price, min_price=min_price, in_stock=in_stock, limit=limit)

    def get_product(self, product_id):
        return self._call("catalog_get", product_id=product_id)

    def search_policies(self, query="", limit=10):
        return self._call("policies_search", query=query, limit=limit)

    def get_policy(self, policy_id):
        return self._call("policy_get", policy_id=policy_id)

    def list_knowledge(self, limit=20):
        return self._call("knowledge_list", limit=limit)

    def customer_profile(self, customer_id):
        return self._call("customer_get", customer_id=customer_id)

    def customer_orders(self, customer_id, limit=20):
        return self._call("customer_orders", customer_id=customer_id, limit=limit)

    def recent_messages(self, customer_id, limit=20):
        return self._call("customer_messages", customer_id=customer_id, limit=limit)

    def recent_orders(self, limit=20):
        return self._call("orders_recent", limit=limit)

    def get_order(self, order_id=None, reference=None):
        return self._call("order_get", **{key: value for key, value in {"order_id": order_id, "reference": reference}.items() if value is not None})

    def support_cases(self, status=None, limit=20):
        return self._call("support_cases", status=status, limit=limit)

    def metrics_summary(self):
        return self._call("metrics_summary")


def get_gateway(shop_id: str):
    """Return a cached MCP gateway when configured, otherwise local SQL."""
    global _gateway
    if not settings.mcp_enabled:
        return LocalGateway(shop_id)
    with _gateway_lock:
        if _gateway is None:
            try:
                _gateway = MCPGateway(shop_id)
            except Exception:
                log.warning("MCP transport unavailable; using the local database gateway")
                _gateway = LocalGateway(shop_id)
    return _gateway
