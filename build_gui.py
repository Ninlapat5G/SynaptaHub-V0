"""
SynaptaOS Hub Builder
GUI สำหรับตั้งค่า .env และ compile hub agent เป็น .exe ไฟล์เดียว
รันด้วย: python hub/build_gui.py  (จาก project root)
"""

import os, sys, threading, queue, shutil, importlib.util, subprocess
import customtkinter as ctk
from dotenv import dotenv_values

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))   # hub/
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)                  # project root
ENV_PATH     = os.path.join(SCRIPT_DIR, ".env")
AGENT_PY     = os.path.join(SCRIPT_DIR, "agent.py")
DIST_DIR     = os.path.join(SCRIPT_DIR, "dist")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class HubBuilder(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SynaptaOS Hub Builder")
        self.geometry("960x660")
        self.minsize(820, 560)

        self._build_queue = queue.Queue()
        self._building    = False

        self._setup_ui()
        self._load_env()

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=4)
        self.grid_columnconfigure(1, weight=5)
        self.grid_rowconfigure(0, weight=1)

        self._build_left_panel()
        self._build_right_panel()

    def _build_left_panel(self):
        frame = ctk.CTkScrollableFrame(self, label_text="⚙  Settings",
                                       label_font=ctk.CTkFont(size=13, weight="bold"))
        frame.grid(row=0, column=0, padx=(14, 6), pady=14, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        r = [0]  # mutable row counter

        def section(label):
            ctk.CTkLabel(frame, text=label,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#6b7280", anchor="w").grid(
                row=r[0], column=0, columnspan=2,
                padx=8, pady=(14, 2), sticky="w")
            r[0] += 1

        def field(label, var, secret=False):
            ctk.CTkLabel(frame, text=label, anchor="w").grid(
                row=r[0], column=0, padx=(8, 4), pady=3, sticky="w")

            if secret:
                wrap = ctk.CTkFrame(frame, fg_color="transparent")
                wrap.grid(row=r[0], column=1, padx=(0, 8), pady=3, sticky="ew")
                wrap.grid_columnconfigure(0, weight=1)
                entry = ctk.CTkEntry(wrap, textvariable=var, show="*")
                entry.grid(row=0, column=0, sticky="ew")
                shown = [False]
                def toggle(e=entry, s=shown):
                    s[0] = not s[0]
                    e.configure(show="" if s[0] else "*")
                ctk.CTkButton(wrap, text="👁", width=32, height=28,
                              fg_color="transparent", border_width=1,
                              command=toggle).grid(row=0, column=1, padx=(4, 0))
            else:
                ctk.CTkEntry(frame, textvariable=var).grid(
                    row=r[0], column=1, padx=(0, 8), pady=3, sticky="ew")
            r[0] += 1

        def check(label, text, var):
            ctk.CTkLabel(frame, text=label, anchor="w").grid(
                row=r[0], column=0, padx=(8, 4), pady=3, sticky="w")
            ctk.CTkCheckBox(frame, text=text, variable=var).grid(
                row=r[0], column=1, padx=(0, 8), pady=3, sticky="w")
            r[0] += 1

        # LLM
        section("── LLM ──────────────────────")
        self.v_api_key  = ctk.StringVar()
        self.v_endpoint = ctk.StringVar()
        self.v_model    = ctk.StringVar()
        field("API Key",  self.v_api_key,  secret=True)
        field("Endpoint", self.v_endpoint)
        field("Model",    self.v_model)

        # MQTT
        section("── MQTT ─────────────────────")
        self.v_broker     = ctk.StringVar()
        self.v_port       = ctk.StringVar()
        self.v_tls        = ctk.BooleanVar(value=True)
        self.v_base_topic = ctk.StringVar()
        self.v_agent_name = ctk.StringVar()
        field("Broker",     self.v_broker)
        field("Port",       self.v_port)
        check("TLS",        "เปิดใช้ TLS", self.v_tls)
        field("Base Topic", self.v_base_topic)
        field("Agent Name", self.v_agent_name)

        # Other
        section("── อื่นๆ ──────────────────────")
        self.v_serper  = ctk.StringVar()
        self.v_timeout = ctk.StringVar()
        self.v_os_type = ctk.StringVar(value="Auto")
        field("Serper Key (optional)", self.v_serper, secret=True)
        field("Timeout (วิ)",          self.v_timeout)

        ctk.CTkLabel(frame, text="OS Type", anchor="w").grid(
            row=r[0], column=0, padx=(8, 4), pady=3, sticky="w")
        ctk.CTkOptionMenu(frame, variable=self.v_os_type,
                          values=["Auto", "Windows", "Linux", "macOS"]).grid(
            row=r[0], column=1, padx=(0, 8), pady=3, sticky="ew")
        r[0] += 1

        # Build options
        section("── Build ─────────────────────")
        self.v_console = ctk.BooleanVar(value=False)
        check("Console Window", "แสดง Console (debug)", self.v_console)

        # Buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=r[0], column=0, columnspan=2,
                       padx=8, pady=(18, 10), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(btn_frame, text="💾  Save Settings",
                      fg_color="transparent", border_width=1,
                      command=self._save_env).grid(
            row=0, column=0, padx=(0, 4), sticky="ew")

        self.build_btn = ctk.CTkButton(btn_frame, text="🔨  Build .exe",
                                       fg_color="#2563eb", hover_color="#1d4ed8",
                                       command=self._start_build)
        self.build_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

    def _build_right_panel(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=0, column=1, padx=(6, 14), pady=14, sticky="nsew")
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="📋  Build Log",
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w").grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="w")

        self.log_box = ctk.CTkTextbox(frame, font=ctk.CTkFont(family="Consolas", size=11),
                                      wrap="word", state="disabled")
        self.log_box.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="nsew")
        self.log_box._textbox.tag_configure("red",   foreground="#f87171")
        self.log_box._textbox.tag_configure("green", foreground="#4ade80")
        self.log_box._textbox.tag_configure("dim",   foreground="#6b7280")

        self.status_label = ctk.CTkLabel(frame, text="พร้อมใช้งาน",
                                          anchor="w", text_color="#6b7280",
                                          font=ctk.CTkFont(size=11))
        self.status_label.grid(row=2, column=0, padx=12, pady=(0, 4), sticky="w")

        self.progress = ctk.CTkProgressBar(frame, mode="indeterminate")
        self.progress.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.progress.set(0)

    # ── .env load / save ───────────────────────────────────────────────────────

    def _load_env(self):
        if not os.path.exists(ENV_PATH):
            self._log("ไม่พบ hub/.env — ตั้งค่าด้านซ้ายแล้วกด Save ได้เลย", "dim")
            return
        cfg = dotenv_values(ENV_PATH)
        self.v_api_key.set(cfg.get("LLM_API_KEY", ""))
        self.v_endpoint.set(cfg.get("LLM_BASE_URL", "https://api.opentyphoon.ai/v1"))
        self.v_model.set(cfg.get("LLM_MODEL", "typhoon-v2.5-30b-a3b-instruct"))
        self.v_broker.set(cfg.get("MQTT_BROKER", "broker.hivemq.com"))
        self.v_port.set(cfg.get("MQTT_PORT", "8883"))
        self.v_tls.set(cfg.get("MQTT_USE_TLS", "true").lower() == "true")
        self.v_base_topic.set(cfg.get("MQTT_BASE_TOPIC", ""))
        self.v_agent_name.set(cfg.get("AGENT_NAME", "office-pc"))
        self.v_serper.set(cfg.get("SERPER_API_KEY", ""))
        self.v_timeout.set(cfg.get("COMMAND_TIMEOUT", "60"))
        self._log("✓ โหลด hub/.env แล้ว", "dim")

    def _save_env(self):
        lines = [
            f"LLM_API_KEY={self.v_api_key.get()}",
            f"LLM_BASE_URL={self.v_endpoint.get()}",
            f"LLM_MODEL={self.v_model.get()}",
            "",
            f"SERPER_API_KEY={self.v_serper.get()}",
            "",
            f"MQTT_BROKER={self.v_broker.get()}",
            f"MQTT_PORT={self.v_port.get()}",
            f"MQTT_USE_TLS={'true' if self.v_tls.get() else 'false'}",
            "",
            f"MQTT_BASE_TOPIC={self.v_base_topic.get()}",
            f"AGENT_NAME={self.v_agent_name.get()}",
            "",
            f"COMMAND_TIMEOUT={self.v_timeout.get()}",
            "",
            "CREWAI_TRACING_ENABLED=false",
            "OTEL_SDK_DISABLED=true",
        ]
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self._log("✓ บันทึก settings ไปที่ hub/.env แล้ว", "green")
        self.status_label.configure(text="✓ บันทึกแล้ว")

    # ── Build ──────────────────────────────────────────────────────────────────

    def _start_build(self):
        if self._building:
            return

        # ตรวจ nuitka
        if importlib.util.find_spec("nuitka") is None:
            self._log("✗ ไม่พบ nuitka — ติดตั้งก่อนนะ:", "red")
            self._log("  pip install nuitka", "dim")
            return

        self._save_env()

        self._building = True
        self.build_btn.configure(state="disabled")
        self.progress.start()
        self.status_label.configure(text="⚙  กำลัง compile...")
        self._log("\n─── เริ่ม Build ────────────────────────────", "dim")

        threading.Thread(target=self._build_worker, daemon=True).start()
        self.after(100, self._poll_log)

    def _build_worker(self):
        q = self._build_queue
        try:
            cmd = [
                sys.executable, "-m", "nuitka",
                "--onefile",
                f"--output-dir={DIST_DIR}",
                "--remove-output",
                "--output-filename=SynaptaHubAgent",
            ]

            # --windows-console-mode เฉพาะ Windows เท่านั้น
            if sys.platform == "win32":
                os_choice = self.v_os_type.get()
                if os_choice in ("Auto", "Windows") and not self.v_console.get():
                    cmd.append("--windows-console-mode=disable")

            cmd.append(AGENT_PY)

            os.chdir(PROJECT_ROOT)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                q.put(("line", line.rstrip()))
            proc.wait()

            # ลบ folder ขยะที่ nuitka ทิ้งไว้ (ทั้งสำเร็จและล้มเหลว)
            self._cleanup_build_dirs(q)

            if proc.returncode == 0:
                shutil.copy(ENV_PATH, os.path.join(DIST_DIR, ".env"))
                q.put(("done", None))
            else:
                q.put(("error", f"exit code {proc.returncode}"))

        except Exception as e:
            q.put(("error", str(e)))

    def _cleanup_build_dirs(self, q):
        """ลบ .build และ .onefile-build ที่ nuitka ทิ้งไว้หลัง compile"""
        suffixes = (".build", ".onefile-build")
        # หาใน dist/ และ project root (nuitka บางเวอร์ชันสร้างคนละที่)
        search_dirs = [DIST_DIR, PROJECT_ROOT]
        for search in search_dirs:
            if not os.path.isdir(search):
                continue
            for entry in os.listdir(search):
                if any(entry.endswith(s) for s in suffixes):
                    full = os.path.join(search, entry)
                    try:
                        shutil.rmtree(full)
                        q.put(("line", f"  ลบ {entry} แล้ว"))
                    except Exception as e:
                        q.put(("line", f"  ลบ {entry} ไม่ได้: {e}"))

    def _poll_log(self):
        while not self._build_queue.empty():
            kind, data = self._build_queue.get()
            if kind == "line":
                self._log(data, self._detect_color(data))
            elif kind == "done":
                self._log("\n✓ Build สำเร็จ!", "green")
                self._log(f"  → {DIST_DIR}\\SynaptaHubAgent.exe", "green")
                self._log("  → คัดลอก .env ไปไว้ใน dist/ แล้ว", "green")
                self._finish_build()
                return
            elif kind == "error":
                self._log(f"\n✗ Build ล้มเหลว: {data}", "red")
                self._finish_build()
                return
        self.after(100, self._poll_log)

    def _finish_build(self):
        self._building = False
        self.build_btn.configure(state="normal")
        self.progress.stop()
        self.progress.set(0)
        self.status_label.configure(text="เสร็จแล้ว — ดูผลลัพธ์ด้านบน")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _log(self, text, tag=None):
        self.log_box.configure(state="normal")
        if tag:
            self.log_box._textbox.insert("end", text + "\n", tag)
        else:
            self.log_box.insert("end", text + "\n")
        self.log_box.configure(state="disabled")
        self.log_box._textbox.see("end")

    def _detect_color(self, line):
        lo = line.lower()
        if any(w in lo for w in ("error", "failed", "traceback", "exception",
                                  "cannot", "could not", "invalid")):
            return "red"
        if any(w in lo for w in ("done", "success", "✓", "finished",
                                  "completed", "successfully", "created")):
            return "green"
        if line.startswith(("Nuitka", ">>>", "INFO", "NOTE")):
            return "dim"
        return None


if __name__ == "__main__":
    app = HubBuilder()
    app.mainloop()
