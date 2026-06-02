#!/usr/bin/env python3
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent
MANAGER = ROOT / "proxy_manager.py"


class ProxyManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex Proxy Manager")
        self.geometry("720x520")
        self.minsize(640, 440)
        self.configure(bg="#f4f5f7")

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("TFrame", background="#f4f5f7")
        self.style.configure("Title.TLabel", background="#f4f5f7", font=("Helvetica", 20, "bold"))
        self.style.configure("Hint.TLabel", background="#f4f5f7", foreground="#596170")
        self.style.configure("Status.TLabel", background="#ffffff", font=("Menlo", 12))
        self.style.configure("TButton", font=("Helvetica", 13), padding=(12, 8))

        header = ttk.Frame(self)
        header.pack(fill="x", padx=22, pady=(20, 8))

        ttk.Label(header, text="Codex Proxy Manager", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="用 Xray 接管 V2BOX 那条 CDN 节点，并自动配置 macOS Wi-Fi 系统代理。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=22, pady=12)

        self.buttons = []
        actions = [
            ("启动并接管", ["start"], "启动 Xray，并把 Wi-Fi 系统代理切到 127.0.0.1:56542"),
            ("仅启动核心", ["start", "--no-system-proxy"], "只开本地代理，不改系统代理"),
            ("停止并关闭代理", ["stop"], "停止 Xray，并关闭 Wi-Fi 系统代理"),
            ("切回 Clash", ["proxy-clash"], "把 Wi-Fi 系统代理切回 127.0.0.1:7890"),
            ("查看状态", ["status"], "查看 Xray、端口和 Wi-Fi 代理状态"),
            ("测试出口", ["test", "https://api.ipify.org"], "测试当前管理器代理出口 IP"),
        ]
        for i, (label, args, tip) in enumerate(actions):
            btn = ttk.Button(controls, text=label, command=lambda a=args: self.run_action(a))
            btn.grid(row=i // 3, column=i % 3, sticky="ew", padx=6, pady=6)
            self.buttons.append(btn)
            ToolTip(btn, tip)

        for col in range(3):
            controls.columnconfigure(col, weight=1)

        card = ttk.Frame(self, style="TFrame")
        card.pack(fill="both", expand=True, padx=22, pady=(6, 22))

        self.output = tk.Text(
            card,
            wrap="word",
            height=16,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief="flat",
            padx=14,
            pady=12,
            font=("Menlo", 12),
        )
        self.output.pack(fill="both", expand=True)
        self.output.insert("end", "Ready.\n")

        self.after(300, lambda: self.run_action(["status"], quiet=True))

    def log(self, text):
        self.output.insert("end", text)
        self.output.see("end")

    def set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in self.buttons:
            btn.configure(state=state)

    def run_action(self, args, quiet=False):
        if not MANAGER.exists():
            messagebox.showerror("Missing File", f"找不到 {MANAGER}")
            return

        self.set_buttons(False)
        if not quiet:
            self.log(f"\n$ proxy_manager.py {' '.join(args)}\n")

        def worker():
            try:
                proc = subprocess.run(
                    [sys.executable, str(MANAGER), *args],
                    cwd=str(ROOT),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=45,
                )
                output = proc.stdout or ""
                if proc.returncode != 0:
                    output += f"\n[exit {proc.returncode}]\n"
            except Exception as exc:
                output = f"Error: {exc}\n"
            self.after(0, lambda: self.finish(output))

        threading.Thread(target=worker, daemon=True).start()

    def finish(self, output):
        self.log(output if output.endswith("\n") else output + "\n")
        self.set_buttons(True)


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip,
            text=self.text,
            bg="#1f2937",
            fg="#ffffff",
            padx=8,
            pady=5,
            font=("Helvetica", 11),
            justify="left",
        )
        label.pack()

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


if __name__ == "__main__":
    ProxyManagerApp().mainloop()
