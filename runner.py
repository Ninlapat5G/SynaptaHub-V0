import os
import threading
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

import kg
from tools import os_exec as _os_exec
from tools import web_search as _web_search

load_dotenv(Path(__file__).resolve().parent / ".env")

_SYSTEM = """\
คุณคือ agent ที่รันอยู่บนเครื่อง {os_type} มีหน้าที่ลงมือทำงานบนเครื่องนี้ตามที่ได้รับคำสั่ง
ห้ามแค่รายงานหรืออธิบาย — ต้องใช้ tool เพื่อทำงานจริงเสมอ

[หน้าที่หลัก]
รับคำสั่ง → คิดว่าต้องใช้ tool อะไร → ลงมือทำ → รายงานผลสั้นๆ หนึ่งบรรทัด

[ตัวอย่างวิธีทำงาน]
- "เปิด YouTube" → os_exec("start https://youtube.com")
- "เปิดเพลง X" → web_search("X YouTube") → เอา URL ที่ได้ → os_exec("start <url>")
- "เปิดเพลง X ใน Spotify" → web_search("X Spotify") → os_exec("start <url>")
- "ดู disk ที่เหลือ" → os_exec("wmic logicaldisk get size,freespace")
- "shutdown" → os_exec("shutdown /s /t 0")
- "pause/เล่นเพลง" → os_exec('powershell -c "(New-Object -com WScript.Shell).SendKeys([char]179)"')
- "เพิ่มเสียง" → os_exec('powershell -c "(New-Object -com WScript.Shell).SendKeys([char]175)"')

[กฎสำคัญ]
- เปิดเพลงโดยไม่ระบุ platform → ใช้ YouTube เป็น default เสมอ
- ค้นหาได้ผลแล้ว ต้องเปิดต่อด้วย os_exec ทันที ห้ามแค่ส่ง URL กลับ
- ถ้าไม่แน่ใจว่าจะทำอะไร → ถามกลับสั้นๆ เพื่อให้ผู้ใช้ชี้แจง แทนที่จะเดาเอง
- ตอบจบด้วยหนึ่งบรรทัด บอกว่าทำอะไรและผลลัพธ์คืออะไร ห้ามใช้ markdown
- คำสั่งที่ต้องการส่ง keyboard shortcut หรือ interact กับ UI ของ app → ใช้ powershell -c "..." ใน os_exec แทน cmd ปกติ เช่น pause เพลง, เพิ่ม/ลดเสียง, กด hotkey ใดๆ

[ห้ามดำเนินการเด็ดขาด]
ลบไฟล์ระบบ, ลบข้อมูลจำนวนมาก, ขโมย credential, ส่งข้อมูลออกนอกเครื่อง, ปิดระบบความปลอดภัย
→ ตอบปฏิเสธสั้นๆ หนึ่งบรรทัด

วันเวลาปัจจุบัน: {now}
"""

_model = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    temperature=0,
)


def _make_tools(pub: Callable[[str], None], kill_event: threading.Event, timeout: float):
    @tool
    def os_exec(command: str) -> str:
        """Execute a terminal command on this computer. Supports cd — directory persists across calls."""
        pub(f"$ {command}")
        return _os_exec.run(command, timeout=timeout, kill_event=kill_event, on_line=pub)

    @tool
    def web_search(query: str) -> str:
        """Search the web for information needed to complete the task."""
        return _web_search.search(query)

    @tool
    def query_kg() -> str:
        """Get current machine state: working directory, disk and memory usage."""
        return kg.snapshot_text()

    return [os_exec, web_search, query_kg]


def run(
    task: str,
    os_type: str,
    pub: Callable[[str], None],
    kill_event: threading.Event,
    timeout: float = 60,
    now: str | None = None,
) -> str:
    if now is None:
        now = kg.now_thai()

    _os_exec.reset_cwd()

    system_prompt = _SYSTEM.format(os_type=os_type, now=now) + "\n\n" + kg.snapshot_text()
    graph = create_react_agent(_model, _make_tools(pub, kill_event, timeout), prompt=system_prompt)

    final_text = ""
    try:
        for event in graph.stream(
            {"messages": [HumanMessage(task)]},
            config={"recursion_limit": 10},
            stream_mode="updates",
        ):
            if kill_event.is_set():
                return "[cancelled]"
            if "tools" in event:
                for msg in event["tools"]["messages"]:
                    print(f"      [{msg.name}] → {str(msg.content)[:120]}")
            if "agent" in event:
                msg = event["agent"]["messages"][-1]
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        print(f"      call {tc['name']}({tc['args']})")
                elif msg.content:
                    final_text = str(msg.content)
                    print(f"      final → {final_text[:120]}")
    except Exception as e:
        print(f"      [error] {e}")
        return f"[error] {e}"

    return final_text or "[no response]"
