"""Read-only MCP transport smoke test against the configured database.

Run: python -m scripts.mcp_smoke
"""
import json

from backend.mcp_client import MCPClient


def main():
    client = MCPClient("shop_01")
    try:
        tools = client.request("tools/list")
        print("MCP tools:", len(tools["tools"]))
        print("Tool names:", ", ".join(tool["name"] for tool in tools["tools"]))
        summary = client.call("catalog_summary", {})
        print("Catalog summary:", json.dumps(summary, ensure_ascii=False))
        products = client.call("catalog_search", {"query": "gaming keyboard", "limit": 2})
        print("Exact catalogue hit:", products[0]["product_id"], products[0]["name"])
        policies = client.call("policies_search", {"query": "refund policy", "limit": 1})
        print("Policy hit:", policies[0]["policy_type"])
    finally:
        client.close()


if __name__ == "__main__":
    main()
