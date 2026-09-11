from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .config import PIPELINE_ROOT, load_config
from .storage import rotate_file, runtime_paths


APP_ID = "bangla-ocr"


@dataclass(frozen=True)
class ProbeResult:
    state: str
    detail: str


@dataclass(frozen=True)
class StartupEvent:
    state: str
    title: str
    detail: str
    progress: int
    waiting: bool = False


def application_url(host: str, port: int) -> str:
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{address}:{port}"


def probe_application(
    host: str,
    port: int,
    *,
    timeout: float = 0.8,
) -> ProbeResult:
    url = f"{application_url(host, port)}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return ProbeResult("occupied", f"The port returned HTTP {exc.code}.")
    except (OSError, urllib.error.URLError):
        return ProbeResult("offline", "The local service is not running.")

    try:
        payload = json.loads(body)
    except ValueError:
        return ProbeResult("occupied", "Another service is using the local address.")

    if (
        response.status == 200
        and payload.get("application") == APP_ID
        and payload.get("status") == "ready"
    ):
        reported_root = str(payload.get("application_root", ""))
        try:
            same_root = (
                bool(reported_root)
                and Path(reported_root).resolve() == PIPELINE_ROOT.resolve()
            )
        except (OSError, ValueError):
            same_root = False
        if not same_root:
            return ProbeResult(
                "occupied",
                "A different Bangla OCR installation is using the local address.",
            )
        return ProbeResult("ready", str(payload.get("version", "")))
    return ProbeResult("occupied", "Another service is using the local address.")


def server_command(host: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "bangla_ocr",
        "app",
        "--host",
        host,
        "--port",
        str(port),
        "--no-browser",
    ]


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


