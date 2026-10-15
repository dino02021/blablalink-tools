"""Bounded mock-workspace backup and fenced restore; no production source paths."""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile

from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json
from executors.local.demo import MARKER

MAX_BACKUP_BYTES = 64 * 1024 * 1024
DATABASES = ("state.sqlite", "staging.sqlite", "messages.sqlite", "chunks.sqlite")


def create_backup(directory: Path, archive: Path, code_sha: str) -> dict:
    directory, archive = Path(directory).resolve(), Path(archive).resolve()
    validate_identifier(code_sha)
    if (not (directory / "mock-workspace.json").is_file() or not (directory / "state.sqlite").is_file()
            or json.loads(_bounded_read(directory / "mock-workspace.json")) != MARKER):
        raise ValueError("not an initialized mock workspace")
    if archive.exists():
        raise ValueError("backup archive already exists")
    files = {"mock-workspace.json": _bounded_read(directory / "mock-workspace.json")}
    guard = sqlite3.connect(directory / "state.sqlite", isolation_level=None, timeout=5)
    try:
        guard.execute("BEGIN IMMEDIATE")
        _require_quiescent(guard)
        migrations = _stored_migrations(guard)
        if migrations != _current_migrations():
            raise StateConflict("backup schema differs from current release")
        books = sorted((directory / "workbooks").glob("*.json"))
        if len(books) > 900:
            raise ValueError("too many mock workbooks")
        for path in books:
            if path.resolve().parent != directory / "workbooks" or not _allowed_file("workbooks/" + path.name):
                raise ValueError("unexpected mock workbook path")
            _add_file(files, "workbooks/" + path.name, _bounded_read(path))
        for name in DATABASES:
            path = directory / name
            if path.exists():
                if path.resolve().parent != directory:
                    raise ValueError("database path escapes mock workspace")
                _add_file(files, name, _database_bytes(path))
        if books != sorted((directory / "workbooks").glob("*.json")) or any(
                files["workbooks/" + path.name] != _bounded_read(path) for path in books):
            raise StateConflict("workbooks changed during backup capture")
        if sum(map(len, files.values())) > MAX_BACKUP_BYTES:
            raise ValueError("backup exceeds local size limit")
        manifest = {"schema_version": 1, "is_mock": True, "release_code_sha": code_sha, "migrations": migrations,
            "files": {name: {"sha256": _hash(content), "size": len(content)} for name, content in sorted(files.items())}}
        serialized = canonical_json(manifest).encode()
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", serialized)
            for name, content in files.items():
                output.writestr(name, content)
        return {"is_mock": True, "backup_id": _hash(serialized), "archive": str(archive), "file_count": len(files)}
    finally:
        guard.rollback()
        guard.close()


