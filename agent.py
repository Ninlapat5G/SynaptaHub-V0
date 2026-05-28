"""
SynaptaOS Hub Agent
===================
Connects to MQTT broker via MQTT 5, receives natural language tasks,
runs a ReAct loop (os_exec + web_search), and streams results back.

Topic layout (auto-built from MQTT_BASE_TOPIC + AGENT_NAME):
  cmd    : {base}/hub/{AGENT_NAME}/cmd
  output : {base}/hub/{AGENT_NAME}/output  (ใช้เมื่อไม่มี ResponseTopic)
  cancel : {base}/hub/{AGENT_NAME}/cancel

MQTT 5:
  - รับ ResponseTopic + CorrelationData จาก request
  - ส่ง stream_status user property (chunk | end) แทน sentinel string
  - Fallback: ถ้าไม่มี ResponseTopic → publish ไป OUTPUT_TOPIC + (mqtt_end)
"""

import os
import platform
import threading
import time
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


def _t(suffix: str) -> str:
    return f"{BASE}/{suffix}" if BASE else suffix


CMD_TOPIC    = _t(f"hub/{AGENT}/cmd")
OUTPUT_TOPIC = _t(f"hub/{AGENT}/output")
CANCEL_TOPIC = _t(f"hub/{AGENT}/cancel")

# ── State ──────────────────────────────────────────────────────────────────────

_client:    mqtt.Client | None = None
_task_lock  = threading.Lock()
_kill_event = threading.Event()

# ── MQTT 5 publish helpers ─────────────────────────────────────────────────────

def _make_props(stream_status: str, correlation_data: bytes | None) -> Properties:
    props = Properties(PacketTypes.PUBLISH)
    props.UserProperty = [("stream_status", stream_status)]
    if correlation_data:
        props.CorrelationData = correlation_data
    return props


def _pub_chunk(text: str, response_topic: str | None, correlation_data: bytes | None) -> None:
    if not _client:
        return
    target = response_topic or OUTPUT_TOPIC
    props  = _make_props("chunk", correlation_data)
    _client.publish(target, text, qos=1, properties=props)


def _pub_end(response_topic: str | None, correlation_data: bytes | None) -> None:
    if not _client:
        return
    target = response_topic or OUTPUT_TOPIC
    props  = _make_props("end", correlation_data)

    if response_topic:
        # MQTT 5: ส่ง empty payload + stream_status=end
        _client.publish(target, "", qos=1, properties=props)
    else:
        # Backward compat: publish sentinel string สำหรับ client เก่าที่ฟัง OUTPUT_TOPIC
        _client.publish(target, "(mqtt_end)", qos=1)

# ── Task handler ───────────────────────────────────────────────────────────────

def _handle_task(
    task: str,
    received_at: float,
    response_topic: str | None,
    correlation_data: bytes | None,
) -> None:
    if not _task_lock.acquire(blocking=False):
        _pub_chunk("[busy] Already running a task — send 'cancel' to abort.", response_topic, correlation_data)
        _pub_end(response_topic, correlation_data)
        return

    _kill_event.clear()

    dispatch_ms = (time.perf_counter() - received_at) * 1000
    print(f"\n[Hub] Task : {task}")
    print(f"      MQTT dispatch : {dispatch_ms:.0f} ms")
    if response_topic:
        print(f"      Reply topic   : {response_topic}")

    # สร้าง pub closure ที่ capture response_topic + correlation_data
    def pub(text: str) -> None:
        _pub_chunk(text, response_topic, correlation_data)

    try:
        t0 = time.perf_counter()
        result = runner.run(
            task=task,
            os_type=OS_TYPE,
            pub=pub,
            kill_event=_kill_event,
            timeout=TIMEOUT,
        )
        print(f"      Total elapsed : {(time.perf_counter() - t0) * 1000:.0f} ms")
        if result:
            _pub_chunk(result, response_topic, correlation_data)
        _pub_end(response_topic, correlation_data)
    except Exception as e:
        import traceback
        print(f"      [error] {e}")
        traceback.print_exc()
        _pub_chunk(f"[error] {e}", response_topic, correlation_data)
        _pub_end(response_topic, correlation_data)
    finally:
        _task_lock.release()

# ── Cancel ─────────────────────────────────────────────────────────────────────

def _cancel() -> None:
    print("[Hub] Cancel received")
    _kill_event.set()
    os_exec.cancel()

# ── MQTT callbacks ─────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code.value == 0:
        client.subscribe(CMD_TOPIC,    qos=1)
        client.subscribe(CANCEL_TOPIC, qos=1)
        print("[Hub] Connected (MQTT 5)")
        print(f"      CMD    : {CMD_TOPIC}")
        print(f"      OUTPUT : {OUTPUT_TOPIC}  (fallback)")
        print(f"      CANCEL : {CANCEL_TOPIC}")
    else:
        print(f"[Hub] Connect failed reason={reason_code}")


def _on_message(client, userdata, msg):
    if msg.topic == CANCEL_TOPIC:
        _cancel()
        return

    payload = msg.payload.decode(errors="replace").strip()
    if not payload:
        return

    # อ่าน MQTT 5 properties
    props          = getattr(msg, "properties", None)
    response_topic = getattr(props, "ResponseTopic",        None)
    correlation_data = getattr(props, "CorrelationData",     None)
    expiry         = getattr(props, "MessageExpiryInterval", None)

    if expiry is not None:
        print(f"      Message expiry remaining: {expiry}s")

    received_at = time.perf_counter()
    threading.Thread(
        target=_handle_task,
        args=(payload, received_at, response_topic, correlation_data),
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

    if USE_TLS:
        import ssl
        _client.tls_set(cert_reqs=ssl.CERT_NONE)

    conn_props = Properties(PacketTypes.CONNECT)
    _client.connect(BROKER, PORT, keepalive=60, properties=conn_props)
    _client.loop_forever()


if __name__ == "__main__":
    main()
