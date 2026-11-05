#!/usr/bin/env python3
"""Browser console for SDRTrunk event and decoded-message logs."""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import os
import re
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent


def load_config() -> dict[str, str]:
    """Read simple key=value settings from config.txt beside this program."""
    settings: dict[str, str] = {}
    config_path = ROOT / "config.txt"
    if not config_path.exists():
        return settings
    for raw_line in config_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip().lower()] = value.strip().strip('"').strip("'")
    return settings


def configured_log_dir() -> Path:
    configured = os.environ.get("SDRTRUNK_LOG_DIR") or load_config().get("log_dir", "")
    if not configured:
        configured = str(Path.home() / "SDRTrunk" / "event_logs")
    # Expand both Unix-style $NAME and Windows-style %NAME% variables on either OS.
    configured = os.path.expandvars(configured)
    configured = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), configured)
    return Path(configured).expanduser()


LOG_DIR = configured_log_dir()
HOST = os.environ.get("CONSOLE_HOST", "0.0.0.0")
PORT = int(os.environ.get("CONSOLE_PORT", "5100"))
GRID_HOST = os.environ.get("GRID_HOST", HOST)
GRID_PORT = 5101
GRID_DEFAULT_TALKGROUPS = ("58214", "58341", "58337", "58211", "58014", "58015")
POLL_STALE_SECONDS = 25
MAX_EVENTS = 150
MAX_SITES = 100
MAX_TALKGROUPS = 100


def load_talkgroups() -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    path = ROOT / "talkgroups.txt"
    if not path.exists():
        return mapping
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = line.split("\t")
        if len(cells) < 4 or cells[0].strip().upper() == "DEC":
            continue
        dec = cells[0].strip()
        if not dec.isdigit():
            continue
        mapping[dec] = {
            "hex": cells[1].strip() if len(cells) > 1 else "",
            "alpha": cells[2].strip() if len(cells) > 2 else "",
            "description": cells[3].strip() if len(cells) > 3 else "",
            "tag": cells[4].strip() if len(cells) > 4 else "",
            "mode": cells[5].strip() if len(cells) > 5 else "",
        }
    return mapping


