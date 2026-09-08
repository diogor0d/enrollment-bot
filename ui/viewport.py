"""Separate diagnostic viewport for visual dry-run mode."""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import tkinter as tk
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageTk


class LiveViewport:
    """Show annotated desktop captures in a dedicated Tk window."""

    def __init__(self, screen_width: int, screen_height: int) -> None:
        self.screen_width = max(int(screen_width), 1)
        self.screen_height = max(int(screen_height), 1)
        self.window_width = min(1000, self.screen_width)
        self.window_height = min(680, self.screen_height)
        self._commands: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._ready = threading.Event()
        self._root: tk.Tk | None = None
        self._image_label: tk.Label | None = None
        self._status_label: tk.Label | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        """Start the daemon thread that owns the viewport window."""
        with self._state_lock:
            if self._closed or (self._thread and self._thread.is_alive()):
                return
            self._thread = threading.Thread(
                target=self._run,
                name="visual-viewport",
                daemon=True,
            )
            self._thread.start()
        self._ready.wait(timeout=2.0)

    def hide(self) -> None:
        """Hide the viewport synchronously before desktop capture."""
        completed = threading.Event()
        self._enqueue("hide", completed)
        completed.wait(timeout=0.75)

    def show(self, screenshot: Image.Image, box: Any, label: str, color: str) -> None:
        """Display a full-screen capture annotated with one detected target."""
        try:
            values = self._box_values(box)
            completed = threading.Event()
            self._enqueue(
                "show",
                screenshot.copy(),
                values,
                str(label),
                str(color),
                completed,
            )
            completed.wait(timeout=0.75)
        except Exception as exc:
            self._record_error(exc)

    def close(self) -> None:
        """Request orderly destruction of the viewport window."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread and thread.is_alive():
            self._commands.put(("close", ()))

    @staticmethod
    def _box_values(box: Any) -> tuple[int, int, int, int]:
        try:
            values = (box.left, box.top, box.width, box.height)
        except AttributeError:
            values = tuple(box)
        if len(values) != 4:
            raise ValueError("box must contain left, top, width, and height")
        return tuple(int(value) for value in values)  # type: ignore[return-value]

    def _enqueue(self, command: str, *args: Any) -> None:
        with self._state_lock:
            if self._closed:
                return
        self.start()
        self._commands.put((command, args))

    def _run(self) -> None:
        try:
            root = tk.Tk()
            self._root = root
            root.title("Enrollment Bot - Visual Viewport")
            root.geometry(f"{self.window_width}x{self.window_height}+30+30")
            root.configure(bg="#111827")
            root.attributes("-topmost", True)
            root.protocol("WM_DELETE_WINDOW", root.withdraw)

            heading = tk.Label(
                root,
                text="Visual dry-run - latest detected target",
                bg="#111827",
                fg="#f9fafb",
                font=("Segoe UI", 13, "bold"),
                anchor="w",
                padx=14,
                pady=10,
            )
            heading.pack(fill="x")

            image_label = tk.Label(
                root,
                text="Waiting for the automation to start...",
                bg="#030712",
                fg="#9ca3af",
                font=("Segoe UI", 13),
            )
            image_label.pack(fill="both", expand=True, padx=12)
            self._image_label = image_label

            status_label = tk.Label(
                root,
                text="The final Save click remains disabled in visual mode.",
                bg="#111827",
                fg="#d1d5db",
                font=("Segoe UI", 10),
                anchor="w",
                padx=14,
                pady=10,
            )
            status_label.pack(fill="x")
            self._status_label = status_label

            root.update_idletasks()
            if os.name == "nt":
                self._configure_windows_window(root)

            self._ready.set()
            root.after(25, self._drain_queue)
            root.mainloop()
        except Exception as exc:
            self._record_error(exc)
        finally:
            self._ready.set()
            self._root = None
            self._image_label = None
            self._status_label = None
            self._photo = None

    def _drain_queue(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            while True:
                command, args = self._commands.get_nowait()
                if command == "hide":
                    root.withdraw()
                    args[0].set()
                elif command == "show":
                    screenshot, box, label, color, completed = args
                    self._render_frame(screenshot, box, label, color)
                    root.deiconify()
                    root.attributes("-topmost", True)
                    root.lift()
                    root.update_idletasks()
                    completed.set()
                elif command == "close":
                    root.destroy()
                    return
        except queue.Empty:
            pass
        except Exception as exc:
            self._record_error(exc)
        root.after(25, self._drain_queue)

    def _render_frame(
        self,
        screenshot: Image.Image,
        box: tuple[int, int, int, int],
        label: str,
        color: str,
    ) -> None:
        image_label = self._image_label
        status_label = self._status_label
        if image_label is None or status_label is None:
            return

        frame = screenshot.convert("RGB")
        draw = ImageDraw.Draw(frame)
        left, top, width, height = box
        right, bottom = left + width, top + height
        line_width = max(round(frame.width / 320), 4)
        draw.rectangle(
            (left, top, right, bottom),
            outline=color,
            width=line_width,
        )

        font = self._load_font(max(round(frame.width / 80), 18))
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_left = max(min(left, frame.width - text_width - 24), 0)
        label_top = max(top - text_height - 22, 0)
        draw.rounded_rectangle(
            (
                label_left,
                label_top,
                label_left + text_width + 20,
                label_top + text_height + 14,
            ),
            radius=7,
            fill="#111827",
            outline=color,
            width=max(line_width // 2, 2),
        )
        draw.text(
            (label_left + 10, label_top + 5),
            label,
            fill="white",
            font=font,
        )

        available_width = max(self.window_width - 36, 1)
        available_height = max(self.window_height - 120, 1)
        frame.thumbnail(
            (available_width, available_height),
            Image.Resampling.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(frame)
        image_label.configure(image=self._photo, text="")
        status_label.configure(
            text=(
                f"{label}  |  x={left}, y={top}, "
                f"width={width}, height={height}"
            )
        )

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        for name in ("segoeuib.ttf", "arialbd.ttf"):
            try:
                return ImageFont.truetype(name, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _configure_windows_window(self, root: tk.Tk) -> None:
        """Place the viewport on a second monitor and keep clicks non-blocking."""
        user32 = ctypes.windll.user32
        tk_hwnd = root.winfo_id()
        hwnd = user32.GetParent(tk_hwnd) or tk_hwnd

        bounds = self._secondary_monitor_bounds()
        if bounds is not None:
            left, top, right, bottom = bounds
            x = left + max((right - left - self.window_width) // 2, 0)
            y = top + max((bottom - top - self.window_height) // 2, 0)
            user32.SetWindowPos(
                hwnd,
                -1,
                x,
                y,
                self.window_width,
                self.window_height,
                0x0010,
            )

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = get_style(hwnd, -20)
        set_style(hwnd, -20, style | 0x00000020 | 0x08000000)

        # The viewport is hidden before every capture as the primary safeguard.
        # This additionally excludes it on Windows builds that support it.
        user32.SetWindowDisplayAffinity(hwnd, 0x00000011)

    @staticmethod
    def _secondary_monitor_bounds() -> tuple[int, int, int, int] | None:
        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", Rect),
                ("rcWork", Rect),
                ("dwFlags", ctypes.c_ulong),
            ]

        monitors: list[tuple[int, int, int, int, bool]] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(Rect),
            ctypes.c_void_p,
        )

        def collect(monitor, _dc, _rect, _data):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                monitors.append(
                    (
                        work.left,
                        work.top,
                        work.right,
                        work.bottom,
                        bool(info.dwFlags & 1),
                    )
                )
            return 1

        callback = callback_type(collect)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)
        for left, top, right, bottom, primary in monitors:
            if not primary:
                return left, top, right, bottom
        return None

    def _record_error(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}".strip()
        self.last_error = message[:240]
        print(f"LiveViewport: {self.last_error}")
