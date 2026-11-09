"""Инкрементальный обход выгрузки логов сервера и наполнение индекса (`index.py`).

Понимает структуру выгрузки из docs/LOGS.md: `log_storage/<unix_ts>/` с
`*.ADM`, `server_console.log`, `LBmaster_*.log`; `_ZFServerLog/` с
`zfbubusik_log-*.log`; `Logs/` с `CarCodeLock_*.log`. `.rar`/`.zip` на пути
распаковываются через `bsdtar` во временную директорию и обходятся как
обычная папка.

Инкрементальность: файл, уже записанный в таблицу `files` с тем же
size+mtime, парсится заново только если что-то из них изменилось — докачка
по смещению внутри файла не делается (см. задачу).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import adm, carcodelock, console, lbmaster, zf
from .base import LogEvent
from .index import LogIndex

logger = logging.getLogger(__name__)

_SESSION_DIR_RE = re.compile(r"^\d{9,10}$")
_ARCHIVE_SUFFIXES = (".rar", ".zip")


def _session_date(path: Path) -> Optional[date]:
    """Дата сессии по имени папки `log_storage/<unix_ts>/...` (если есть на пути)."""
    for part in path.parts:
        if _SESSION_DIR_RE.match(part):
            try:
                return datetime.utcfromtimestamp(int(part)).date()
            except (ValueError, OSError, OverflowError):
                continue
    return None


def _classify(path: Path) -> Optional[str]:
    name = path.name
    if name.endswith(".ADM"):
        return "adm"
    if name == "server_console.log":
        return "console"
    if name.startswith("LBmaster_") and name.endswith(".log"):
        return "lbmaster"
    if name.startswith("zfbubusik_log-") and name.endswith(".log"):
        return "zf"
    if name.startswith("CarCodeLock_") and name.endswith(".log"):
        return "carcodelock"
    return None


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    if shutil.which("bsdtar") is None:
        raise RuntimeError(
            f"bsdtar не найден в PATH — нужен для распаковки {archive_path} "
            "(установите libarchive/bsdtar)"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bsdtar", "-xf", str(archive_path), "-C", str(dest_dir)], check=True)


def _parse_events(kind: str, path: Path, summary):
    if kind == "adm":
        return adm.parse_file(path, summary=summary)
    if kind == "console":
        return console.parse_file(path, base_date=_session_date(path), summary=summary)
    if kind == "lbmaster":
        return lbmaster.parse_file(path, base_date=_session_date(path), summary=summary)
    if kind == "zf":
        return zf.parse_file(path, summary=summary)
    return carcodelock.parse_file(path, summary=summary)


def _update_identity(index: LogIndex, event: LogEvent, kind: str) -> None:
    if event.player is None:
        return
    if kind == "adm":
        if event.kind == "connect":
            index.upsert_identity(event.ts, name=event.player, bi_hash=event.bi_hash)
        elif event.kind == "disconnect":
            index.upsert_identity(event.ts, name=event.player, steam64=event.steam64)
    elif kind == "console":
        if event.kind == "identity":
            index.upsert_identity(
                event.ts, name=event.player, steam64=event.steam64,
                be_guid=(event.extra or {}).get("be_guid"),
            )
    elif kind == "lbmaster":
        if event.kind in ("join", "leave"):
            index.upsert_identity(event.ts, name=event.player, steam64=event.steam64)


def _update_vehicle(index: LogIndex, event: LogEvent, kind: str) -> None:
    extra = event.extra or {}
    if kind == "zf" and event.kind in ("vehicle_spawn", "vehicle_despawn"):
        index.upsert_vehicle(extra.get("vehicle_class"), extra.get("vehicle_id"), event.x, event.z, event.ts)
    elif kind == "lbmaster" and event.kind in ("garage_store", "garage_restore"):
        index.upsert_vehicle(extra.get("vehicle_class"), extra.get("vehicle_uid"), None, None, event.ts)


def _ingest_file(path: Path, kind: str, index: LogIndex, stats: dict) -> None:
    st = path.stat()
    size, mtime = st.st_size, st.st_mtime

    existing = index.get_file_status(str(path))
    if existing is not None and existing["size"] == size and existing["mtime"] == mtime:
        stats["files_skipped"] += 1
        return

    summary = {
        "adm": adm.ParseSummary, "console": console.ParseSummary, "lbmaster": lbmaster.ParseSummary,
        "zf": zf.ParseSummary, "carcodelock": carcodelock.ParseSummary,
    }[kind]()

    for event in _parse_events(kind, path, summary):
        index.add_event(event, source=kind)
        _update_identity(index, event, kind)
        _update_vehicle(index, event, kind)
        stats["events"] += 1

    index.record_file(str(path), size, mtime, summary.parsed_lines, summary.skipped_lines)
    index.commit()
    stats["files_parsed"] += 1
    logger.info(
        "ingest: %s (%s) — %d событий, %d строк пропущено",
        path, kind, summary.parsed_lines, summary.skipped_lines,
    )


def _walk(root: Path, index: LogIndex, stats: dict, tmp_root: Path) -> None:
    for entry in sorted(root.rglob("*")):
        if entry.is_dir():
            continue
        if entry.suffix.lower() in _ARCHIVE_SUFFIXES:
            extract_dir = Path(tempfile.mkdtemp(prefix=f"{entry.stem}_", dir=tmp_root))
            _extract_archive(entry, extract_dir)
            _walk(extract_dir, index, stats, tmp_root)
            continue
        kind = _classify(entry)
        if kind is None:
            continue
        _ingest_file(entry, kind, index, stats)


def ingest(log_dir: str | Path, db_path: Optional[str] = None) -> dict:
    """Рекурсивно обходит `log_dir`, инкрементально индексируя известные форматы логов."""
    stats = {"files_parsed": 0, "files_skipped": 0, "events": 0}
    with LogIndex(db_path) as index:
        with tempfile.TemporaryDirectory(prefix="disco_bot_logs_") as tmp:
            _walk(Path(log_dir), index, stats, Path(tmp))
    return stats