class LogMonitor:
    def __init__(self) -> None:
        self.aliases = load_talkgroups()
        self.lock = threading.Lock()
        self.positions: dict[str, int] = {}
        self.system_id = "—"
        self.nac = "—"
        self.control_frequency = "—"
        self.sites: dict[str, dict] = {}
        self.talkgroups: dict[str, dict] = {}
        self.events: list[dict] = []
        self.file_count = 0
        self.last_data_epoch: float | None = None
        self.last_scan_epoch: float | None = None
        self.error = ""

    def scan(self) -> None:
        with self.lock:
            try:
                if not LOG_DIR.exists():
                    self.error = f"Log folder not found: {LOG_DIR}"
                    self.file_count = 0
                    self.last_scan_epoch = time.time()
                    return
                files = sorted(
                    (p for p in LOG_DIR.glob("*") if p.is_file() and (p.name.endswith("_decoded_messages.log") or p.name.endswith("_call_events.log"))),
                    key=lambda p: p.stat().st_mtime,
                )
                self.file_count = len(files)
                # Read a recent tail on first launch, then follow each file from its saved byte offset.
                for path in files:
                    key = str(path)
                    size = path.stat().st_size
                    if key not in self.positions:
                        start = max(0, size - 262144)
                    else:
                        start = self.positions[key]
                        if size < start:  # SDRTrunk rotated or truncated this file.
                            start = 0
                    if start >= size:
                        self.positions[key] = size
                        continue
                    with path.open("rb") as stream:
                        stream.seek(start)
                        if start > 0:
                            stream.readline()  # discard a partial first line
                        while raw := stream.readline():
                            line = raw.decode("utf-8", errors="replace").strip()
                            if line:
                                if path.name.endswith("_decoded_messages.log"):
                                    self._decoded_line(path, line)
                                else:
                                    self._call_line(path, line)
                        self.positions[key] = stream.tell()
                self.error = ""
                self.last_scan_epoch = time.time()
            except (OSError, PermissionError) as exc:
                self.error = f"Could not read SDRTrunk logs: {exc}"
                self.last_scan_epoch = time.time()

    def _timestamp(self, line: str) -> str:
        match = re.match(r"(\d{8})\s+(\d{6})(?:\.\d+)?", line)
        if match:
            return f"{match.group(1)[4:6]}/{match.group(1)[6:8]} {match.group(2)[:2]}:{match.group(2)[2:4]}:{match.group(2)[4:6]}"
        return datetime.now().strftime("%m/%d %H:%M:%S")

    def _touch(self) -> None:
        self.last_data_epoch = time.time()

    def _record_event(self, when: str, kind: str, message: str, talkgroup: str = "", source: str = "") -> None:
        alias = self.aliases.get(talkgroup, {})
        self.events.insert(0, {
            "time": when,
            "kind": kind,
            "message": message,
            "talkgroup": talkgroup,
            "alpha": alias.get("alpha", ""),
            "description": alias.get("description", ""),
            "source": source,
        })
        del self.events[MAX_EVENTS:]

    def _touch_talkgroup(self, tg: str, when: str, message: str, source: str = "") -> None:
        alias = self.aliases.get(tg, {})
        row = self.talkgroups.setdefault(tg, {
            "id": tg,
            "alpha": alias.get("alpha", ""),
            "description": alias.get("description", ""),
            "tag": alias.get("tag", ""),
            "mode": alias.get("mode", ""),
            "last_seen": when,
            "last_epoch": time.time(),
            "count": 0,
            "source": "",
            "message": "",
        })
        row.update(last_seen=when, last_epoch=time.time(), count=row["count"] + 1, source=source or row["source"], message=message)
        self.talkgroups = dict(sorted(self.talkgroups.items(), key=lambda item: item[1]["last_epoch"], reverse=True)[:MAX_TALKGROUPS])

    def _decoded_line(self, path: Path, line: str) -> None:
        # SDRTrunk decoded-message format: timestamp, decode result, decoded payload.
        cells = next(csv.reader([line]))
        if len(cells) < 3 or cells[0].strip().upper() == "DECODED MESSAGE LOGGER":
            return
        when, result, message = cells[0].strip(), cells[1].strip(), ",".join(cells[2:]).strip()
        if not re.match(r"\d{8}\s+\d{6}", when):
            return
        self._touch()
        display_time = self._timestamp(when)

        freq_match = re.search(r"_(\d{7,10})_Hz_", path.name)
        if freq_match:
            hz = int(freq_match.group(1))
            self.control_frequency = f"{hz / 1_000_000:.4f} MHz"

        nac_match = re.search(r"\bNAC:(\d+)(?:/x([\da-fA-F]+))?", message)
        if nac_match:
            self.nac = f"{nac_match.group(1)}" + (f" / 0x{nac_match.group(2).upper()}" if nac_match.group(2) else "")
        sys_match = re.search(r"\bSYSTEM:(\d+)(?:/x([\da-fA-F]+))?", message)
        if sys_match:
            self.system_id = f"{sys_match.group(1)}" + (f" / 0x{sys_match.group(2).upper()}" if sys_match.group(2) else "")

        site_match = re.search(r"ADJACENT SITE STATUS RFSS:(\d+)(?:/x[\da-fA-F]+)? SITE:(\d+)(?:/x[\da-fA-F]+)? SYSTEM:(\d+)(?:/x[\da-fA-F]+)? LRA:(\d+)(?:/x[\da-fA-F]+)? CHAN:([\d-]+)(?: SERVICE OPTIONS:(.*))?", message)
        if site_match:
            rfss, site, system, lra, channel, services = (site_match.group(i) or "" for i in range(1, 7))
            key = f"{system}:{rfss}:{site}"
            row = self.sites.setdefault(key, {"system": system, "rfss": rfss, "site": site, "channel": channel, "services": "", "last_seen": display_time, "last_epoch": time.time(), "count": 0})
            row.update(channel=channel, services=services.strip("[] "), last_seen=display_time, last_epoch=time.time(), count=row["count"] + 1, lra=lra)
            self.sites = dict(sorted(self.sites.items(), key=lambda item: item[1]["last_epoch"], reverse=True)[:MAX_SITES])
            self._record_event(display_time, "SITE", f"Observed neighbor site RFSS {rfss}, Site {site}, channel {channel}")
            return

        user_match = re.search(r"GROUP VOICE CHANNEL USER.*?\bFM:(\d+)\s+TO:(\d+)", message)
        if user_match:
            source, tg = user_match.groups()
            self._touch_talkgroup(tg, display_time, message, source)
            self._record_event(display_time, "VOICE", message, tg, source)
            return

        update_match = re.search(r"GROUP VOICE CHANNEL UPDATE.*?TALKGROUP A:(\d+).*?(?:CHAN A:([\d-]+))?", message)
        if update_match:
            tg, channel = update_match.groups()
            self._touch_talkgroup(tg, display_time, message)
            self._record_event(display_time, "ASSIGN", f"Talkgroup {tg} assigned channel {channel or 'unknown'}", tg)
            return

        if "CALL TERMINATION" in message:
            self._record_event(display_time, "END", message)
        elif "SYNC LOSS" in message:
            self._record_event(display_time, "DECODE", message)

    def _call_line(self, path: Path, line: str) -> None:
        try:
            cells = next(csv.reader([line]))
        except csv.Error:
            return
        if not cells or cells[0].strip().upper() == "TIMESTAMP":
            return
        if len(cells) < 10:
            return
        timestamp, duration, protocol, event, from_id, to_id, channel, frequency, timeslot, details = (c.strip() for c in cells[:10])
        if not timestamp:
            return
        self._touch()
        when = self._timestamp(timestamp) if re.match(r"\d{8}\s+\d{6}", timestamp) else timestamp
        candidates = re.findall(r"(?<!\d)(\d{4,6})(?!\d)", f"{from_id} {to_id}")
        tg = next((candidate for candidate in candidates if candidate in self.aliases), candidates[0] if candidates else "")
        summary = f"{event or 'Call event'} · {protocol or 'protocol'} · {frequency or 'frequency'} · {details}".strip(" ·")
        self._record_event(when, "CALL", summary, tg, from_id)
        if tg:
            self._touch_talkgroup(tg, when, summary, from_id)

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            age = (now - self.last_data_epoch) if self.last_data_epoch else None
            sites = sorted(self.sites.values(), key=lambda row: row["last_epoch"], reverse=True)
            talkgroups = sorted(self.talkgroups.values(), key=lambda row: row["last_epoch"], reverse=True)
            activity_by_id = {row["id"]: row for row in talkgroups}
            grid_ids = list(GRID_DEFAULT_TALKGROUPS)
            grid_ids.extend(tg_id for tg_id in activity_by_id if tg_id not in grid_ids)
            grid_talkgroups = []
            for tg_id in grid_ids:
                alias = self.aliases.get(tg_id, {})
                activity = activity_by_id.get(tg_id, {})
                last_epoch = activity.get("last_epoch", 0)
                grid_talkgroups.append({
                    "id": tg_id,
                    "alpha": activity.get("alpha") or alias.get("alpha", ""),
                    "description": activity.get("description") or alias.get("description", ""),
                    "tag": activity.get("tag") or alias.get("tag", ""),
                    "known": bool(alias.get("alpha") or alias.get("description")),
                    "last_seen": activity.get("last_seen", "—"),
                    "last_epoch": last_epoch,
                    "count": activity.get("count", 0),
                    "source": activity.get("source", ""),
                    "message": activity.get("message", ""),
                    "recent": bool(last_epoch and now - last_epoch <= 12),
                })
            for row in talkgroups:
                row["recent"] = now - row["last_epoch"] <= 12
            return {
                "title": "KF8ENC TRUNK CONSOLE",
                "system_id": self.system_id,
                "nac": self.nac,
                "control_frequency": self.control_frequency,
                "sites": sites,
                "talkgroups": talkgroups,
                "grid_talkgroups": grid_talkgroups,
                "events": self.events[:MAX_EVENTS],
                "file_count": self.file_count,
                "alias_count": len(self.aliases),
                "log_dir": str(LOG_DIR),
                "last_data_age": age,
                "last_data_time": datetime.fromtimestamp(self.last_data_epoch).strftime("%H:%M:%S") if self.last_data_epoch else "Waiting for decoded messages",
                "online": age is not None and age <= POLL_STALE_SECONDS,
                "error": self.error,
                "checked_at": datetime.now().strftime("%H:%M:%S"),
            }