def restore_backup(archive: Path, directory: Path, expected_backup_id: str, code_sha: str) -> dict:
    directory = Path(directory).resolve()
    archive = Path(archive).resolve()
    validate_identifier(code_sha)
    if directory.exists():
        raise ValueError("restore requires a new destination directory")
    if archive.stat().st_size > MAX_BACKUP_BYTES + 1024 * 1024:
        raise ValueError("backup archive exceeds local size limit")
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        names = [info.filename for info in infos]
        if (len(names) != len(set(names)) or not 1 <= len(names) <= 1000 or "manifest.json" not in names
                or any(name != "manifest.json" and not _allowed_file(name) for name in names)
                or sum(info.file_size for info in infos) > MAX_BACKUP_BYTES):
            raise ValueError("invalid bounded backup inventory")
        serialized = _zip_read(source, "manifest.json")
        if _hash(serialized) != expected_backup_id:
            raise StateConflict("backup manifest identity differs")
        manifest = json.loads(serialized, object_pairs_hook=_unique_object)
        if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
                or manifest.get("is_mock") is not True or manifest.get("release_code_sha") != code_sha
                or manifest.get("migrations") != _current_migrations()
                or set(manifest["files"]) != set(names) - {"manifest.json"}
                or not {"state.sqlite", "mock-workspace.json"} <= set(manifest["files"])):
            raise StateConflict("backup release, schema or inventory differs")
        files = {name: _zip_read(source, name) for name in manifest["files"]}
    for name, content in files.items():
        if manifest["files"][name] != {"sha256": _hash(content), "size": len(content)}:
            raise StateConflict("backup file integrity differs")
    with tempfile.TemporaryDirectory(prefix="mock-restore-check-") as temporary:
        verification = Path(temporary) / "state.sqlite"
        verification.write_bytes(files["state.sqlite"])
        connection = sqlite3.connect(verification)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise StateConflict("backup database validation failed")
            _require_quiescent(connection)
            if _stored_migrations(connection) != manifest["migrations"]:
                raise StateConflict("backup database schema differs from manifest")
        finally:
            connection.close()
    if json.loads(files["mock-workspace.json"]) != MARKER:
        raise ValueError("backup is not a recognized mock workspace")
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        if name == "mock-workspace.json":
            continue
        path = directory / name  # names already restricted to fixed files and hashed workbook names
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (directory / "restore-lock.json").write_text(canonical_json({"schema_version": 1, "backup_id": expected_backup_id,
        "release_code_sha": code_sha, "execution_mode": "restored_read_only"}), encoding="utf-8")
    (directory / "mock-workspace.json").write_bytes(files["mock-workspace.json"])
    return {"is_mock": True, "backup_id": expected_backup_id, "execution_mode": "restored_read_only", "workspace": str(directory)}


def _require_quiescent(connection):
    if connection.execute("SELECT 1 FROM resource_locks LIMIT 1").fetchone():
        raise StateConflict("publisher resources are still held")
    if connection.execute("SELECT 1 FROM jobs WHERE state IN ('running','dispatched','publishing','publish_uncertain') LIMIT 1").fetchone():
        raise StateConflict("active or uncertain executors must be resolved before backup")
    if connection.execute("SELECT 1 FROM report_parts WHERE state IN ('sending','delivery_uncertain') LIMIT 1").fetchone():
        raise StateConflict("active or uncertain senders must be resolved before backup")


def _database_bytes(path):
    with tempfile.TemporaryDirectory(prefix="mock-db-backup-") as temporary:
        snapshot = Path(temporary) / "snapshot.sqlite"
        source, target = sqlite3.connect(path), sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return _bounded_read(snapshot)


def _bounded_read(path):
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError("backup file exceeds local size limit")
    with path.open("rb") as source:
        value = source.read(MAX_BACKUP_BYTES + 1)
    if len(value) > MAX_BACKUP_BYTES:
        raise ValueError("backup file exceeds local size limit")
    return value


def _add_file(files, name, content):
    if sum(map(len, files.values())) + len(content) > MAX_BACKUP_BYTES:
        raise ValueError("backup exceeds local size limit")
    files[name] = content


def _zip_read(source, name):
    with source.open(name) as entry:
        value = entry.read(MAX_BACKUP_BYTES + 1)
    if len(value) > MAX_BACKUP_BYTES:
        raise ValueError("backup entry exceeds local size limit")
    return value


def _allowed_file(name):
    return name in (*DATABASES, "mock-workspace.json") or re.fullmatch(r"workbooks/[0-9a-f]{64}\.json", name) is not None


def _current_migrations():
    folder = Path(__file__).resolve().parents[1] / "coordination/local_migrations"
    return {str(int(path.name.split("_")[0])): _hash(path.read_text(encoding="utf-8").encode()) for path in sorted(folder.glob("[0-9][0-9][0-9][0-9]_*.sql"))}


def _stored_migrations(connection):
    return {str(row[0]): row[1] for row in connection.execute("SELECT version,checksum FROM schema_migrations ORDER BY version")}


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate backup metadata key")
        result[key] = value
    return result
