"""
ReAct loop for the Hub Agent.

ทำไมยังเป็น ReAct ไม่ใช่ LangGraph:
  - flow ตรงไปตรงมา: รับ task → loop → ตอบ ไม่มี branching ซับซ้อน
  - มีแค่ 3 tools (os_exec, web_search, query_kg)
  - หลีกเลี่ยง dependency หนัก (langgraph + langchain) บนเครื่อง user
  - guard เป็น post-check แบบ rule-based ไม่ต้องใช้ state machine

แต่ปรับปรุงจากของเดิม:
  1. Refresh KG ทุก task (machine state สด ๆ + reset command history)
  2. Tool calls รัน parallel ด้วย ThreadPoolExecutor
  3. Post-loop guard เช็คว่า "ขอ action แต่ไม่มี tool ถูกเรียก" → flag warning
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from openai import OpenAI
from dotenv import load_dotenv

import kg
from tools import SCHEMAS, execute
from tools import os_exec

load_dotenv(Path(__file__).resolve().parent / ".env")

MAX_ROUNDS = 10
PARALLEL_TOOLS = 4

_client = OpenAI(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
)
_model = os.getenv("LLM_MODEL", "gpt-4o-mini")

_SYSTEM = """\
You are an executor agent running on a {os_type} machine.
You are communicating with an AI orchestrator, not a human — respond concisely and structured.

Use tools as needed: run commands, search the web, chain multiple steps until the task is done.
Adapt your approach to the machine — check [KNOWLEDGE GRAPH] below for cwd, recent commands, and resources.
Call query_kg anytime to refresh state.

Safety — refuse with one line, do not execute:
  • Deleting/corrupting system files, mass deletion, credential theft, exfiltrating data, disabling security controls

Reply format when done: one short line stating what was done and the result. No greetings, no explanation, no markdown.
Current date/time: {now}\
"""

# คำที่บ่งชี้ว่า user ต้องการ action จริง (ใช้ใน guard post-check)
_ACTION_WORDS = re.compile(
    r'\b(create|delete|remove|install|uninstall|run|execute|make|start|stop|kill|'
    r'open|close|download|upload|build|deploy|restart|copy|move|rename|mkdir|rmdir)\b'
    r'|สร้าง|ลบ|ติดตั้ง|ถอนติดตั้ง|รัน|ดำเนินการ|เปิด|ปิด|ดาวน์โหลด|อัพโหลด|build|deploy|รีสตาร์ท|คัดลอก|ย้าย|เปลี่ยนชื่อ',
    re.IGNORECASE,
)


def _is_action_task(task: str) -> bool:
    """ดูว่า task มีคำสั่งให้ทำ action จริงไหม (vs. แค่ถามคำถาม)"""
    return bool(_ACTION_WORDS.search(task))


def _run_tool_calls_parallel(
    tool_calls,
    timeout: float,
    kill_event: threading.Event,
    pub: Callable[[str], None],
):
    """
    รัน tool calls หลายตัวพร้อมกัน (max PARALLEL_TOOLS) — คืน list ของ {tc, result}
    ลำดับผลลัพธ์ตรงกับลำดับ input เดิม
    """
    def _one(tc):
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError as exc:
            return {"tc": tc, "result": f"[error] Invalid tool arguments: {exc}", "name": tc.function.name}

        name = tc.function.name
        if name == "os_exec":
            pub(f"$ {args['command']}")
        result = execute(name, args, timeout=timeout, kill_event=kill_event, on_line=pub)
        return {"tc": tc, "result": result, "name": name}

    with ThreadPoolExecutor(max_workers=min(PARALLEL_TOOLS, len(tool_calls))) as ex:
        return list(ex.map(_one, tool_calls))


def run(
    task: str,
    os_type: str,
    pub: Callable[[str], None],
    kill_event: threading.Event,
    timeout: float = 60,
    now: str | None = None,
    system_info: str = "",   # legacy — ไม่ใช้แล้ว แต่เก็บ signature ไว้
) -> str:
    """
    Run the ReAct loop for a given task.
    Calls pub() for each command line streamed in real-time.
    Returns the final LLM summary string.
    """
    if now is None:
        now = kg.now_thai()

    os_exec.reset_cwd()

    # publish KG snapshot brief ให้ web app รู้ตอนเริ่ม
    pub(f"[hub-kg] {kg.snapshot_brief()}")

    # ── 2. Build system prompt with KG snapshot ──────────────────────────────
    sys_content = (
        _SYSTEM.format(os_type=os_type, now=now)
        + "\n\n" + kg.snapshot_text()
    )

    messages: list[dict] = [
        {"role": "system", "content": sys_content},
        {"role": "user",   "content": task},
    ]

    tools_called_count = 0

    # ── 3. ReAct loop ────────────────────────────────────────────────────────
    for round_n in range(1, MAX_ROUNDS + 1):
        if kill_event.is_set():
            return "[cancelled]"

        t0 = time.perf_counter()
        response = _client.chat.completions.create(
            model=_model,
            messages=messages,
            tools=SCHEMAS,
            tool_choice="auto",
        )

        msg = response.choices[0].message
        print(f"      R{round_n} LLM : {(time.perf_counter() - t0) * 1000:.0f} ms")

        if not msg.tool_calls:
            final_text = msg.content or ""
            return _post_check(task, final_text, tools_called_count, pub)

        messages.append(msg)
        tools_called_count += len(msg.tool_calls)

        # parallel execute
        results = _run_tool_calls_parallel(msg.tool_calls, timeout, kill_event, pub)

        if kill_event.is_set():
            return "[cancelled]"

        # append ตามลำดับเดิมของ tool_calls
        for r in results:
            messages.append({
                "role":         "tool",
                "tool_call_id": r["tc"].id,
                "content":      r["result"],
            })

    return _post_check(
        task,
        "[error] Reached maximum rounds without completing the task",
        tools_called_count,
        pub,
    )


def _post_check(task: str, final_text: str, tools_called: int, pub: Callable[[str], None]) -> str:
    """
    Rule-based guard: ถ้า task ขอ action แต่ไม่มี tool ถูกเรียกเลย → flag warning
    หลักคิด: agent อาจหลอนว่าทำแล้ว ทั้งที่จริง ๆ ไม่ได้รัน os_exec เลย
    """
    if tools_called == 0 and final_text and _is_action_task(task):
        warning = "[guard] ⚠️ ไม่มี tool ถูกเรียก ทั้งที่ task ขอให้ทำ action — ผลลัพธ์อาจไม่เกิดขึ้นจริง"
        pub(warning)
        return f"{warning}\n{final_text}"
    return final_text
