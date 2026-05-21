"""
Web search tool via Serper API.
"""

import os
import requests

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web. Use to look up command syntax, troubleshoot errors, "
            "or find information needed to complete the task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
}


def call(args: dict, **_) -> str:
    return search(args["query"])


def search(query: str) -> str:
    api_key = os.getenv("SERPER_API_KEY", "")
    if not api_key:
        return "web_search unavailable — SERPER_API_KEY not set"

    try:
        res = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 3},
            timeout=10,
        )
        data = res.json()
        parts = []

        box = data.get("answerBox", {})
        if box.get("answer"):
            parts.append(box["answer"])
        elif box.get("snippet"):
            parts.append(box["snippet"])

        for r in (data.get("organic") or [])[:3]:
            parts.append(f"{r.get('title', '')}: {r.get('snippet', '')}")

        return "\n".join(parts) or "No results found"

    except Exception as e:
        return f"[search error] {e}"
