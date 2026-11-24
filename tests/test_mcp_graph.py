import json

from backend import graph_rag
from backend.mcp_server import handle_request


def _mcp_call(name, arguments):
    response = handle_request({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}})
    assert "result" in response
    result = response["result"]
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])


def test_mcp_initialize_and_tools_are_discoverable(store):
    initialized = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    assert initialized["result"]["serverInfo"]["name"] == "omnichat-database"
    tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"catalog_summary", "catalog_search", "customer_orders", "support_cases", "metrics_summary"} <= names
    assert not any("delete" in name or "write" in name for name in names)


def test_mcp_catalog_and_policy_tools_read_live_sql(store):
    assert _mcp_call("catalog_summary", {})["total_products"] == 100
    products = _mcp_call("catalog_search", {"query": "gaming keyboard", "limit": 3})
    assert products[0]["product_id"] == "SKU_107"
    assert products[0]["stock_count"] == 0
    policy = _mcp_call("policies_search", {"query": "refund policy", "limit": 1})
    assert policy[0]["policy_type"] == "Refund Policy"
    order = _mcp_call("support_cases", {"limit": 1})
    assert isinstance(order, list)


def test_mcp_rejects_unlisted_arguments_and_tools(store):
    bad = handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "catalog_search", "arguments": {"sql": "select * from customers"}}})
    assert bad["result"]["isError"] is True
    unknown = handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                              "params": {"name": "db_query", "arguments": {}}})
    assert unknown["result"]["isError"] is True
    assert "Unknown" in unknown["result"]["content"][0]["text"]


def test_graph_rag_reads_authoritative_row_before_documents(store):
    result = graph_rag.retrieve("SKU_102", "shop_01", "product", 3)
    first = result["sources"][0]
    assert first["id"] == "product:SKU_102"
    assert first["retrieval"] == "graph_sql"
    assert first["graph"]["relation"] == "exact"
    assert first["data"]["stock_count"] == 0
    assert any(item["node"] == "graph.sql" for item in result["trace"])


def test_graph_rag_keeps_specific_unavailable_product_focused(store):
    result = graph_rag.retrieve("Smart Watch Pro 2", "shop_01", "product", 5)
    assert result["sources"][0]["id"] == "product:SKU_102"
    assert all(item.get("id") == "product:SKU_102" for item in result["sources"])
    assert not any(item.get("graph", {}).get("relation") == "alternative" for item in result["sources"])


def test_graph_rag_adds_alternatives_only_when_requested(store):
    result = graph_rag.retrieve("Show similar alternatives to Smart Watch Pro 2", "shop_01", "product", 5)
    alternatives = [item for item in result["sources"] if item.get("graph", {}).get("relation") == "alternative"]
    assert alternatives
    assert all(item["data"]["stock_count"] > 0 for item in alternatives)


def test_graph_rag_specific_product_does_not_return_other_products(store):
    result = graph_rag.retrieve("Hoodie price", "shop_01", "product", 5)
    assert result["sources"]
    assert {item["id"] for item in result["sources"]} == {"product:SKU_116"}
    assert result["focus_product_id"] == "SKU_116"


def test_graph_rag_comparison_keeps_multiple_products(store):
    result = graph_rag.retrieve("Compare Hoodie and Premium Cotton T-Shirt", "shop_01", "product", 5)
    ids = {item["id"] for item in result["sources"]}
    assert "product:SKU_116" in ids
    assert "product:SKU_101" in ids


def test_quantity_followup_uses_focused_product_only(store):
    result = graph_rag.retrieve("i need 6", "shop_01", "product", 5, focus_product_id="SKU_108")
    assert [item["id"] for item in result["sources"]] == ["product:SKU_108"]
    assert result["focus_product_id"] == "SKU_108"


def test_bare_unfocused_turn_does_not_enumerate_catalogue(store):
    assert graph_rag.retrieve("i need 6", "shop_01", "product", 5)["sources"] == []


def test_graph_rag_policy_uses_exact_sql_topic(store):
    result = graph_rag.retrieve("What is the refund policy?", "shop_01", "policy", 3)
    assert result["sources"][0]["id"] == "policy:1:graph"
    assert result["sources"][0]["title"] == "Refund Policy"


def test_graph_rag_does_not_answer_unrelated_queries(store):
    assert graph_rag.retrieve("quantum fusion reactor", "shop_01", "product", 3)["sources"] == []
    assert graph_rag.retrieve("gift wrapping", "shop_01", "policy", 3)["sources"] == []
