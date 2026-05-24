"""Init wizard — opens when `gitcraft init` is called with no arguments."""
from __future__ import annotations
from tkinter import filedialog
from typing import NamedTuple

import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class InitParams(NamedTuple):
    name: str
    path: str
    remote: str        # empty = no remote
    port: int
    key: str           # empty = default
    password: str      # empty = use key


def _browse_dir(entry: ctk.CTkEntry) -> None:
    d = filedialog.askdirectory(title="Select world folder")
    if d:
        entry.delete(0, "end")
        entry.insert(0, d)


def _browse_file(entry: ctk.CTkEntry) -> None:
    f = filedialog.askopenfilename(title="Select SSH key", initialdir="~/.ssh")
    if f:
        entry.delete(0, "end")
        entry.insert(0, f)


def run_init_wizard() -> InitParams | None:
    """Show the init wizard. Returns InitParams on confirm, None on cancel."""
    result: list[InitParams | None] = [None]

    root = ctk.CTk()
    root.title("GitCraft — Init World")
    root.resizable(False, False)

    pad = {"padx": 16, "pady": 6}

    # ── World name ────────────────────────────────────────────────────────────
    ctk.CTkLabel(root, text="World name", anchor="w").grid(
        row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(20, 2)
    )
    e_name = ctk.CTkEntry(root, width=360, placeholder_text="ForeverWorld")
    e_name.grid(row=1, column=0, columnspan=3, **pad)

    # ── Local path ────────────────────────────────────────────────────────────
    ctk.CTkLabel(root, text="Local path", anchor="w").grid(
        row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 2)
    )
    e_path = ctk.CTkEntry(root, width=304, placeholder_text="~/.minecraft/saves/ForeverWorld")
    e_path.grid(row=3, column=0, columnspan=2, padx=(16, 4), pady=6, sticky="w")
    ctk.CTkButton(root, text="Browse", width=52, command=lambda: _browse_dir(e_path)).grid(
        row=3, column=2, padx=(0, 16), pady=6
    )

    # ── Remote section ────────────────────────────────────────────────────────
    ctk.CTkLabel(root, text="Remote (optional)", anchor="w",
                 font=ctk.CTkFont(size=12, weight="bold")).grid(
        row=4, column=0, columnspan=3, sticky="w", padx=16, pady=(18, 2)
    )

    ctk.CTkLabel(root, text="user@host:/remote/path", anchor="w").grid(
        row=5, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 2)
    )
    e_remote = ctk.CTkEntry(root, width=360, placeholder_text="minecraft@myserver.com:/srv/gitcraft/ForeverWorld")
    e_remote.grid(row=6, column=0, columnspan=3, **pad)

    frame_opts = ctk.CTkFrame(root, fg_color="transparent")
    frame_opts.grid(row=7, column=0, columnspan=3, padx=16, pady=6, sticky="ew")

    ctk.CTkLabel(frame_opts, text="Port").grid(row=0, column=0, padx=(0, 6))
    e_port = ctk.CTkEntry(frame_opts, width=72)
    e_port.insert(0, "8765")
    e_port.grid(row=0, column=1, padx=(0, 24))

    ctk.CTkLabel(frame_opts, text="Password").grid(row=0, column=2, padx=(0, 6))
    e_password = ctk.CTkEntry(frame_opts, width=140, show="●",
                              placeholder_text="(or use SSH key)")
    e_password.grid(row=0, column=3)

    ctk.CTkLabel(frame_opts, text="SSH key").grid(row=1, column=0, padx=(0, 6), pady=(6, 0))
    e_key = ctk.CTkEntry(frame_opts, width=224, placeholder_text="~/.ssh/id_rsa (default)")
    e_key.grid(row=1, column=1, columnspan=3, padx=(0, 6), pady=(6, 0), sticky="w")
    ctk.CTkButton(frame_opts, text="Browse", width=52,
                  command=lambda: _browse_file(e_key)).grid(row=1, column=4, pady=(6, 0))

    # ── Error label ───────────────────────────────────────────────────────────
    lbl_err = ctk.CTkLabel(root, text="", text_color="#e05555", anchor="w")
    lbl_err.grid(row=8, column=0, columnspan=3, padx=16, sticky="w")

    # ── Buttons ───────────────────────────────────────────────────────────────
    def on_confirm():
        name = e_name.get().strip()
        path = e_path.get().strip()
        remote = e_remote.get().strip()
        key = e_key.get().strip()
        password = e_password.get()

        try:
            port = int(e_port.get().strip())
        except ValueError:
            lbl_err.configure(text="Port must be a number.")
            return

        if not name:
            lbl_err.configure(text="World name is required.")
            return
        if not path:
            lbl_err.configure(text="Local path is required.")
            return
        if remote and ("@" not in remote or ":" not in remote):
            lbl_err.configure(text="Remote must be user@host:/path")
            return

        result[0] = InitParams(name=name, path=path, remote=remote, port=port,
                               key=key, password=password)
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = ctk.CTkFrame(root, fg_color="transparent")
    btn_frame.grid(row=9, column=0, columnspan=3, pady=(8, 20))
    ctk.CTkButton(btn_frame, text="Cancel", width=100, fg_color="transparent",
                  border_width=1, command=on_cancel).grid(row=0, column=0, padx=8)
    ctk.CTkButton(btn_frame, text="Init", width=100, command=on_confirm).grid(
        row=0, column=1, padx=8
    )

    root.mainloop()
    return result[0]
