"""
Tool registry for the Hub Agent.

To add a new tool:
  1. Create tools/<name>.py with SCHEMA + call(args, **ctx) + any helpers
  2. Import it here and add to _REGISTRY

That's it — runner.py picks it up automatically.
"""

from . import os_exec, web_search, query_kg

_REGISTRY = {
    "os_exec":    os_exec,
    "web_search": web_search,
    "query_kg":   query_kg,
}

SCHEMAS = [mod.SCHEMA for mod in _REGISTRY.values()]


def execute(name: str, args: dict, **ctx) -> str:
    mod = _REGISTRY.get(name)
    if not mod:
        return f"[error] Unknown tool: {name}"
    return mod.call(args, **ctx)
