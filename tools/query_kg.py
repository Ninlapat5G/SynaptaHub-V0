"""
Query Knowledge Graph tool — agent ใช้ดูสถานะเครื่องสด ๆ ระหว่าง task

ใช้เมื่อ:
  - ต้องการ cwd ปัจจุบัน
  - เช็ค disk/ram ว่าพอไหม
  - ทบทวนคำสั่งที่เพิ่งรันไปในรอบก่อน
"""

import json

SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_kg",
        "description": (
            "Query current machine state from Knowledge Graph: working directory, "
            "recent commands ran in this session, disk/memory/cpu free. "
            "Call when you need fresh state or to recall what was just done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "'text' = ต้นไม้แบบอ่านง่าย, 'json' = structured object",
                }
            },
            "required": [],
        },
    },
}


def call(args: dict, **_) -> str:
    from .. import kg
    fmt = (args or {}).get("format", "text")
    if fmt == "json":
        return json.dumps(kg.snapshot_json(), ensure_ascii=False, indent=2)
    return kg.snapshot_text()
