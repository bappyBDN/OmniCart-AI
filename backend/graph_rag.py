"""Agentic Graph RAG over the full shop database.

The graph is deliberately small and deterministic:

    analyse -> structured_sql -> expand_relations -> hybrid_search -> fuse

`structured_sql` reads authoritative rows through the shared read-only gateway
(local SQL or the MCP transport). `expand_relations` adds same-category
alternatives when an exact product is unavailable. `hybrid_search` supplies
BM25/dense policy and description evidence. `fuse` deduplicates live SQL rows
with document chunks and attaches graph provenance for the grounding critic.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from backend.conversation import alternative_request
from backend.mcp_client import get_gateway
from backend.retrieval import ACCESSORY_WORDS, TYPE_GROUPS, analyse_query, retriever, tokens

log = logging.getLogger(__name__)


class GraphRagState(TypedDict, total=False):
    query: str
    shop_id: str
    kind: str
    limit: int
    customer_id: int | None
    focus_product_id: str | None
    plan: object
    structured: list
    alternatives: list
    hybrid: list
    sources: list
    trace: list
    error: str


def _trace(state, node, detail, **extra):
    return state.get("trace", []) + [{"node": node, "detail": detail, **extra}]


def _name_key(value):
    return " ".join(re.sub(r"[^\w\u0980-\u09ff]+", " ", (value or "").casefold()).split())


def _product_relevant(product, plan):
    query_terms = set(plan.terms)
    title_terms = set(tokens(product.get("name", "")))
    if plan.groups and not any(group & query_terms and group & title_terms for group in plan.groups):
        return False
    if plan.groups and (title_terms & ACCESSORY_WORDS) and not (title_terms & ACCESSORY_WORDS & query_terms):
        return False
    if product.get("product_id", "").casefold() in plan.normalised.casefold():
        return True
    type_match = any(group & query_terms and group & title_terms for group in TYPE_GROUPS)
    title_coverage = len(title_terms & query_terms) / max(1, len(title_terms))
    body_terms = set(tokens(product.get("description", "") + " " + product.get("category", "")))
    coverage = len(query_terms & (title_terms | body_terms)) / max(1, len(query_terms))
    return bool(type_match or title_coverage >= 0.5 or coverage >= 0.6 or not query_terms)


def _policy_relevant(policy, plan):
    query_terms = set(plan.terms)
    title_terms = set(tokens(policy.get("policy_type", "")))
    body_terms = set(tokens(policy.get("policy_text", "")))
    if not query_terms:
        return False
    title_coverage = len(title_terms & query_terms) / max(1, len(title_terms))
    coverage = len(query_terms & (title_terms | body_terms)) / max(1, len(query_terms))
    return bool((title_terms and title_terms.issubset(query_terms)) or title_coverage >= 0.75 or coverage >= 0.60)


def analyse_node(state):
    plan = analyse_query(state["query"])
    return {"plan": plan, "trace": _trace(state, "graph.analyse", f"Extracted {len(plan.terms)} terms", locale=plan.locale)}


def structured_node(state, config: RunnableConfig):
    gateway = config["configurable"]["gateway"]
    plan, kind = state["plan"], state["kind"]
    limit = max(1, min(10, state.get("limit", 5)))
    try:
        if kind == "product":
            # A bare conversational turn with no product terms or attributes is
            # not permission to enumerate the whole catalogue.
            if (not plan.terms and not (plan.colors or plan.size or plan.max_price is not None
                or plan.min_price is not None or plan.in_stock is not None or plan.category)
                    and not state.get("focus_product_id")):
                return {"structured": [], "focus_product_id": None,
                        "trace": _trace(state, "graph.sql", "No product terms or filters; skipped catalogue scan")}
            rows = []
            # Exact IDs are authoritative and bypass fuzzy ranking.
            for product_id in set(re.findall(r"SKU_\d+", state["query"], re.I)):
                try:
                    rows.append(gateway.get_product(product_id.upper()))
                except Exception:
                    pass
            if not rows:
                rows = gateway.search_products(
                    query=state["query"], category=plan.category,
                    color=next(iter(plan.colors), None) if len(plan.colors) == 1 else None,
                    size=plan.size, max_price=plan.max_price, min_price=plan.min_price,
                    in_stock=plan.in_stock, limit=limit * 2,
                )
            query_key = _name_key(state["query"])
            asks_alternatives = alternative_request(state["query"])
            asks_comparison = bool(re.search(r"compare|difference|versus|\bvs\b|তুলনা|পার্থক্য", state["query"], re.I))
            exact = [row for row in rows if _name_key(row.get("name", "")) in query_key]
            if asks_alternatives or asks_comparison:
                focus_id = None
            elif len(exact) == 1:
                focus_id = exact[0]["product_id"]
            else:
                focus_id = state.get("focus_product_id")
            if focus_id:
                try:
                    exact_row = gateway.get_product(focus_id)
                    structured = [exact_row]
                except Exception:
                    structured = [row for row in rows if row.get("product_id") == focus_id]
            else:
                structured = [row for row in rows if _product_relevant(row, plan)]
            trace = _trace(state, "graph.sql", f"Read {len(structured)} relevant product rows",
                           focused=focus_id)
            return {"structured": structured[:limit], "focus_product_id": focus_id, "trace": trace}
        rows = gateway.search_policies(state["query"], limit=limit * 2)
        structured = [row for row in rows if _policy_relevant(row, plan)]
        trace = _trace(state, "graph.sql", f"Read {len(structured)} relevant policy rows")
        return {"structured": structured[:limit], "trace": trace}
    except Exception:
        log.warning("Structured graph lookup failed; hybrid retrieval remains active")
        return {"structured": [], "error": "structured_lookup_failed",
                "trace": _trace(state, "graph.sql", "Structured lookup failed safely")}


def expand_node(state, config: RunnableConfig):
    structured = state.get("structured", [])
    if state["kind"] != "product":
        return {"alternatives": [], "trace": _trace(state, "graph.expand", "No product relationship expansion needed")}
    gateway = config["configurable"]["gateway"]
    alternatives = []
    asks_alternatives = alternative_request(state["query"])
    needs_alternative = asks_alternatives or (
        not state.get("focus_product_id") and any((row.get("stock_count") or 0) <= 0 for row in structured)
    )
    if needs_alternative:
        categories = {row.get("category") for row in structured if row.get("category")}
        for category in categories:
            try:
                rows = gateway.search_products(category=category, in_stock=True, limit=8)
                for row in rows:
                    if row.get("product_id") not in {item.get("product_id") for item in structured + alternatives}:
                        row["graph_relation"] = "alternative_to"
                        row["graph_relation_to"] = structured[0].get("product_id") if structured else None
                        alternatives.append(row)
                    if len(alternatives) >= 3:
                        break
            except Exception:
                log.warning("Graph alternative lookup failed")
    return {"alternatives": alternatives[:3],
            "trace": _trace(state, "graph.expand", f"Found {len(alternatives[:3])} related in-stock alternatives")}


def hybrid_node(state):
    try:
        rows = retriever.search(state["query"], state["shop_id"], state["kind"], max(5, state.get("limit", 5) * 2))
    except Exception:
        log.warning("Hybrid retrieval failed; structured rows remain active")
        rows = []
    return {"hybrid": rows, "trace": _trace(state, "graph.hybrid", f"Retrieved {len(rows)} lexical/dense chunks")}


def fuse_node(state):
    kind = state["kind"]
    limit = max(1, min(10, state.get("limit", 5)))
    structured = state.get("structured", [])
    alternatives = state.get("alternatives", [])
    hybrid = state.get("hybrid", [])
    focus_id = state.get("focus_product_id") or (structured[0].get("product_id") if kind == "product" and len(structured) == 1 and not alternative_request(state["query"]) else None)
    if focus_id:
        hybrid = [source for source in hybrid if source.get("id") == f"product:{focus_id}"]
    output = []
    seen = set()
    policy_titles = set()

    def add(source, priority):
        key = source["id"]
        if key in seen:
            return
        if source.get("kind") == "policy" and source["title"] in policy_titles:
            return
        seen.add(key)
        if source.get("kind") == "policy":
            policy_titles.add(source["title"])
        source["graph_priority"] = priority
        output.append(source)

    for row in structured:
        if kind == "product":
            source = {
                "id": f"product:{row['product_id']}", "kind": "product", "title": row["name"],
                "content": json.dumps(row, ensure_ascii=False), "data": row, "score": 1.0,
                "confidence": 1.0, "lexical_score": 0, "dense_score": 0, "coverage": 1.0,
                "retrieval": "graph_sql", "source_type": "product",
                "graph": {"relation": "exact"},
            }
        else:
            source = {
                "id": f"policy:{row['policy_id']}:graph", "kind": "policy", "title": row["policy_type"],
                "content": row["policy_text"], "data": None, "score": 1.0, "confidence": 1.0,
                "lexical_score": 0, "dense_score": 0, "coverage": 1.0,
                "retrieval": "graph_sql", "source_type": "policy",
                "graph": {"relation": "exact"},
            }
        add(source, 0)

    for row in alternatives:
        source = {
            "id": f"product:{row['product_id']}", "kind": "product", "title": row["name"],
            "content": json.dumps(row, ensure_ascii=False), "data": row, "score": 0.7,
            "confidence": 0.9, "lexical_score": 0, "dense_score": 0, "coverage": 1.0,
            "retrieval": "graph_sql", "source_type": "product",
            "graph": {"relation": "alternative", "relation_to": row.get("graph_relation_to")},
        }
        add(source, 1)

    for source in hybrid:
        if source.get("kind") != kind:
            continue
        # Prefer the live SQL row when the hybrid hit is the same product.
        if source["id"] in seen:
            continue
        source = dict(source)
        source["graph"] = {"relation": "document"}
        add(source, 2)

    output.sort(key=lambda item: (item["graph_priority"], -item["score"]))
    output = output[:limit]
    return {"sources": output, "focus_product_id": focus_id,
            "trace": _trace(state, "graph.fuse", f"Fused {len(output)} cited sources",
                            exact=len(structured), documents=len(hybrid), alternatives=len(alternatives))}


def build_graph():
    graph = StateGraph(GraphRagState)
    graph.add_node("analyse", analyse_node)
    graph.add_node("structured", structured_node)
    graph.add_node("expand", expand_node)
    graph.add_node("hybrid", hybrid_node)
    graph.add_node("fuse", fuse_node)
    graph.add_edge(START, "analyse")
    graph.add_edge("analyse", "structured")
    graph.add_edge("structured", "expand")
    graph.add_edge("expand", "hybrid")
    graph.add_edge("hybrid", "fuse")
    graph.add_edge("fuse", END)
    return graph.compile()


graph = build_graph()


def retrieve(query, shop_id, kind="product", limit=5, customer_id=None, focus_product_id=None):
    config = {"configurable": {"gateway": get_gateway(shop_id)}}
    try:
        result = graph.invoke({
            "query": query, "shop_id": shop_id, "kind": kind, "limit": limit,
            "customer_id": customer_id, "focus_product_id": focus_product_id,
            "structured": [], "alternatives": [], "hybrid": [], "sources": [], "trace": [],
        }, config=config)
        focused = result.get("focus_product_id") if kind == "product" else None
        return {"sources": result.get("sources", []), "trace": result.get("trace", []),
                "error": result.get("error"), "focus_product_id": focused}
    except Exception:
        log.exception("Graph RAG failed; falling back to hybrid retrieval")
        return {"sources": retriever.search(query, shop_id, kind, limit),
                "trace": [{"node": "graph.fallback", "detail": "Graph RAG failed safely; hybrid retrieval used"}],
                "error": "graph_failed"}
