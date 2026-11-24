# OmniCart MCP database server

The MCP server exposes **read-only, shop-scoped database tools** over stdio. It is
not a SQL proxy and cannot mutate orders, customers, prices or content.

## Start / verify

From the project root:

```powershell
.\venv\Scripts\python.exe -m scripts.mcp_smoke
```

The smoke test initializes MCP, lists tools, reads catalogue/policy data and closes
the child process.

## Client configuration

Use the absolute path to this project's Python executable and project directory.
`DATABASE_URL` must point at the database the API is using. For SQLite, the default
is `ecommerce_saas.db` in the project root.

```json
{
  "mcpServers": {
    "omnichat-database": {
      "command": "E:\\Agentic_Chat_BotV-0.1.0\\venv\\Scripts\\python.exe",
      "args": ["-m", "backend.mcp_server"],
      "cwd": "E:\\Agentic_Chat_BotV-0.1.0",
      "env": {
        "PYTHONPATH": "E:\\Agentic_Chat_BotV-0.1.0",
        "DATABASE_URL": "sqlite:///E:/Agentic_Chat_BotV-0.1.0/ecommerce_saas.db",
        "MCP_SHOP_ID": "shop_01"
      }
    }
  }
}
```

Do not add provider API keys, email passwords or PostgreSQL credentials to a client
configuration that is shared. The server only needs the application database URL.

## Tools

| Tool | Purpose |
|---|---|
| `catalog_summary` | Whole-catalogue counts and stock units |
| `catalog_search` | SQL-filtered live product search |
| `catalog_get` | Exact product by ID |
| `catalog_aliases` | Search aliases for an exact product |
| `policies_search` / `policy_get` | Store policy retrieval |
| `knowledge_list` | Published knowledge documents |
| `customer_get` | Shop-scoped customer profile |
| `customer_orders` | Customer order history |
| `customer_messages` | Recent customer conversation |
| `orders_recent` / `order_get` | Order reads |
| `support_cases` | Support case queue |
| `metrics_summary` | Commerce/support metrics |

Writes are intentionally excluded. Use the authenticated FastAPI admin endpoints
for mutations, audit reason codes and role checks.

## Graph RAG integration

`backend/graph_rag.py` calls the same gateway. With `MCP_ENABLED=true` it routes
structured reads through this server; otherwise it uses the in-process gateway.
Hybrid BM25/dense document retrieval remains local and still works if MCP is
unavailable. Set `MCP_ENABLED=false` for the lowest-latency single-process setup.
