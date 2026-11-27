"""Measure embedding latency and service memory for a VM sizing decision.

    EMBEDDING_SERVICE_URL=http://127.0.0.1:8080 \
    EMBEDDING_SERVICE_API_KEY=... \
    python -m embedding_service.benchmark --count 100

Reports p50/p95 latency for 100 multilingual queries and the service's reported
RSS. No model is loaded in this process; it only uses HTTP.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time

import httpx

DEFAULT_QUERIES = [
    "amar jonno ekta nil kurti dekhao",
    "eta ki stock e ache?",
    "delivery charge koto Dhaka er bhitore?",
    "show me a red saree under 3000 taka",
    "আমার জন্য একটি নীল কুর্তি দেখান",
    "return policy for damaged items",
    "purple jamdani saree with blouse",
    "is this available in size M?",
    "সবচেয়ে সস্তা কটন শার্ট",
    "black leather wallet price",
]


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("EMBEDDING_SERVICE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("EMBEDDING_SERVICE_API_KEY", ""))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if not args.url:
        parser.error("Set EMBEDDING_SERVICE_URL or pass --url")
    if not args.api_key:
        parser.error("Set EMBEDDING_SERVICE_API_KEY or pass --api-key")

    base = args.url.rstrip("/")
    headers = {"X-API-Key": args.api_key}
    queries = args.query or DEFAULT_QUERIES
    with httpx.Client(base_url=base, headers=headers, timeout=args.timeout) as client:
        health = client.get("/health").json()
        deadline = time.monotonic() + 120
        while health.get("status") != "ready" and time.monotonic() < deadline:
            time.sleep(2)
            health = client.get("/health").json()
        if health.get("status") != "ready":
            raise SystemExit(f"Service is not ready: {health.get('status')} {health.get('error') or ''}")

        latencies = []
        dimensions = set()
        for index in range(args.count):
            query = queries[index % len(queries)]
            started = time.perf_counter()
            response = client.post("/embed/text", json={"texts": [query], "kind": "query"})
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.raise_for_status()
            body = response.json()
            latencies.append(elapsed_ms)
            dimensions.add(body["dimension"])

    report = {
        "url": base,
        "count": len(latencies),
        "dimension": sorted(dimensions),
        "text_model": health.get("models", {}).get("text", {}).get("model"),
        "text_revision": health.get("models", {}).get("text", {}).get("revision"),
        "memory_rss_mb": health.get("memory_rss_mb"),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "min": round(min(latencies), 2),
            "max": round(max(latencies), 2),
            "mean": round(statistics.fmean(latencies), 2),
        },
        "queries_per_second": round(1000 / statistics.fmean(latencies), 2) if latencies else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
