import argparse
import os
import subprocess
import textwrap
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import List, Optional

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Static
try:
    from textual.widgets import LoadingIndicator  # Textual >= 8
except Exception:
    from textual.widgets._loading_indicator import LoadingIndicator  # Textual 6.x fallback

from helpers.checker import load_links, run_cli, test_link
from helpers.constants import (
    DEFAULT_CURL_TIMEOUT_SECONDS,
    DEFAULT_DNS_RETRIES,
    DEFAULT_DNS_RETRY_INTERVAL_MS,
    DEFAULT_MAX_CONCURRENT_CHECKS,
    DEFAULT_PROXY_BASE_PORT,
    DEFAULT_TEST_URL,
)
from helpers.proxy_links import parse_link
from helpers.xray import detect_xray
from models.vless_item import VlessItem
from widgets.modals import (
    ExportModal,
    FilterModal,
    ImportLinksModal,
    LogsModal,
    RowDetailsModal,
    SettingsModal,
)

class VlessTesterApp(App[None]):
    _CSS_FILE = Path(__file__).resolve().with_name("oppy.tcss")
    _CSS_FILE_FALLBACK = Path(__file__).resolve().parent / "widgets" / "oppy.tcss"
    CSS_PATH = str(_CSS_FILE if _CSS_FILE.exists() else _CSS_FILE_FALLBACK)
    TITLE = "OPPY — Mission Control for Proxy Links"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "toggle_scan", "Start/Stop checks"),
        ("f", "open_filter", "Filter"),
        ("r", "reset_scan", "Reset scan"),
        ("e", "export_healthy", "Export"),
        ("i", "open_import_links", "Import links"),
        ("c", "open_settings", "Config"),
        ("a", "toggle_follow", "Auto-follow"),
        ("p", "toggle_pause", "Pause/Resume"),
        ("t", "toggle_terminal", "Terminal"),
        ("l", "open_logs", "Logs"),
        ("enter", "show_selected_details", "Details"),
    ]

    def __init__(
        self, items: List[VlessItem], input_file: Optional[str], export_file: Optional[str]
    ) -> None:
        super().__init__()
        self.items = items
        self.input_file = input_file or "(import-only)"
        if export_file:
            self.export_file = export_file
        elif input_file:
            self.export_file = str(Path(input_file).with_name("healthy_links.txt"))
        else:
            self.export_file = str(Path.cwd() / "healthy_links.txt")
        self.checked_count = 0
        self.healthy_count = 0
        self.partial_count = 0
        self.failed_count = 0
        self.status_message = "Idle."
        self.xray_available, self.xray_version = detect_xray()
        self.max_concurrent_checks = DEFAULT_MAX_CONCURRENT_CHECKS
        self.proxy_base_port = DEFAULT_PROXY_BASE_PORT
        self.test_url = DEFAULT_TEST_URL
        self.curl_timeout_seconds = DEFAULT_CURL_TIMEOUT_SECONDS
        self.dns_retries = DEFAULT_DNS_RETRIES
        self.dns_retry_interval_ms = DEFAULT_DNS_RETRY_INTERVAL_MS
        self.start_directory = Path.cwd()
        self.auto_follow_rows = True
        self.active_row_index: Optional[int] = None
        self._log_entries: list[Text] = []
        self._logs_modal: Optional[LogsModal] = None
        self.latency_history: list[float] = []
        self.latency_valid_history: list[float] = []
        self._paused = False
        self._loader_mode = "idle"
        self._worker = None
        self.active_filters: dict[str, str] = {"type": "", "name": "", "server": ""}
        self.visible_item_indices: list[int] = list(range(len(self.items)))

    @staticmethod
    def _item_type(item: VlessItem) -> str:
        return str(item.parsed.get("protocol") or "vless").strip().lower()

    @staticmethod
    def _normalize_filters(filters: dict | None) -> dict[str, str]:
        raw = filters or {}
        return {
            "type": str(raw.get("type", "")).strip().lower(),
            "name": str(raw.get("name", "")).strip().lower(),
            "server": str(raw.get("server", "")).strip().lower(),
        }

    def _item_matches_filters(self, item: VlessItem, filters: dict | None = None) -> bool:
        normalized = self._normalize_filters(filters or self.active_filters)
        type_filter = normalized["type"]
        name_filter = normalized["name"]
        server_filter = normalized["server"]

        item_type = self._item_type(item)
        item_name = str(item.parsed.get("name") or "").strip().lower()
        item_server = str(item.parsed.get("server") or "").strip().lower()

        if type_filter and item_type != type_filter:
            return False
        if name_filter and name_filter not in item_name:
            return False
        if server_filter and server_filter not in item_server:
            return False
        return True

    def _recalculate_counters_from_items(self) -> None:
        self.healthy_count = sum(1 for item in self.items if item.status == "OK")
        self.partial_count = sum(1 for item in self.items if item.status == "PARTIAL")
        self.failed_count = sum(1 for item in self.items if item.status == "FAILED")
        self.checked_count = self.healthy_count + self.partial_count + self.failed_count

        self.latency_valid_history = [
            float(item.latency_ms)
            for item in self.items
            if item.latency_ms is not None
        ]
        self.latency_history = list(self.latency_valid_history)

    def _selected_item_index(self) -> Optional[int]:
        table = self.query_one(DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(self.visible_item_indices):
            return None
        return self.visible_item_indices[row]

    def _add_table_row(self, table: DataTable, item: VlessItem) -> None:
        v = item.parsed
        table.add_row(
            v.get("name") or "-",
            self._item_type(item),
            v.get("server") or "-",
            str(v.get("port") or "-"),
            v.get("type") or "-",
            v.get("security") or "-",
            v.get("sni") or "-",
            v.get("flow") or "-",
            self._status_text(item.status),
            self._latency_text(item.latency_ms),
            item.exit_ip or "-",
            self._reason_text(item.reason),
            key=str(item.index),
        )

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        selected_item_index = self._selected_item_index()
        table.clear(columns=False)
        self.visible_item_indices = []

        for item in self.items:
            if not self._item_matches_filters(item):
                continue
            self._add_table_row(table, item)
            self.visible_item_indices.append(item.index)

        if not self.visible_item_indices:
            return

        target_item_index = selected_item_index
        if target_item_index is None and self.active_row_index is not None:
            target_item_index = self.active_row_index

        if target_item_index in self.visible_item_indices:
            row_index = self.visible_item_indices.index(target_item_index)
        else:
            row_index = 0

        try:
            table.move_cursor(row=row_index, column=0, animate=False, scroll=False)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="app"):
            with Horizontal(id="summary"):
                with Vertical(id="summary_info_box"):
                    yield Static(id="stats", markup=True)
                with Vertical(id="summary_config_box"):
                    yield Static(id="runtime_config", markup=True)
                with Vertical(id="summary_meter_box"):
                    yield Static(id="status_meters")
            with Horizontal(id="trend_row"):
                yield Static("Latency Trend", id="latency_trend_title")
                yield Static("", id="latency_spark")
                yield Static("L: --  A: --  P: --", id="latency_stats")
            yield DataTable(id="table")
            with Horizontal(id="actions_row"):
                with Horizontal(id="actions_left"):
                    yield Button(
                        "Start checks",
                        id="scan",
                        variant="primary",
                        flat=True,
                        compact=True,
                    )
                    yield Button(
                        "Pause",
                        id="pause",
                        variant="primary",
                        flat=True,
                        compact=True,
                    )
                with Horizontal(id="actions_center"):
                    pass
                with Horizontal(id="actions_right"):
                    yield Button(
                        "Export",
                        id="export",
                        variant="success",
                        flat=True,
                        compact=True,
                    )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.zebra_stripes = True
        table.show_cursor = True
        table.cursor_type = "row"
        table.add_column("Name", key="name", width=19)
        table.add_column("Type", key="link_type", width=10)
        table.add_column("Server", key="server", width=23)
        table.add_column("Port", key="port", width=7)
        table.add_column("Net", key="net", width=7)
        table.add_column("Sec", key="sec", width=7)
        table.add_column("SNI", key="sni", width=19)
        table.add_column("Flow", key="flow", width=11)
        table.add_column("Status", key="status", width=11)
        table.add_column("Latency", key="latency", width=10)
        table.add_column("Exit IP", key="exit_ip", width=16)
        table.add_column("Reason", key="reason", width=44)

        self._refresh_table()

        self._update_stats()
        self._update_runtime_config()
        self._update_status_bars()
        self._update_latency_trend()
        self.query_one("#pause", Button).disabled = True
        self._set_action_loader("idle")
        table.focus()
        self._append_log(f"Loaded {len(self.items)} links from {self.input_file}")
        if not self.items:
            self.notify("No input file loaded. Press I to import links.", severity="information")
            self._append_log("No links loaded at startup. Use I to import links.", style="yellow")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = getattr(event, "cursor_row", -1)
        if row_index < 0 or row_index >= len(self.visible_item_indices):
            return
        self._show_details_by_index(self.visible_item_indices[row_index])

    def on_key(self, event: events.Key) -> None:
        if not self._is_worker_running() or not self.auto_follow_rows:
            return
        table = self.query_one(DataTable)
        nav_keys = {"up", "down", "pageup", "pagedown", "home", "end"}
        if self.focused is table and event.key in nav_keys:
            self._pause_auto_follow()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._pause_auto_follow_from_widget(getattr(event, "widget", None))

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._pause_auto_follow_from_widget(getattr(event, "widget", None))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "scan":
            self.action_toggle_scan()
        elif event.button.id == "pause":
            self.action_toggle_pause()
        elif event.button.id == "export":
            self.action_export_healthy()

    def action_open_logs(self) -> None:
        if self._logs_modal is not None:
            return
        modal = LogsModal(self._log_entries)
        self._logs_modal = modal
        self.push_screen(modal, self._on_logs_modal_closed)

    def _on_logs_modal_closed(self, _result: None) -> None:
        self._logs_modal = None

    def action_toggle_terminal(self) -> None:
        if os.name == "nt":
            shell = os.environ.get("COMSPEC", "cmd.exe")
            cmd = [shell]
        else:
            shell = os.environ.get("SHELL", "/bin/zsh")
            cmd = [shell, "-l"]
        self._append_log(f"Opening shell ({shell}). Type 'exit' to return.", style="cyan")
        with self.suspend():
            print("\n[OPPY] Interactive shell opened.")
            print("[OPPY] Type 'exit' to return to OPPY.\n", flush=True)
            subprocess.run(cmd, cwd=str(self.start_directory))
            print("\n[OPPY] Returning to OPPY...\n", flush=True)
        self._append_log("Returned from shell.")

    def action_open_settings(self) -> None:
        if (
            self._worker
            and not self._worker.is_cancelled
            and not self._worker.is_finished
        ):
            self.notify("Stop checks before editing config.", severity="warning")
            return
        self.push_screen(
            SettingsModal(
                {
                    "concurrency": self.max_concurrent_checks,
                    "test_url": self.test_url,
                    "base_port": self.proxy_base_port,
                    "timeout": self.curl_timeout_seconds,
                    "dns_retries": self.dns_retries,
                    "dns_retry_interval_ms": self.dns_retry_interval_ms,
                }
            ),
            self._apply_settings_result,
        )

    def action_open_import_links(self) -> None:
        if self._is_worker_running():
            self.notify("Stop checks before importing links.", severity="warning")
            return
        self.push_screen(
            ImportLinksModal(self.start_directory),
            self._apply_import_links,
        )

    def _apply_import_links(self, result: dict | None) -> None:
        if not result:
            return
        content = result.get("content", "")
        lines = [line.strip() for line in content.splitlines()]
        if not lines:
            return

        supported = {"vless", "vmess", "socks", "mtproto", "dns"}
        existing_keys = {
            self._link_duplicate_key(item.parsed, item.url) for item in self.items
        }
        added = 0
        duplicates = 0
        unsupported = 0

        for link in lines:
            if not link:
                continue
            parsed = parse_link(link)
            protocol = (parsed.get("protocol") or "").lower()
            if protocol not in supported:
                unsupported += 1
                continue
            key = self._link_duplicate_key(parsed, link)
            if key in existing_keys:
                duplicates += 1
                continue

            index = len(self.items)
            item = VlessItem(index=index, url=link, parsed=parsed)
            self.items.append(item)
            existing_keys.add(key)
            added += 1

        self._refresh_table()
        self._update_stats()
        self._update_status_bars()

        if added:
            self.notify(
                f"Imported {added} links. Duplicates: {duplicates}, unsupported: {unsupported}.",
                severity="information",
            )
            self._append_log(
                f"Imported {added} links (duplicates={duplicates}, unsupported={unsupported})",
                style="green",
            )
        else:
            self.notify(
                f"No new supported links imported. Duplicates: {duplicates}, unsupported: {unsupported}.",
                severity="warning",
            )

    @staticmethod
    def _link_duplicate_key(parsed: dict, raw_url: str) -> tuple[str, str, str] | tuple[str, str]:
        server = str(parsed.get("server") or "").strip().lower()
        link_type = str(parsed.get("type") or "").strip().lower()
        port_value = parsed.get("port")

        if server and port_value is not None:
            try:
                port = str(int(port_value))
            except Exception:
                port = str(port_value).strip().lower()
            return (server, link_type, port)

        # Fallback: keep previous behavior for malformed/unknown links.
        return ("url", raw_url.strip())

    def _apply_settings_result(self, result: dict | None) -> None:
        if not result:
            return
        try:
            concurrency = int(result["concurrency"])
            base_port = int(result["base_port"])
            timeout_seconds = float(result["timeout"])
            dns_retries = int(result["dns_retries"])
            dns_retry_interval_ms = int(result["dns_retry_interval_ms"])
        except Exception:
            self.notify(
                "Concurrency, base port, timeout, DNS retries and DNS retry interval must be numbers.",
                severity="error",
            )
            return

        test_url = result["test_url"].strip()
        parsed = urllib.parse.urlparse(test_url)
        if not parsed.scheme or not parsed.netloc:
            self.notify("Test URL is invalid.", severity="error")
            return
        if concurrency <= 0:
            self.notify("Concurrency must be greater than 0.", severity="error")
            return
        if not (1 <= base_port <= 65535):
            self.notify("Base port must be between 1 and 65535.", severity="error")
            return
        if base_port + concurrency - 1 > 65535:
            self.notify("Base port + concurrency exceeds 65535.", severity="error")
            return
        if timeout_seconds <= 0:
            self.notify("Timeout must be greater than 0.", severity="error")
            return
        if dns_retries < 0:
            self.notify("DNS retries cannot be negative.", severity="error")
            return
        if dns_retries > 20:
            self.notify("DNS retries must be 20 or less.", severity="error")
            return
        if dns_retry_interval_ms < 0:
            self.notify("DNS retry interval cannot be negative.", severity="error")
            return
        if dns_retry_interval_ms > 10000:
            self.notify("DNS retry interval must be 10000ms or less.", severity="error")
            return

        self.max_concurrent_checks = concurrency
        self.proxy_base_port = base_port
        self.test_url = test_url
        self.curl_timeout_seconds = timeout_seconds
        self.dns_retries = dns_retries
        self.dns_retry_interval_ms = dns_retry_interval_ms
        self._update_runtime_config()
        self.notify("Configuration updated.", severity="information")
        self._append_log(
            f"Config updated: concurrency={concurrency}, timeout={timeout_seconds:g}s, base_port={base_port}, dns_retries={dns_retries}, dns_retry_interval_ms={dns_retry_interval_ms}"
        )

    def action_show_selected_details(self) -> None:
        table = self.query_one(DataTable)
        if self.focused is not table:
            return
        row_index = table.cursor_row
        if row_index < 0 or row_index >= len(self.visible_item_indices):
            return
        self._show_details_by_index(self.visible_item_indices[row_index])

    def action_open_filter(self) -> None:
        available_types = sorted({self._item_type(item) for item in self.items})
        self.push_screen(
            FilterModal(available_types, self.active_filters),
            self._apply_filter_result,
        )

    def _apply_filter_result(self, result: dict | None) -> None:
        if result is None:
            return
        if result.get("reset"):
            self.active_filters = {"type": "", "name": "", "server": ""}
            self._refresh_table()
            self.notify("Filters reset.", severity="information")
            return

        candidate_filters = self._normalize_filters(result)

        if result.get("drop"):
            if self._is_worker_running():
                self.notify("Stop checks before dropping records.", severity="warning")
                return
            if not any(candidate_filters.values()):
                self.notify(
                    "Set at least one filter before dropping records.",
                    severity="warning",
                )
                return

            kept_items: list[VlessItem] = []
            dropped = 0
            for item in self.items:
                if self._item_matches_filters(item, candidate_filters):
                    dropped += 1
                else:
                    kept_items.append(item)

            if dropped == 0:
                self.notify("No records matched the drop filters.", severity="warning")
                return

            for new_index, item in enumerate(kept_items):
                item.index = new_index
            self.items = kept_items
            self.active_filters = {"type": "", "name": "", "server": ""}
            self.active_row_index = None
            self._recalculate_counters_from_items()
            self._refresh_table()
            self._update_stats()
            self._update_status_bars()
            self._update_latency_trend()
            self.notify(f"Dropped {dropped} records.", severity="information")
            self._append_log(f"Dropped {dropped} records via filter.", style="yellow")
            return

        self.active_filters = candidate_filters
        self._refresh_table()
        self.notify(
            f"Filter applied: {len(self.visible_item_indices)}/{len(self.items)} visible.",
            severity="information",
        )

    def _show_details_by_index(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.items):
            return
        item = self.items[row_index]
        payload = {
            "status": item.status,
            "latency_ms": item.latency_ms,
            "exit_ip": item.exit_ip,
            "reason": item.reason,
            "partial": item.partial,
            "xray_error": item.xray_error,
            "url": item.url,
            "parsed": item.parsed,
        }
        self.push_screen(RowDetailsModal(payload))

    def action_toggle_scan(self) -> None:
        if self._is_worker_running():
            self.action_stop_checks()
        else:
            self.action_run_checks()

    def action_run_checks(self) -> None:
        if (
            self._worker
            and not self._worker.is_cancelled
            and not self._worker.is_finished
        ):
            return
        self.auto_follow_rows = True
        self.active_row_index = None
        self._paused = False
        if (
            self.checked_count
            or self.healthy_count
            or self.partial_count
            or self.failed_count
        ):
            self._reset_results()
        has_xray_links = any(
            (item.parsed.get("protocol") or "").lower() in {"vless", "vmess"}
            for item in self.items
        )
        if has_xray_links and not self.xray_available:
            self.notify(
                "xray is not available: VLESS/VMESS checks may fail.",
                severity="warning",
            )
            self._append_log(
                "xray not available; VLESS/VMESS checks may fail",
                style="yellow",
            )
        workers = min(self.max_concurrent_checks, len(self.items))
        self._set_status(
            f"Checking {len(self.items)} links ({workers} concurrent)..."
        )
        self._append_log(
            f"Started check run: total={len(self.items)}, workers={workers}",
            style="cyan",
        )
        scan_btn = self.query_one("#scan", Button)
        scan_btn.label = "Stop checks"
        scan_btn.variant = "warning"
        scan_btn.disabled = False
        pause_btn = self.query_one("#pause", Button)
        pause_btn.disabled = False
        pause_btn.label = "Pause"
        pause_btn.variant = "primary"
        self._set_action_loader("running")
        self._worker = self.run_checks()

    def action_toggle_pause(self) -> None:
        if not self._is_worker_running():
            return
        self._paused = not self._paused
        pause_btn = self.query_one("#pause", Button)
        if self._paused:
            pause_btn.label = "Resume"
            pause_btn.variant = "success"
            self._set_status("Paused.")
            self._set_action_loader("paused")
            self._append_log("Paused", style="yellow")
            self.notify("Checks paused.", severity="warning")
        else:
            pause_btn.label = "Pause"
            pause_btn.variant = "primary"
            workers = min(self.max_concurrent_checks, len(self.items))
            self._set_status(
                f"Checking {len(self.items)} links ({workers} concurrent)..."
            )
            self._set_action_loader("running")
            self._append_log("Resumed", style="green")
            self.notify("Checks resumed.", severity="information")

    def action_stop_checks(self) -> None:
        if (
            self._worker
            and not self._worker.is_cancelled
            and not self._worker.is_finished
        ):
            self._worker.cancel()
            self._set_status("Stop requested. Waiting for current check to finish...")
            self._paused = False
            pause_btn = self.query_one("#pause", Button)
            pause_btn.label = "Pause"
            pause_btn.variant = "primary"
            pause_btn.disabled = True
            scan_btn = self.query_one("#scan", Button)
            scan_btn.label = "Start checks"
            scan_btn.variant = "primary"
            scan_btn.disabled = True
            self._set_action_loader("stopping")
            self._append_log("Stop requested", style="yellow")

    def action_toggle_follow(self) -> None:
        self.auto_follow_rows = not self.auto_follow_rows
        if self.auto_follow_rows:
            self.notify("Auto-follow enabled.", severity="information")
            self._append_log("Auto-follow enabled")
            if self.active_row_index is not None:
                self._follow_row(self.active_row_index)
        else:
            self.notify("Auto-follow paused.", severity="warning")
            self._append_log("Auto-follow paused")

    def action_export_healthy(self) -> None:
        default_filename = Path(self.export_file).name
        available_types = sorted({self._item_type(item) for item in self.items})
        self.push_screen(
            ExportModal(self.start_directory, default_filename, available_types),
            self._apply_export_result,
        )

    def _apply_export_result(self, result: dict | None) -> None:
        if not result:
            return
        output_path = Path(result["directory"]) / result["filename"]
        include_partial = bool(result.get("include_partial", False))
        selected_types = {
            str(link_type).strip().lower() for link_type in result.get("selected_types", [])
        }
        partial_exportable = [
            item
            for item in self.items
            if item.status == "PARTIAL"
            and item.latency_ms is not None
            and item.latency_ms < 1000
            and (not selected_types or self._item_type(item) in selected_types)
        ]
        if include_partial:
            healthy_urls = [
                item.url
                for item in self.items
                if item.status == "OK"
                and (not selected_types or self._item_type(item) in selected_types)
            ] + [
                item.url for item in partial_exportable
            ]
        else:
            healthy_urls = [
                item.url
                for item in self.items
                if item.status == "OK"
                and (not selected_types or self._item_type(item) in selected_types)
            ]
        separator = "\n------------------------------\n"
        payload = separator.join(healthy_urls)
        if payload:
            payload += "\n"
        try:
            output_path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            self.notify(f"Export failed: {exc}", severity="error")
            self._append_log(f"Export failed: {exc}", style="red")
            return
        self.export_file = str(output_path)
        if include_partial:
            label = f"links (OK + {len(partial_exportable)} PARTIAL with latency < 1000ms)"
        else:
            label = "healthy links"
        self.notify(
            f"Exported {len(healthy_urls)} {label} to {output_path}",
            severity="information",
        )
        partial_note = (
            f" (including {len(partial_exportable)} PARTIAL with latency < 1000ms)"
            if include_partial
            else ""
        )
        self._append_log(
            f"Exported {len(healthy_urls)} links{partial_note} to {output_path}"
        )

    def action_reset_results(self) -> None:
        if (
            self._worker
            and not self._worker.is_cancelled
            and not self._worker.is_finished
        ):
            return
        self._reset_results()
        self.notify("Results cleared.", severity="information")
        self._append_log("Results cleared")

    def action_reset_scan(self) -> None:
        if self._is_worker_running():
            self.notify("Stop checks before resetting scan.", severity="warning")
            return

        table = self.query_one(DataTable)
        table.clear(columns=False)

        self.items = []
        self.visible_item_indices = []
        self.active_filters = {"type": "", "name": "", "server": ""}
        self.checked_count = 0
        self.healthy_count = 0
        self.partial_count = 0
        self.failed_count = 0
        self.active_row_index = None
        self.latency_history = []
        self.latency_valid_history = []
        self._paused = False
        self.auto_follow_rows = True

        scan_btn = self.query_one("#scan", Button)
        scan_btn.label = "Start checks"
        scan_btn.variant = "primary"
        scan_btn.disabled = False
        pause_btn = self.query_one("#pause", Button)
        pause_btn.disabled = True
        pause_btn.label = "Pause"
        pause_btn.variant = "primary"

        self._set_action_loader("idle")
        self._set_status("Idle.")
        self._update_runtime_config()
        self._update_stats()
        self._update_status_bars()
        self._update_latency_trend()
        table.focus()

        self.notify("Scan reset. Table cleared.", severity="information")
        self._append_log("Scan reset: table and previous results cleared.")

    @staticmethod
    def _make_failure_details(reason: str) -> dict:
        return {
            "reason": reason,
            "xray_error": "",
            "status_code": "",
            "exit_ip": "",
            "partial": False,
        }

    @staticmethod
    def _check_item_with_port(
        url: str,
        test_url: str,
        timeout_seconds: float,
        dns_retries: int,
        dns_retry_interval_ms: int,
        port_pool: Queue,
    ) -> tuple[bool, Optional[int], dict]:
        local_port = port_pool.get()
        try:
            return test_link(
                url,
                local_port=local_port,
                test_url=test_url,
                timeout_seconds=timeout_seconds,
                dns_retries=dns_retries,
                dns_retry_interval_ms=dns_retry_interval_ms,
            )
        finally:
            port_pool.put(local_port)

    @work(thread=True, exclusive=True)
    def run_checks(self) -> None:
        worker_count = min(self.max_concurrent_checks, len(self.items))
        if worker_count <= 0:
            self.call_from_thread(self._finish_checks, False)
            return

        port_pool: Queue = Queue()
        for offset in range(worker_count):
            port_pool.put(self.proxy_base_port + offset)

        stop_requested = False
        next_index = 0
        futures: dict[Future, int] = {}

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            while futures or next_index < len(self.items):
                if self._worker and self._worker.is_cancelled and not stop_requested:
                    stop_requested = True
                    for future in list(futures):
                        future.cancel()

                while (
                    not stop_requested
                    and not self._paused
                    and next_index < len(self.items)
                    and len(futures) < worker_count
                ):
                    item = self.items[next_index]
                    next_index += 1
                    self.call_from_thread(self._set_row_checking, item.index)
                    future = executor.submit(
                        self._check_item_with_port,
                        item.url,
                        self.test_url,
                        self.curl_timeout_seconds,
                        self.dns_retries,
                        self.dns_retry_interval_ms,
                        port_pool,
                    )
                    futures[future] = item.index

                if not futures:
                    if stop_requested:
                        break
                    time.sleep(0.05)
                    continue

                done, _ = wait(
                    list(futures.keys()),
                    timeout=0.2,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue

                for future in done:
                    item_index = futures.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        ok, latency, details = future.result()
                    except Exception as exc:
                        ok = False
                        latency = None
                        details = self._make_failure_details(repr(exc))

                    self.call_from_thread(
                        self._apply_result, item_index, ok, latency, details
                    )

        self.call_from_thread(self._finish_checks, stop_requested)

    def _set_row_checking(self, index: int) -> None:
        table = self.query_one(DataTable)
        try:
            table.update_cell(str(index), "status", self._status_text("CHECKING"))
            table.update_cell(str(index), "reason", Text("working...", style="dim"))
        except Exception:
            pass
        self.active_row_index = index
        item = self.items[index]
        p = item.parsed
        protocol = (p.get("protocol") or "").upper() or "UNKNOWN"
        server = p.get("server") or "-"
        port = p.get("port") or "-"
        self._append_log(
            f"[{index + 1}/{len(self.items)}] CHECKING {protocol} {server}:{port}",
            style="cyan",
        )
        self._follow_row(index)

    def _apply_result(
        self, index: int, ok: bool, latency: Optional[int], details: dict
    ) -> None:
        item = self.items[index]
        reason = details.get("reason", "")
        exit_ip = details.get("exit_ip", "")
        is_partial = details.get("partial", False)

        if ok:
            item.status = "OK"
            self.healthy_count += 1
        elif is_partial:
            item.status = "PARTIAL"
            self.partial_count += 1
        else:
            item.status = "FAILED"
            self.failed_count += 1
            latency = None

        item.latency_ms = latency
        item.exit_ip = exit_ip
        item.reason = reason
        item.partial = is_partial
        item.xray_error = details.get("xray_error", "")

        self.checked_count += 1

        table = self.query_one(DataTable)
        try:
            table.update_cell(str(index), "status", self._status_text(item.status))
            table.update_cell(str(index), "latency", self._latency_text(latency))
            table.update_cell(str(index), "exit_ip", exit_ip or "-")
            table.update_cell(str(index), "reason", self._reason_text(reason))
        except Exception:
            pass

        if latency is not None:
            plotted = float(latency)
            self.latency_valid_history.append(plotted)
        else:
            # Keep one point per checked link in the trend, even when latency is unavailable.
            plotted = max(float(self.curl_timeout_seconds * 1000), 1500.0)
        self.latency_history.append(plotted)
        if len(self.latency_history) > 500:
            self.latency_history = self.latency_history[-500:]
        if len(self.latency_valid_history) > 500:
            self.latency_valid_history = self.latency_valid_history[-500:]
        self._update_latency_trend()
        self._update_stats()
        self._update_status_bars()
        short_reason = textwrap.shorten(reason, width=90, placeholder="...") if reason else "-"
        latency_label = f"{latency}ms" if latency is not None else "--"
        self._append_log(
            f"[{index + 1}/{len(self.items)}] {item.status} latency={latency_label} reason={short_reason}"
        )

    def _finish_checks(self, cancelled: bool = False) -> None:
        scan_btn = self.query_one("#scan", Button)
        scan_btn.label = "Start checks"
        scan_btn.variant = "primary"
        scan_btn.disabled = False
        pause_btn = self.query_one("#pause", Button)
        pause_btn.disabled = True
        pause_btn.label = "Pause"
        pause_btn.variant = "primary"
        self._paused = False
        self._set_action_loader("idle")
        self._set_status("Idle.")
        if cancelled:
            self.notify("Checks stopped.", severity="warning")
            self._append_log("Checks stopped", style="yellow")
        else:
            self.notify("Checks completed.", severity="information")
            self._append_log("Checks completed", style="green")

    def _update_stats(self) -> None:
        total = len(self.items)
        status_text = self.status_message.replace("\n", " ").strip()
        if self.xray_available:
            version = self.xray_version or "Unknown"
            xray_state = f"[bold #32d17d]Available - Version {version}[/]"
        else:
            xray_state = "[bold #ff6b6b]Not Available[/]"
        stats = (
            f"[b]Loaded:[/b] {total}\n"
            f"[b]Checked:[/b] {self.checked_count}/{total}\n"
            f"[b]Xray:[/b] {xray_state}\n"
            "[b]HTTP Probe:[/b] [bold #32d17d]Built-in (Python)[/]\n"
            f"[b]Status:[/b] {status_text}"
        )
        stats_widget = self.query_one("#stats", Static)
        stats_widget.update(stats)
        self._update_status_bars()

    def _update_runtime_config(self) -> None:
        end_port = self.proxy_base_port + self.max_concurrent_checks - 1
        if self.max_concurrent_checks <= 10:
            ports_text = ", ".join(
                str(self.proxy_base_port + i) for i in range(self.max_concurrent_checks)
            )
        else:
            ports_text = f"{self.proxy_base_port}-{end_port}"

        config = (
            f"[b]Concurrency:[/b] {self.max_concurrent_checks}\n"
            f"[b]Test URL:[/b] {self.test_url}\n"
            f"[b]Timeout:[/b] {self.curl_timeout_seconds:g}s [b]| DNS:[/b] {self.dns_retries} x {self.dns_retry_interval_ms}ms\n"
            f"[b]Base Port:[/b] {self.proxy_base_port}\n"
            f"[b]Busy Ports:[/b] [bold #f5a524]{ports_text}[/]"
        )
        self.query_one("#runtime_config", Static).update(config)

    def _set_status(self, message: str) -> None:
        self.status_message = message
        self._update_stats()

    def _update_latency_trend(self) -> None:
        spark = self.query_one("#latency_spark", Static)
        stats = self.query_one("#latency_stats", Static)
        if not self.latency_history:
            spark.update(Text("▁", style="dim"))
            stats.update("L: --  A: --  P: --")
            return

        max_points = max((spark.size.width or 120) - 2, 20)
        series = self.latency_history[-max_points:]
        min_v = min(series)
        max_v = max(series)
        span = max(max_v - min_v, 1.0)
        glyphs = "▁▂▃▄▅▆▇█"
        bars = Text()
        for value in series:
            level = int(((value - min_v) / span) * (len(glyphs) - 1))
            level = max(0, min(level, len(glyphs) - 1))
            if value < 400:
                color = "#32d17d"
            elif value < 1000:
                color = "#f5a524"
            else:
                color = "#ff6b6b"
            bars.append(glyphs[level], style=f"bold {color}")
        spark.update(bars)

        if not self.latency_valid_history:
            stats.update("L: --  A: --  P: --")
            return
        latest = self.latency_valid_history[-1]
        avg = sum(self.latency_valid_history) / len(self.latency_valid_history)
        peak = max(self.latency_valid_history)
        latest_style = "bold #32d17d" if latest < 400 else ("bold #f5a524" if latest < 1000 else "bold #ff6b6b")
        peak_style = "bold #32d17d" if peak < 400 else ("bold #f5a524" if peak < 1000 else "bold #ff6b6b")
        stat_text = Text()
        stat_text.append("L:", style="bold #66b6ff")
        stat_text.append(f"{latest:.0f}ms", style=latest_style)
        stat_text.append("  A:", style="bold #66b6ff")
        stat_text.append(f"{avg:.0f}ms", style="bold #c9d7e6")
        stat_text.append("  P:", style="bold #66b6ff")
        stat_text.append(f"{peak:.0f}ms", style=peak_style)
        stats.update(stat_text)

    def _update_status_bars(self) -> None:
        total_items = len(self.items)
        total_for_ratio = total_items if total_items > 0 else 1
        ok = self.healthy_count
        partial = self.partial_count
        failed = self.failed_count
        pending = max(total_items - (ok + partial + failed), 0)

        meter = self.query_one("#status_meters", Static)
        # Keep bars compact so they stay visible in narrower terminals.
        bar_width = max(min((meter.size.width or 56) - 18, 38), 20)

        text = Text.assemble(
            self._make_meter_line("OK", ok, total_for_ratio, "#32d17d", bar_width),
            "\n",
            self._make_meter_line("PARTIAL", partial, total_for_ratio, "#f5a524", bar_width),
            "\n",
            self._make_meter_line("FAILED", failed, total_for_ratio, "#ff6b6b", bar_width),
            "\n",
            self._make_meter_line("PENDING", pending, total_for_ratio, "#677489", bar_width),
        )
        meter.update(text)

    def _reset_results(self) -> None:
        self.checked_count = 0
        self.healthy_count = 0
        self.partial_count = 0
        self.failed_count = 0
        self.active_row_index = None
        self.latency_history = []
        self.latency_valid_history = []

        for item in self.items:
            item.status = "PENDING"
            item.latency_ms = None
            item.exit_ip = ""
            item.reason = ""
            item.partial = False
            item.xray_error = ""

        table = self.query_one(DataTable)
        for item in self.items:
            try:
                table.update_cell(str(item.index), "status", self._status_text("PENDING"))
                table.update_cell(str(item.index), "latency", self._latency_text(None))
                table.update_cell(str(item.index), "exit_ip", "-")
                table.update_cell(str(item.index), "reason", "-")
            except Exception:
                pass

        self._update_stats()
        self._update_status_bars()
        self._update_latency_trend()
        self._set_action_loader("idle")
        self._set_status("Idle.")

    @staticmethod
    def _make_meter_line(
        label: str, count: int, total: int, color: str, bar_width: int
    ) -> Text:
        percent = int(round((count / total) * 100)) if total else 0
        filled = int(round((count / total) * bar_width)) if total else 0
        filled = max(0, min(bar_width, filled))

        line = Text()
        line.append(f"{label:<7}", style=f"bold {color}")
        line.append(" [", style="dim")
        line.append("█" * filled, style=f"bold {color}")
        line.append("░" * (bar_width - filled), style="dim")
        line.append("] ", style="dim")
        line.append(f"{count:>3}", style="bold")
        line.append(f" {percent:>3}%", style="dim")
        return line

    @staticmethod
    def _status_text(status: str) -> Text:
        style = {
            "PENDING": "dim",
            "CHECKING": "bold cyan",
            "OK": "bold green",
            "PARTIAL": "bold yellow",
            "FAILED": "bold red",
        }.get(status, "dim")
        return Text(status, style=style)

    @staticmethod
    def _latency_text(latency: Optional[int]) -> Text:
        if latency is None:
            return Text("--", style="dim")
        if latency < 400:
            style = "green"
        elif latency < 1000:
            style = "yellow"
        else:
            style = "red"
        return Text(f"{latency} ms", style=style)

    @staticmethod
    def _reason_text(reason: str) -> Text:
        if not reason:
            return Text("-", style="dim")
        short = textwrap.shorten(reason, width=72, placeholder="...")
        return Text(short, style="dim")

    def _append_log(self, message: str, style: str = "") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = Text()
        line.append(f"[{timestamp}] ", style="dim")
        if style:
            line.append(message, style=style)
        else:
            line.append(message)

        self._log_entries.append(line)
        if len(self._log_entries) > 4000:
            self._log_entries = self._log_entries[-3000:]

        if self._logs_modal is not None:
            try:
                self._logs_modal.append_line(line)
            except Exception:
                self._logs_modal = None

    def _follow_row(self, index: int) -> None:
        if not self.auto_follow_rows:
            return
        if index not in self.visible_item_indices:
            return
        table = self.query_one(DataTable)
        try:
            visible_row = self.visible_item_indices.index(index)
            table.move_cursor(row=visible_row, animate=False, scroll=True)
        except Exception:
            return

    def _is_worker_running(self) -> bool:
        return bool(
            self._worker
            and not self._worker.is_cancelled
            and not self._worker.is_finished
        )

    def _pause_auto_follow(self) -> None:
        if not self.auto_follow_rows:
            return
        self.auto_follow_rows = False
        self.notify("Auto-follow paused. Press A to resume.", severity="warning")
        self._append_log("Auto-follow paused. Press A to resume.", style="yellow")

    def _pause_auto_follow_from_widget(self, widget: object) -> None:
        if not self._is_worker_running() or not self.auto_follow_rows:
            return
        table = self.query_one(DataTable)
        current = widget
        while current is not None:
            if current is table:
                self._pause_auto_follow()
                return
            current = getattr(current, "parent", None)

    def _set_action_loader(self, mode: str) -> None:
        if mode == self._loader_mode and mode != "idle":
            return
        center = self.query_one("#actions_center", Horizontal)
        for child in list(center.children):
            child.remove()
        if mode == "idle":
            self._loader_mode = "idle"
            return
        center.mount(LoadingIndicator(classes=f"action-loader loader-{mode}"))
        self._loader_mode = mode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OPPY - Mission Control for Proxy Links (VLESS / VMESS / SOCKS / MTProto / DNS)"
    )
    parser.add_argument(
        "--input-file",
        required=False,
        help="Path to proxy list file (optional)",
    )
    parser.add_argument(
        "--export-file",
        default=None,
        help="Output file for healthy proxy links (default: healthy_links.txt next to input file)",
    )
    parser.add_argument(
        "--no-tui", action="store_true", help="Run in CLI mode (no interface)"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    links: list[str] = []
    if args.input_file:
        try:
            links = load_links(args.input_file)
        except FileNotFoundError as exc:
            print(str(exc))
            return

    items = [VlessItem(index=i, url=link, parsed=parse_link(link)) for i, link in enumerate(links)]

    if args.no_tui:
        if not args.input_file:
            print("--input-file is required when using --no-tui mode")
            return
        if not links:
            print(f"No links found in {args.input_file}")
            return
        run_cli(links)
        return

    app = VlessTesterApp(items, args.input_file, args.export_file)
    app.run()


if __name__ == "__main__":
    main()
