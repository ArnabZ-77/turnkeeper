"""MCP servers for Turnkeeper.

- ``app.mcp.internal``: ``paypal_ops_internal`` (name "paypal-ops"), the full tool registry the treasurer
  agent reaches in-process through ``fastmcp.Client``. Never mounted over HTTP.
- ``app.mcp.public``: ``paypal_ops_public``, the read-only, PII-masked subset for judges.
- ``app.mcp.auth``: bearer-token middleware, rate limit and ``mount_public_mcp``.
- ``app.mcp.strict_schema``: converts fastmcp input schemas into Anthropic ``strict: true`` tools.

Submodules are imported lazily by callers so importing this package never pulls in fastmcp.
"""