class LauncherWindow:
    background = "#f5f6f8"
    surface = "#ffffff"
    text = "#17191d"
    muted = "#626975"
    border = "#d9dde3"
    accent = "#1f5eff"
    danger = "#b42318"

    def __init__(self, host: str, port: int) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.host = host
        self.port = port
        self.url = application_url(host, port)
        self.events: queue.Queue[StartupEvent] = queue.Queue()
        self.stop_requested = threading.Event()
        self.server_process: subprocess.Popen[bytes] | None = None
        self.starting = False

        self.root = tk.Tk()
        self.root.title("Bangla OCR")
        self.root.geometry("560x350")
        self.root.minsize(520, 330)
        self.root.configure(background=self.background)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Launcher.Horizontal.TProgressbar",
            troughcolor="#e7e9ed",
            background=self.accent,
            bordercolor="#e7e9ed",
            lightcolor=self.accent,
            darkcolor=self.accent,
            thickness=6,
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 9),
            foreground="#ffffff",
            background=self.accent,
            borderwidth=0,
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#174bd1"), ("disabled", "#aeb9d4")],
        )
        style.configure(
            "Quiet.TButton",
            font=("Segoe UI", 10),
            padding=(15, 9),
            foreground=self.text,
            background=self.surface,
            bordercolor=self.border,
            borderwidth=1,
        )
        style.map("Quiet.TButton", background=[("active", "#edf0f4")])

        frame = tk.Frame(
            self.root,
            background=self.surface,
            highlightbackground=self.border,
            highlightthickness=1,
        )
        frame.pack(fill="both", expand=True, padx=24, pady=24)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            frame,
            text="Bangla OCR",
            font=("Segoe UI", 17, "bold"),
            foreground=self.text,
            background=self.surface,
        ).grid(row=0, column=0, sticky="w", padx=28, pady=(26, 2))
        tk.Label(
            frame,
            text="Local document workspace",
            font=("Segoe UI", 9),
            foreground=self.muted,
            background=self.surface,
        ).grid(row=1, column=0, sticky="w", padx=28)

        self.title_label = tk.Label(
            frame,
            text="Preparing the application",
            font=("Segoe UI", 12, "bold"),
            foreground=self.text,
            background=self.surface,
        )
        self.title_label.grid(row=2, column=0, sticky="w", padx=28, pady=(38, 4))
        self.detail_label = tk.Label(
            frame,
            text="Checking the local service.",
            font=("Segoe UI", 10),
            foreground=self.muted,
            background=self.surface,
            anchor="w",
            justify="left",
            wraplength=440,
        )
        self.detail_label.grid(row=3, column=0, sticky="ew", padx=28)

        self.progress = ttk.Progressbar(
            frame,
            style="Launcher.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=8,
        )
        self.progress.grid(row=4, column=0, sticky="ew", padx=28, pady=(22, 24))

        self.actions = tk.Frame(frame, background=self.surface)
        self.actions.grid(row=5, column=0, sticky="w", padx=24, pady=(0, 24))
        self.open_button = ttk.Button(
            self.actions,
            text="Open Bangla OCR",
            style="Primary.TButton",
            command=self.open_application,
        )
        self.copy_button = ttk.Button(
            self.actions,
            text="Copy address",
            style="Quiet.TButton",
            command=self.copy_address,
        )
        self.retry_button = ttk.Button(
            self.actions,
            text="Retry",
            style="Primary.TButton",
            command=self.start,
        )
        self.log_button = ttk.Button(
            self.actions,
            text="Open log",
            style="Quiet.TButton",
            command=self.open_log,
        )

        self.root.after(80, self.consume_events)
        self.root.after(120, self.start)

    @property
    def log_path(self) -> Path:
        config = load_config()
        return runtime_paths(config)["logs"] / "application.log"

    def show_actions(self, *names: str) -> None:
        for button in (
            self.open_button,
            self.copy_button,
            self.retry_button,
            self.log_button,
        ):
            button.grid_forget()
        available = {
            "open": self.open_button,
            "copy": self.copy_button,
            "retry": self.retry_button,
            "log": self.log_button,
        }
        for column, name in enumerate(names):
            available[name].grid(row=0, column=column, padx=4)

    def start(self) -> None:
        if self.starting:
            return
        self.starting = True
        self.stop_requested.clear()
        self.show_actions()
        thread = threading.Thread(target=self.start_service, daemon=True)
        thread.start()

    def send(self, event: StartupEvent) -> None:
        self.events.put(event)

    def start_service(self) -> None:
        self.send(
            StartupEvent(
                "checking",
                "Checking the local service",
                "Looking for an existing Bangla OCR session.",
                18,
            )
        )
        probe = probe_application(self.host, self.port)
        if probe.state == "ready":
            self.send(
                StartupEvent(
                    "ready",
                    "Bangla OCR is ready",
                    f"The existing local session is available at {self.url}",
                    100,
                )
            )
            return
        if probe.state == "occupied":
            self.send(
                StartupEvent(
                    "failed",
                    "The local address is already in use",
                    probe.detail,
                    0,
                )
            )
            return
        if self.stop_requested.is_set():
            return

        self.send(
            StartupEvent(
                "starting",
                "Starting the local service",
                "This usually takes a few seconds.",
                46,
                waiting=True,
            )
        )
        try:
            log_path = self.log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            rotate_file(log_path, max_bytes=8 * 1024 * 1024, backups=3)
            with log_path.open("ab") as log_file:
                creation_flags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                )
                self.server_process = subprocess.Popen(
                    server_command(self.host, self.port),
                    cwd=str(PIPELINE_ROOT),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
        except OSError as exc:
            self.send(
                StartupEvent(
                    "failed",
                    "Bangla OCR could not start",
                    str(exc),
                    0,
                )
            )
            return

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not self.stop_requested.wait(0.3):
            if self.server_process.poll() is not None:
                self.send(
                    StartupEvent(
                        "failed",
                        "The local service stopped during startup",
                        "Open the application log for the technical details.",
                        0,
                    )
                )
                return
            probe = probe_application(self.host, self.port)
            if probe.state == "ready":
                self.send(
                    StartupEvent(
                        "ready",
                        "Bangla OCR is ready",
                        f"Open the private local workspace at {self.url}",
                        100,
                    )
                )
                return
            if probe.state == "occupied":
                stop_process_tree(self.server_process)
                self.send(
                    StartupEvent(
                        "failed",
                        "The local address became unavailable",
                        probe.detail,
                        0,
                    )
                )
                return

        if not self.stop_requested.is_set() and self.server_process.poll() is None:
            stop_process_tree(self.server_process)
            self.send(
                StartupEvent(
                    "failed",
                    "Bangla OCR took too long to start",
                    "The service was stopped safely. Check the log, then retry.",
                    0,
                )
            )

    def consume_events(self) -> None:
        try:
            while True:
                self.apply_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self.consume_events)

    def apply_event(self, event: StartupEvent) -> None:
        self.title_label.configure(text=event.title)
        self.detail_label.configure(
            text=event.detail,
            foreground=self.danger if event.state == "failed" else self.muted,
        )
        if event.waiting:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=event.progress)
        if event.state == "ready":
            self.starting = False
            self.show_actions("open", "copy")
        elif event.state == "failed":
            self.starting = False
            self.show_actions("retry", "log")

    def open_application(self) -> None:
        webbrowser.open(self.url)

    def copy_address(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url)
        self.detail_label.configure(text=f"Copied {self.url}", foreground=self.muted)

    def open_log(self) -> None:
        path = self.log_path
        if path.exists():
            os.startfile(path)
        else:
            self.detail_label.configure(
                text="No application log has been created yet.",
                foreground=self.muted,
            )

    def close(self) -> None:
        from tkinter import messagebox

        if self.server_process is not None and self.server_process.poll() is None:
            should_stop = messagebox.askyesno(
                "Stop Bangla OCR?",
                "Closing this window will stop the local application. "
                "An active OCR job can be resumed later.",
                parent=self.root,
            )
            if not should_stop:
                return
        self.stop_requested.set()
        if self.server_process is not None and self.server_process.poll() is None:
            stop_process_tree(self.server_process)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_launcher(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    LauncherWindow(host, port).run()
