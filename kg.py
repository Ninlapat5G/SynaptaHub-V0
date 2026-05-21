"""
Knowledge Graph (Hub)
=====================
แหล่งข้อมูลเดียวสำหรับสถานะเครื่อง + session ของ hub agent

ความสัมพันธ์ที่เก็บ:
    Machine ── runs ──> OS
    Machine ── has ──> Resources (CPU / RAM / Disk)
    Session ── in ──> CWD

Singleton — hub รับ task ทีละงานเท่านั้น (lock อยู่แล้ว) จึงปลอดภัย
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psutil

# ── Thai date helper ──────────────────────────────────────────────────────────
# ไม่ใช้ locale ของเครื่อง (เปลี่ยนตาม OS) — map เองให้คงเส้นคงวา

_BKK = ZoneInfo('Asia/Bangkok')
_THAI_DAYS = ['วันจันทร์', 'วันอังคาร', 'วันพุธ', 'วันพฤหัสบดี',
              'วันศุกร์', 'วันเสาร์', 'วันอาทิตย์']
_THAI_MONTHS = ['', 'มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน',
                'พฤษภาคม', 'มิถุนายน', 'กรกฎาคม', 'สิงหาคม',
                'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม']


def now_thai() -> str:
    """คืนเวลาแบบไทย เช่น 'วันศุกร์ที่ 9 พฤษภาคม ค.ศ. 2026 เวลา 14:23 น. (GMT+7)'"""
    n = datetime.now(_BKK)
    return (
        f'{_THAI_DAYS[n.weekday()]}ที่ {n.day} {_THAI_MONTHS[n.month]} '
        f'ค.ศ. {n.year} เวลา {n.hour:02d}:{n.minute:02d} น. (GMT+7)'
    )

# ── Static machine info (คำนวณครั้งเดียวตอน import) ─────────────────────────────

_HOSTNAME = platform.node()
_OS_FULL  = f"{platform.system()} {platform.release()} ({platform.version()})"
_CPU      = platform.processor() or 'unknown'
_CORES    = psutil.cpu_count(logical=False) or 1
_CPU_FREQ = psutil.cpu_freq()
_FREQ_STR = f" @ {_CPU_FREQ.max / 1000:.1f} GHz" if _CPU_FREQ else ""

# ── Live readouts ─────────────────────────────────────────────────────────────

def _live_resources() -> dict[str, Any]:
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        'ram_total_gb':  mem.total // (1024**3),
        'ram_free_gb':   mem.available // (1024**3),
        'disk_total_gb': disk.total // (1024**3),
        'disk_free_gb':  disk.free // (1024**3),
        'cpu_percent':   psutil.cpu_percent(interval=None),
    }


def _current_cwd() -> str:
    # อ่านจาก os_exec ของ tools (ค่าเดียวกัน) — import แบบ lazy เพื่อเลี่ยง circular
    from tools import os_exec
    return os_exec._cwd


# ── Snapshot (text) — ใส่ใน system prompt ของ runner.py ──────────────────────

def snapshot_text() -> str:
    res = _live_resources()
    cwd = _current_cwd()

    lines = []
    lines.append('[KNOWLEDGE GRAPH — สถานะเครื่อง + session]')
    lines.append('')
    lines.append('Machine:')
    lines.append(f'  ├─ hostname: {_HOSTNAME}')
    lines.append(f'  ├─ os:       {_OS_FULL}')
    lines.append(f'  ├─ cpu:      {_CPU} — {_CORES} cores{_FREQ_STR}  ({res["cpu_percent"]:.0f}% busy)')
    lines.append(f'  ├─ ram:      {res["ram_free_gb"]} GB free / {res["ram_total_gb"]} GB total')
    lines.append(f'  └─ disk:     {res["disk_free_gb"]} GB free / {res["disk_total_gb"]} GB total')
    lines.append('')
    lines.append('Session:')
    lines.append(f'  └─ cwd: {cwd}')

    return '\n'.join(lines)



# ── Brief one-liner — ใช้ publish ไป web app ตอนเริ่ม task ────────────────────

def snapshot_brief() -> str:
    res = _live_resources()
    return (
        f'cwd={_current_cwd()} | '
        f'disk={res["disk_free_gb"]}GB free | '
        f'ram={res["ram_free_gb"]}GB free'
    )
