"""Compatibility helper for the local MCP servers.

These servers were written against an old mcp SDK whose low-level
``Server(...)`` constructor accepted ``on_list_tools`` / ``on_call_tool``
keyword arguments with a ``(context, params)`` handler signature.

The API has changed twice since:

- mcp 1.27: handlers registered through ``@server.list_tools()`` /
  ``@server.call_tool()`` decorators (constructor kwargs removed).
- mcp 2.0:  decorators removed again — the constructor accepts
  ``on_list_tools`` / ``on_call_tool`` callables with the ORIGINAL
  ``(context, params)`` signature (``ServerRequestContext, params``).

``make_server`` therefore supports both generations:

- legacy list handler:   ``async def h(context, params) -> ListToolsResult``
- legacy call handler:   ``async def h(context, params) -> CallToolResult``
  (``params`` is a namespace with ``.name`` and ``.arguments``)

Server scripts import this from the repo-level ``mcp-servers`` directory,
so each script adds its parent-parent directory to ``sys.path`` first.
"""

import inspect

from mcp import types
from mcp.server import Server

__all__ = ["make_server"]


def _detect_generation() -> str:
    """Return "legacy" (pre-1.27: ctor kwargs), "decorator" (1.27.x),
    or "modern" (2.x: ctor kwargs again)."""
    import mcp
    version = getattr(mcp, "__version__", None)
    if version:
        try:
            major = int(str(version).split(".")[0])
            if major >= 2:
                return "modern"
        except (TypeError, ValueError):
            pass
    # Fall back to capability sniffing for unversioned builds.
    if "on_list_tools" in inspect.signature(Server.__init__).parameters:
        return "legacy"
    return "decorator"


def make_server(name: str, on_list_tools, on_call_tool) -> Server:
    """Build a Server wired to legacy-style ``(context, params)`` handlers."""
    generation = _detect_generation()

    if generation in ("legacy", "modern"):
        # Pre-1.27 and 2.x both take the callables straight in the ctor.
        # 2.x signature: (ServerRequestContext, params) — identical shape to
        # the legacy handlers, so they can be passed through unchanged.
        return Server(name, on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    # mcp 1.27 generation: register through decorators. The request
    # parameter must be type-annotated — the 1.27 Server inspects
    # signatures and only passes the request to typed parameters.
    app = Server(name)

    @app.list_tools()
    async def _list_tools(request: types.ListToolsRequest):  # noqa: ANN001, ANN202
        params = getattr(request, "params", None)
        return await on_list_tools(None, params)

    @app.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments):  # noqa: ANN001, ANN202
        from types import SimpleNamespace

        params = SimpleNamespace(name=tool_name, arguments=arguments or {})
        return await on_call_tool(None, params)

    return app
