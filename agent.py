"""
SynaptaOS Hub Agent
===================
Connects to MQTT, receives natural language tasks, runs a ReAct loop
(os_exec + web_search), and streams results back line-by-line.

Topic layout (auto-built from MQTT_BASE_TOPIC + AGENT_NAME):
  cmd    : {base}/hub/{AGENT_NAME}/cmd
  output : {base}/hub/{AGENT_NAME}/output
  cancel : {base}/hub/{AGENT_NAME}/cancel

Every response ends with "(mqtt_end)".
"""

import os
import platform
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

import runner
import kg
from tools import os_exec

load_dotenv(Path(__file__).resolve().parent / ".env")

# ── Config ─────────────────────────────────────────────────────────────────────

BROKER  = os.getenv("MQTT_BROKER",      "broker.hivemq.com")
PORT    = int(os.getenv("MQTT_PORT",    "1883"))
USE_TLS = os.getenv("MQTT_USE_TLS",     "false").lower() == "true"
BASE    = os.getenv("MQTT_BASE_TOPIC",  "").rstrip("/")
AGENT   = os.getenv("AGENT_NAME",       "hub-agent")
TIMEOUT = float(os.getenv("COMMAND_TIMEOUT", "60"))

_OS_MAP = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}
OS_TYPE = os.getenv("OS_TYPE") or _OS_MAP.get(platform.system(), "linux")


def _t(suffix: str) -> str:
    return f"{BASE}/{suffix}" if BASE else suffix


CMD_TOPIC    = _t(f"hub/{AGENT}/cmd")
OUTPUT_TOPIC = _t(f"hub/{AGENT}/output")
CANCEL_TOPIC = _t(f"hub/{AGENT}/cancel")

# ── State ──────────────────────────────────────────────────────────────────────

_client:    mqtt.Client | None = None
_task_lock  = threading.Lock()
_kill_event = threading.Event()

# ── MQTT helpers ───────────────────────────────────────────────────────────────

def _pub(text: str) -> None:
    if _client:
        _client.publish(OUTPUT_TOPIC, text, qos=1)


def _end(msg: str = "") -> None:
    if msg:
        _pub(msg)
    _pub("(mqtt_end)")

# ── Task handler ───────────────────────────────────────────────────────────────

def _handle_task(task: str, received_at: float) -> None:
    if not _task_lock.acquire(blocking=False):
        _end("[busy] Already running a task — send 'cancel' to abort.")
        return

    _kill_event.clear()
    _pub("(mqtt_start)")
    dispatch_ms = (time.perf_counter() - received_at) * 1000
    print(f"\n[Hub] Task : {task}")
    print(f"      MQTT dispatch : {dispatch_ms:.0f} ms")

    try:
        t0 = time.perf_counter()
        result = runner.run(
            task=task,
            os_type=OS_TYPE,
            pub=_pub,
            kill_event=_kill_event,
            timeout=TIMEOUT,
        )
        print(f"      Total elapsed : {(time.perf_counter() - t0) * 1000:.0f} ms")
        _end(result)
    except Exception as e:
        import traceback
        print(f"      [error] {e}")
        traceback.print_exc()
        _end(f"[error] {e}")
    finally:
        _task_lock.release()

# ── Cancel ─────────────────────────────────────────────────────────────────────

def _cancel() -> None:
    print("[Hub] Cancel received")
    _kill_event.set()
    os_exec.cancel()

# ── MQTT callbacks ─────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe(CMD_TOPIC,    qos=1)
        client.subscribe(CANCEL_TOPIC, qos=1)
        print("[Hub] Connected")
        print(f"      CMD    : {CMD_TOPIC}")
        print(f"      OUTPUT : {OUTPUT_TOPIC}")
        print(f"      CANCEL : {CANCEL_TOPIC}")
    else:
        print(f"[Hub] Connect failed rc={rc}")


def _on_message(client, userdata, msg):
    if msg.topic == CANCEL_TOPIC:
        _cancel()
        return
    payload = msg.payload.decode(errors="replace").strip()
    if payload:
        received_at = time.perf_counter()
        threading.Thread(target=_handle_task, args=(payload, received_at), daemon=True).start()


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    print(f"[Hub] Disconnected rc={reason_code} — will reconnect…")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _client
    print("SynaptaOS Hub Agent")
    print(f"  Broker : {BROKER}:{PORT}{'  [TLS]' if USE_TLS else ''}")
    print()
    print(kg.snapshot_text())
    print()

    _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    _client.on_connect    = _on_connect
    _client.on_message    = _on_message
    _client.on_disconnect = _on_disconnect

    if USE_TLS:
        import ssl
        _client.tls_set(cert_reqs=ssl.CERT_NONE)

    _client.connect(BROKER, PORT, keepalive=60)
    _client.loop_forever()


if __name__ == "__main__":
    main()
