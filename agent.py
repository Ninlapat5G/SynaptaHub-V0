"""
SynaptaOS Hub Agent
===================
MQTT 5 agent: receives natural-language tasks, runs a ReAct loop
(os_exec + web_search), and replies on the request's ResponseTopic.

Topics (built from MQTT_BASE_TOPIC + AGENT_NAME):
  cmd    : {base}/hub/{AGENT_NAME}/cmd
  cancel : {base}/hub/{AGENT_NAME}/cancel
  status : {base}/hub/{AGENT_NAME}/status   (retained online / LWT offline)

Reply protocol (per request, MQTT 5):
  - reply goes to the request's ResponseTopic, echoing CorrelationData
  - user property stream_status: ping | chunk | end
  - ping is a heartbeat that keeps the requester's idle timer alive
    during long tasks; chunk carries the result; end closes the stream
"""

import os
import platform
import threading
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
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

HEARTBEAT_SEC = 3


def _t(suffix: str) -> str:
    return f"{BASE}/{suffix}" if BASE else suffix


CMD_TOPIC    = _t(f"hub/{AGENT}/cmd")
CANCEL_TOPIC = _t(f"hub/{AGENT}/cancel")
STATUS_TOPIC = _t(f"hub/{AGENT}/status")

# ── State ──────────────────────────────────────────────────────────────────────

_client:    mqtt.Client | None = None
_task_lock  = threading.Lock()
_kill_event = threading.Event()

# ── Reply (MQTT 5) ───────────────────────────────────────────────────────────────

def _reply(stream_status: str, text: str, response_topic: str | None, correlation_data: bytes | None) -> None:
    if not _client or not response_topic:
        return
    props = Properties(PacketTypes.PUBLISH)
    props.UserProperty = [("stream_status", stream_status)]
    if correlation_data:
        props.CorrelationData = correlation_data
    _client.publish(response_topic, text, qos=1, properties=props)

# ── Task handler ───────────────────────────────────────────────────────────────

def _handle_task(task: str, response_topic: str | None, correlation_data: bytes | None) -> None:
    if not _task_lock.acquire(blocking=False):
        _reply("chunk", "[busy] กำลังทำงานอื่นอยู่ — ส่ง cancel เพื่อยกเลิก", response_topic, correlation_data)
        _reply("end", "", response_topic, correlation_data)
        return

    _kill_event.clear()
    print(f"\n[Hub] Task: {task}")

    # heartbeat — ปิงทุก HEARTBEAT_SEC วิ ให้ตัวจับเวลาฝั่ง requester ไม่หมดระหว่างงานยาว
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_SEC):
            _reply("ping", "", response_topic, correlation_data)

    if response_topic:
        threading.Thread(target=heartbeat, daemon=True).start()

    try:
        result = runner.run(task=task, os_type=OS_TYPE, kill_event=_kill_event, timeout=TIMEOUT)
        _reply("chunk", result, response_topic, correlation_data)
        _reply("end", "", response_topic, correlation_data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _reply("chunk", f"[error] {e}", response_topic, correlation_data)
        _reply("end", "", response_topic, correlation_data)
    finally:
        stop_heartbeat.set()
        _task_lock.release()

# ── MQTT callbacks ─────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code.value != 0:
        print(f"[Hub] Connect failed: {reason_code}")
        return
    client.subscribe(CMD_TOPIC,    qos=1)
    client.subscribe(CANCEL_TOPIC, qos=1)
    client.publish(STATUS_TOPIC, "online", qos=1, retain=True)
    print("[Hub] Connected (MQTT 5)")
    print(f"      CMD    : {CMD_TOPIC}")
    print(f"      STATUS : {STATUS_TOPIC}")


def _on_message(client, userdata, msg):
    if msg.topic == CANCEL_TOPIC:
        print("[Hub] Cancel received")
        _kill_event.set()
        os_exec.cancel()
        return

    task = msg.payload.decode(errors="replace").strip()
    if not task:
        return

    props = getattr(msg, "properties", None)
    response_topic   = getattr(props, "ResponseTopic",   None)
    correlation_data = getattr(props, "CorrelationData", None)

    threading.Thread(
        target=_handle_task,
        args=(task, response_topic, correlation_data),
        daemon=True,
    ).start()


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    print(f"[Hub] Disconnected reason={reason_code} — will reconnect…")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _client
    print("SynaptaOS Hub Agent")
    print(f"  Broker   : {BROKER}:{PORT}{'  [TLS]' if USE_TLS else ''}")
    print(f"  Protocol : MQTT 5")
    print()
    print(kg.snapshot_text())
    print()

    _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    _client.on_connect    = _on_connect
    _client.on_message    = _on_message
    _client.on_disconnect = _on_disconnect

    # Last Will — broker จะ publish "offline" ให้อัตโนมัติถ้า hub หลุดกะทันหัน
    _client.will_set(STATUS_TOPIC, "offline", qos=1, retain=True)

    if USE_TLS:
        import ssl
        _client.tls_set(cert_reqs=ssl.CERT_NONE)

    _client.connect(BROKER, PORT, keepalive=60, properties=Properties(PacketTypes.CONNECT))
    _client.loop_forever()


if __name__ == "__main__":
    main()
