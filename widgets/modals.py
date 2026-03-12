import os
import shlex
import subprocess
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DirectoryTree,
    Input,
    Pretty,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)


class RowDetailsModal(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("enter", "close", "Close"),
        ("c", "copy_url", "Copy URL"),
    ]

    def __init__(self, details: dict) -> None:
        super().__init__()
        self.details = details

    def compose(self) -> ComposeResult:
        with Vertical(id="details_modal"):
            yield Static("Details", id="details_title")
            with VerticalScroll(id="details_scroll"):
                yield Pretty(self.details, id="details_body")
            with Horizontal(id="details_actions"):
                yield Button(
                    "Copy URL",
                    id="details_copy_url",
                    variant="success",
                    flat=True,
                    compact=True,
                )
                yield Button(
                    "Close",
                    id="details_close",
                    variant="primary",
                    flat=True,
                    compact=True,
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "details_copy_url":
            self.action_copy_url()
        elif event.button.id == "details_close":
            self.dismiss()

    def action_copy_url(self) -> None:
        url = str(self.details.get("url") or "").strip()
        if not url:
            self.app.notify("No URL found in details.", severity="warning")
            return
        try:
            self.app.copy_to_clipboard(url)
            self.app.notify("URL copied to clipboard.", severity="information")
        except Exception as exc:
            self.app.notify(f"Copy failed: {exc}", severity="error")

    def action_close(self) -> None:
        self.dismiss()


class SettingsModal(ModalScreen[dict | None]):
    BINDINGS = [
        ("escape", "close", "Cancel"),
        ("enter", "save", "Save"),
    ]

    def __init__(self, current: dict) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_modal"):
            yield Static("Runtime Configuration", id="settings_title")
            with Horizontal(id="settings_row_top"):
                with Vertical(classes="settings_group"):
                    yield Static("Concurrency", classes="settings_label")
                    yield Input(str(self.current["concurrency"]), id="cfg_concurrency")
                with Vertical(classes="settings_group"):
                    yield Static("Xray Local Base Port", classes="settings_label")
                    yield Input(str(self.current["base_port"]), id="cfg_base_port")
                with Vertical(classes="settings_group"):
                    yield Static("Request Timeout (sec)", classes="settings_label")
                    yield Input(str(self.current["timeout"]), id="cfg_timeout")
            with Vertical(id="settings_row_bottom"):
                yield Static("Test URL", classes="settings_label")
                yield Input(self.current["test_url"], id="cfg_test_url")
            with Horizontal(id="settings_row_dns"):
                with Vertical(classes="settings_group"):
                    yield Static("DNS Retries", classes="settings_label")
                    yield Input(str(self.current["dns_retries"]), id="cfg_dns_retries")
                with Vertical(classes="settings_group"):
                    yield Static("DNS Retry Interval (ms)", classes="settings_label")
                    yield Input(
                        str(self.current["dns_retry_interval_ms"]),
                        id="cfg_dns_retry_interval_ms",
                    )
            with Horizontal(id="settings_actions"):
                yield Button(
                    "Save",
                    id="settings_save",
                    variant="primary",
                    flat=True,
                    compact=True,
                )
                yield Button(
                    "Cancel",
                    id="settings_cancel",
                    variant="default",
                    flat=True,
                    compact=True,
                )

    def on_mount(self) -> None:
        self.query_one("#cfg_concurrency", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings_save":
            self.action_save()
        elif event.button.id == "settings_cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {
            "cfg_concurrency",
            "cfg_base_port",
            "cfg_timeout",
            "cfg_test_url",
            "cfg_dns_retries",
            "cfg_dns_retry_interval_ms",
        }:
            self.action_save()

    def action_save(self) -> None:
        self.dismiss(
            {
                "concurrency": self.query_one("#cfg_concurrency", Input).value.strip(),
                "test_url": self.query_one("#cfg_test_url", Input).value.strip(),
                "base_port": self.query_one("#cfg_base_port", Input).value.strip(),
                "timeout": self.query_one("#cfg_timeout", Input).value.strip(),
                "dns_retries": self.query_one("#cfg_dns_retries", Input).value.strip(),
                "dns_retry_interval_ms": self.query_one(
                    "#cfg_dns_retry_interval_ms", Input
                ).value.strip(),
            }
        )

    def action_close(self) -> None:
        self.dismiss(None)


class ExportModal(ModalScreen[dict | None]):
    BINDINGS = [
        ("escape", "close", "Cancel"),
        ("enter", "save", "Save"),
    ]

    def __init__(self, start_directory: Path, default_filename: str) -> None:
        super().__init__()
        self.selected_directory = start_directory
        self.default_filename = default_filename

    def compose(self) -> ComposeResult:
        with Vertical(id="export_modal"):
            yield Static("Export Links", id="export_title")
            yield Static("Choose output directory:", classes="export_label")
            yield DirectoryTree(str(self.selected_directory), id="export_tree")
            yield Static(
                f"Selected Directory: {self.selected_directory}",
                id="export_selected_dir",
            )
            yield Static("Output file name:", classes="export_label")
            yield Input(self.default_filename, id="export_filename")
            yield Checkbox(
                "Include PARTIAL links (green/orange latency)",
                id="export_include_partial",
                value=False,
            )
            yield Static("", id="export_error")
            with Horizontal(id="export_actions"):
                yield Button(
                    "Save",
                    id="export_save",
                    variant="primary",
                    flat=True,
                    compact=True,
                )
                yield Button(
                    "Cancel",
                    id="export_cancel",
                    variant="default",
                    flat=True,
                    compact=True,
                )

    def on_mount(self) -> None:
        self.query_one("#export_filename", Input).focus()

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self.selected_directory = event.path
        self._update_selected_directory_label()

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.selected_directory = event.path.parent
        self.query_one("#export_filename", Input).value = event.path.name
        self._update_selected_directory_label()

    def _update_selected_directory_label(self) -> None:
        self.query_one("#export_selected_dir", Static).update(
            f"Selected Directory: {self.selected_directory}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export_save":
            self.action_save()
        elif event.button.id == "export_cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "export_filename":
            self.action_save()

    def action_save(self) -> None:
        filename = self.query_one("#export_filename", Input).value.strip()
        if not filename:
            self.query_one("#export_error", Static).update("File name is required.")
            return
        if "/" in filename or "\\" in filename:
            self.query_one("#export_error", Static).update(
                "Enter only a file name, not a full path."
            )
            return
        self.dismiss(
            {
                "directory": str(self.selected_directory),
                "filename": filename,
                "include_partial": self.query_one("#export_include_partial", Checkbox).value,
            }
        )

    def action_close(self) -> None:
        self.dismiss(None)


class LogsModal(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("l", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def __init__(self, entries: list) -> None:
        super().__init__()
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(id="logs_modal"):
            yield Static("Live Logs", id="logs_title")
            yield RichLog(id="logs_view", wrap=True, highlight=True, markup=False)
            with Horizontal(id="logs_actions"):
                yield Button(
                    "Close",
                    id="logs_close",
                    variant="primary",
                    flat=True,
                    compact=True,
                )

    def on_mount(self) -> None:
        log = self.query_one("#logs_view", RichLog)
        for entry in self.entries:
            log.write(entry)
        log.focus()

    def append_line(self, line) -> None:
        self.query_one("#logs_view", RichLog).write(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "logs_close":
            self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class TerminalModal(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("t", "close", "Close"),
    ]

    def __init__(self, start_directory: Path) -> None:
        super().__init__()
        self.cwd = start_directory
        self._process: subprocess.Popen[str] | None = None
        self._running = False

    def compose(self) -> ComposeResult:
        with Vertical(id="terminal_modal"):
            yield Static("Terminal", id="terminal_title")
            yield Static(f"CWD: {self.cwd}", id="terminal_cwd")
            yield RichLog(id="terminal_log", wrap=False, highlight=False, markup=False)
            yield Input(placeholder="Type command and press Enter", id="terminal_input")
            with Horizontal(id="terminal_actions"):
                yield Button("Stop", id="terminal_stop", variant="warning", flat=True, compact=True)
                yield Button("Close", id="terminal_close", variant="primary", flat=True, compact=True)

    def on_mount(self) -> None:
        self._write_log("Embedded shell console (non-interactive).")
        self._write_log("Commands run in your current shell. Use 'cd <path>' to change directory.")
        try:
            self.query_one("#terminal_input", Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "terminal_input":
            return
        command = event.value.strip()
        event.input.value = ""
        if not command:
            return
        if command in {"exit", "quit"}:
            self.dismiss()
            return
        if command == "clear":
            try:
                self.query_one("#terminal_log", RichLog).clear()
            except Exception:
                pass
            return
        if command.startswith("cd"):
            self._handle_cd(command)
            return
        if self._running:
            self._write_log("A command is already running.")
            return

        self._write_log(Text(f"$ {command}", style="bold #9ecbff"))
        try:
            self.query_one("#terminal_input", Input).disabled = True
        except Exception:
            pass
        self._running = True
        self._run_command(command)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "terminal_close":
            self.action_close()
        elif event.button.id == "terminal_stop":
            self._stop_command()

    def action_close(self) -> None:
        self._stop_command()
        self.dismiss()

    def _handle_cd(self, command: str) -> None:
        parts = shlex.split(command)
        target = parts[1] if len(parts) > 1 else str(Path.home())
        new_cwd = Path(target)
        if not new_cwd.is_absolute():
            new_cwd = (self.cwd / new_cwd).resolve()
        if not new_cwd.exists() or not new_cwd.is_dir():
            self._write_log(f"cd: no such directory: {target}")
            return
        self.cwd = new_cwd
        try:
            self.query_one("#terminal_cwd", Static).update(f"CWD: {self.cwd}")
        except Exception:
            pass
        self._write_log(f"Changed directory to {self.cwd}")

    @work(thread=True, exclusive=True)
    def _run_command(self, command: str) -> None:
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            process = subprocess.Popen(
                [shell, "-lc", command],
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._process = process
            if process.stdout is not None:
                for line in process.stdout:
                    self.call_from_thread(self._append_output, line.rstrip("\n"))
            return_code = process.wait()
            self.call_from_thread(self._command_finished, return_code)
        except Exception as exc:
            self.call_from_thread(self._append_output, f"error: {exc}")
            self.call_from_thread(self._command_finished, 1)

    def _append_output(self, line: str) -> None:
        self._write_log(line)

    def _command_finished(self, return_code: int) -> None:
        self._running = False
        self._process = None
        try:
            self.query_one("#terminal_input", Input).disabled = False
            self.query_one("#terminal_input", Input).focus()
        except Exception:
            pass
        status_style = "green" if return_code == 0 else "red"
        self._write_log(Text(f"[exit {return_code}]", style=status_style))

    def _stop_command(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()

    def _write_log(self, message: str | Text) -> None:
        try:
            self.query_one("#terminal_log", RichLog).write(message)
        except Exception:
            pass


class ImportLinksModal(ModalScreen[dict | None]):
    BINDINGS = [
        ("escape", "close", "Cancel"),
        ("ctrl+s", "import_links", "Import"),
    ]

    def __init__(self, start_directory: Path | None = None) -> None:
        super().__init__()
        self.start_directory = start_directory or Path.cwd()
        self.selected_file: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="import_modal"):
            yield Static("Import Links", id="import_title")
            yield Static(
                "Supported: vless://, vmess://, t.me/socks, t.me/proxy, tg://proxy, dns as udp://ip[:port] or ip:port",
                id="import_hint",
            )
            with TabbedContent(initial="import_tab_paste", id="import_tabs"):
                with TabPane("Paste Links", id="import_tab_paste"):
                    yield TextArea(
                        "",
                        id="import_editor",
                        show_line_numbers=True,
                        line_number_start=1,
                        soft_wrap=False,
                        language=None,
                        theme="css",
                    )
                    with Horizontal(id="import_paste_actions"):
                        yield Button(
                            "Load Clipboard",
                            id="import_load_clipboard",
                            variant="primary",
                            flat=True,
                            compact=True,
                        )
                        yield Button(
                            "Clear",
                            id="import_clear_paste",
                            variant="default",
                            flat=True,
                            compact=True,
                        )
                with TabPane("From File", id="import_tab_file"):
                    yield Static("Select file:", id="import_file_hint")
                    yield DirectoryTree(str(self.start_directory), id="import_tree")
                    yield Static("Selected file: -", id="import_selected_file")
            yield Static("", id="import_error")
            with Horizontal(id="import_actions"):
                yield Button(
                    "Import",
                    id="import_save",
                    variant="primary",
                    flat=True,
                    compact=True,
                )
                yield Button(
                    "Cancel",
                    id="import_cancel",
                    variant="default",
                    flat=True,
                    compact=True,
                )

    def on_mount(self) -> None:
        self.query_one("#import_editor", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "import_save":
            self.action_import_links()
        elif event.button.id == "import_cancel":
            self.dismiss(None)
        elif event.button.id == "import_load_clipboard":
            self.action_load_clipboard()
        elif event.button.id == "import_clear_paste":
            self.query_one("#import_editor", TextArea).text = ""
            self._set_error("")

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.selected_file = event.path
        self.query_one("#import_selected_file", Static).update(
            f"Selected file: {self.selected_file}"
        )
        self._set_error("")

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self.selected_file = None
        self.query_one("#import_selected_file", Static).update("Selected file: -")

    def _set_error(self, message: str) -> None:
        self.query_one("#import_error", Static).update(message)

    def _read_clipboard_text(self) -> str:
        app_clipboard = (self.app.clipboard or "").strip()
        if app_clipboard:
            return app_clipboard
        try:
            import pyperclip

            value = pyperclip.paste()
            return value.strip() if isinstance(value, str) else ""
        except Exception:
            return ""

    def action_load_clipboard(self) -> None:
        content = self._read_clipboard_text()
        if not content:
            self._set_error(
                "Clipboard is empty or unavailable. Paste manually in this tab."
            )
            return
        self.query_one("#import_editor", TextArea).text = content
        self._set_error("")

    def action_import_links(self) -> None:
        active_tab = self.query_one("#import_tabs", TabbedContent).active or "import_tab_paste"

        if active_tab == "import_tab_file":
            if self.selected_file is None:
                self._set_error("Select a file first.")
                return
            try:
                content = self.selected_file.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
            except Exception as exc:
                self._set_error(f"Failed to read file: {exc}")
                return
        else:
            editor = self.query_one("#import_editor", TextArea)
            content = editor.text.strip()

        if not content:
            self._set_error("No links found in the selected source.")
            return
        self._set_error("")
        self.dismiss({"content": content})

    def action_close(self) -> None:
        self.dismiss(None)