MONITOR = LogMonitor()


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "KF8ENCTrunkConsole/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep normal polling quiet; retain concise server-side errors only.
        if args and str(args[1]).startswith("5"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/":
            path = ROOT / "static" / "index.html"
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        elif route == "/grid":
            host = grid_host_for_request(self.headers.get("Host", ""))
            self.send_response(302)
            self.send_header("Location", f"http://{host}:{GRID_PORT}/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        elif route == "/static/app.css":
            self._send(200, (ROOT / "static" / "app.css").read_bytes(), "text/css; charset=utf-8")
        elif route == "/static/app.js":
            self._send(200, (ROOT / "static" / "app.js").read_bytes(), "application/javascript; charset=utf-8")
        elif route == "/api/status":
            MONITOR.scan()
            status = MONITOR.snapshot()
            status["grid_host"] = grid_host_for_request(self.headers.get("Host", ""))
            body = json.dumps(status, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif route == "/health":
            self._send(200, b"ok", "text/plain; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class GridHandler(ConsoleHandler):
    """Local-only browser view styled as a dispatch talkgroup grid."""

    server_version = "KF8ENCGridView/1.0"

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        static_files = {
            "/": (ROOT / "static" / "grid.html", "text/html; charset=utf-8"),
            "/grid.css": (ROOT / "static" / "grid.css", "text/css; charset=utf-8"),
            "/grid.js": (ROOT / "static" / "grid.js", "application/javascript; charset=utf-8"),
        }
        if route in static_files:
            path, mime = static_files[route]
            self._send(200, path.read_bytes(), mime)
            return
        super().do_GET()


def local_addresses() -> list[str]:
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    # UDP connect selects the address used for the default route without sending data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_probe:
            route_probe.connect(("192.0.2.1", 80))
            ip = route_probe.getsockname()[0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def grid_host_for_request(host_header: str) -> str:
    requested_host = urlparse(f"//{host_header}").hostname or ""
    try:
        requested_ip = ipaddress.ip_address(requested_host)
        if requested_ip.version == 4 and not requested_ip.is_loopback and not requested_ip.is_unspecified:
            return requested_host
    except ValueError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_probe:
            route_probe.connect(("192.0.2.1", 80))
            ip = route_probe.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    addresses = local_addresses()
    return addresses[0] if addresses else "127.0.0.1"


def main() -> None:
    print("KF8ENC Trunk Console")
    print(f"Watching: {LOG_DIR}")
    print(f"Open on this computer: http://127.0.0.1:{PORT}")
    for ip in local_addresses():
        print(f"Open on your local network: http://{ip}:{PORT}")
    grid_server = ThreadingHTTPServer((GRID_HOST, GRID_PORT), GridHandler)
    grid_thread = threading.Thread(target=grid_server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    grid_thread.start()
    print(f"Grid View on this computer: http://127.0.0.1:{GRID_PORT}")
    for ip in local_addresses():
        print(f"Grid View on your local network: http://{ip}:{GRID_PORT}")
    print("Press Ctrl+C to stop.")
    server = ThreadingHTTPServer((HOST, PORT), ConsoleHandler)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nConsole stopped.")
    finally:
        server.server_close()
        grid_server.shutdown()
        grid_server.server_close()


if __name__ == "__main__":
    main()
