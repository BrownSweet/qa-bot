"""Offline workspace backup/restore. Deliberately independent of app.config imports."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone
import zipfile
from contextlib import contextmanager

CURRENT_SCHEMA_VERSION = 5
BACKUP_FORMAT = 1
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024


@contextmanager
def _sqlite(database, **options):
    connection = sqlite3.connect(database, **options)
    try:
        with connection:
            yield connection
    finally:
        # sqlite3's own context manager commits, but does not close handles.
        # Windows must release them before restore renames or temp cleanup.
        connection.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _keys(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if any(not isinstance(data.get(key), str) or len(data[key]) < 64 for key in ("aes", "jwt")):
            raise ValueError()
        return data
    except (ValueError, OSError, AttributeError) as error:
        raise RuntimeError("备份密钥文件无效，不能恢复") from error


def schema_version(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "schema_migrations" not in tables:
        return 1 if tables else 0
    return connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]


def validate_database(database, secrets_file):
    """Verify SQLite consistency and decrypt every stored credential with the paired key."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    keys = _keys(secrets_file)
    with _sqlite(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("备份数据库完整性检查失败")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "sessions", "messages", "system_configs", "db_configs"}.issubset(tables):
            raise RuntimeError("备份不是有效的问答机器人工作区")
        version = schema_version(connection)
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError("备份来自更新版本，请先升级应用，不能降级恢复")
        for table, column in (("system_configs", "api_key"), ("db_configs", "password")):
            for (encrypted,) in connection.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                if not encrypted:
                    continue
                try:
                    raw = base64.b64decode(encrypted, validate=True)
                    decryptor = Cipher(algorithms.AES(hashlib.sha256(keys["aes"].encode()).digest()), modes.CBC(raw[:16])).decryptor()
                    padded = decryptor.update(raw[16:]) + decryptor.finalize()
                    unpadder = padding.PKCS7(128).unpadder()
                    (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
                except Exception as error:
                    raise RuntimeError("备份数据库与密钥不匹配，或已有凭证损坏，不能恢复") from error
        columns = {row[1] for row in connection.execute("PRAGMA table_info(db_configs)")}
        # Early local-source configurations kept the file path in database_name.
        # Match the query engine's legacy fallback while restoring into file_path.
        path_expression = "COALESCE(NULLIF(file_path, ''), database_name)" if "database_name" in columns else "file_path"
        external = [{"id": row[0], "type": row[1], "path": row[2]} for row in connection.execute(f"SELECT id, type, {path_expression} FROM db_configs WHERE type IN ('sqlite', 'excel')")]
        return {"schemaVersion": version, "externalFileReferences": len(external), "externalFiles": external,
                "message": "备份包含会话、配置和配对密钥；外部数据文件未打包，恢复到其他设备后请重新定位。"}


def create_backup(data_dir, destination, app_version="1.2.1", include_sources=False):
    directory, output = Path(data_dir).resolve(), Path(destination).resolve()
    if output in {directory / "qabot.db", directory / "secrets.json"}:
        raise RuntimeError("备份文件不能覆盖工作区数据")
    database, keys = directory / "qabot.db", directory / "secrets.json"
    if not database.is_file() or not keys.is_file():
        raise RuntimeError("工作区数据库或密钥缺失，请先恢复完整备份")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qa-backup-") as temp:
        snapshot = Path(temp) / "qabot.db"
        deadline = time.monotonic() + 60
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise RuntimeError("数据库备份超时，请停止正在运行的分析后重试")
        with _sqlite(database.as_uri() + "?mode=ro", uri=True) as source, _sqlite(snapshot) as target:
            source.backup(target, pages=128, progress=progress, sleep=0.01)
        saved_keys = Path(temp) / "secrets.json"
        shutil.copyfile(keys, saved_keys)
        metadata = validate_database(snapshot, saved_keys)
        files = {name: {"sha256": _digest(Path(temp) / name), "bytes": (Path(temp) / name).stat().st_size}
                 for name in ("qabot.db", "secrets.json")}
        if snapshot.stat().st_size > MAX_DATABASE_BYTES:
            raise RuntimeError("备份数据库超过 1 GiB")
        if include_sources:
            if len(metadata["externalFiles"]) > 100:
                raise RuntimeError("一次备份最多包含 100 个本地数据文件")
            for reference in metadata["externalFiles"]:
                source = Path(reference["path"] or "").expanduser().resolve()
                if not source.is_file() or source.is_relative_to(directory):
                    raise RuntimeError("本地数据文件缺失或指向内部工作区，请重新定位或选择仅备份配置")
                if source.stat().st_size > MAX_SOURCE_BYTES:
                    raise RuntimeError("携带的单个数据文件不得超过 256 MiB")
                extension = ".sqlite" if reference["type"] == "sqlite" else source.suffix.lower()
                if extension not in {".sqlite", ".xlsx", ".xlsm"}:
                    raise RuntimeError("仅能携带 SQLite / Excel 数据文件")
                entry = "sources/" + hashlib.sha256(reference["id"].encode()).hexdigest()[:32] + extension
                saved = Path(temp) / entry
                saved.parent.mkdir(exist_ok=True)
                if reference["type"] == "sqlite":
                    with _sqlite(source.as_uri() + "?mode=ro", uri=True) as original, _sqlite(saved) as copy:
                        original.backup(copy, pages=128, progress=progress, sleep=0.01)
                else:
                    shutil.copyfile(source, saved)
                if saved.stat().st_size > MAX_SOURCE_BYTES:
                    raise RuntimeError("备份后的单个数据文件超过 256 MiB，请缩小文件或选择仅备份配置")
                reference["entry"] = entry
                files[entry] = {"sha256": _digest(saved), "bytes": saved.stat().st_size}
            if sum(info["bytes"] for info in files.values()) > MAX_DATABASE_BYTES:
                raise RuntimeError("备份内容总量不得超过 1 GiB")
        manifest = {"formatVersion": BACKUP_FORMAT, "version": app_version, "createdAt": _now(), **metadata,
                    "externalFilesIncluded": bool(include_sources), "files": files,
                    "message": "已包含本地数据文件，可跨设备恢复。" if include_sources else "外部数据文件未包含，跨设备恢复后需重新定位。"}
        fd, staged_name = tempfile.mkstemp(prefix=".qa-backup-", suffix=".zip", dir=output.parent)
        os.close(fd)
        staged = Path(staged_name)
        try:
            with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                for name in files:
                    archive.write(Path(temp) / name, name)
            os.chmod(staged, 0o600)
            os.replace(staged, output)
        finally:
            staged.unlink(missing_ok=True)
    return {"path": str(output), "valid": True, **manifest}


def _extract_verified(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 103 or not {"manifest.json", "qabot.db", "secrets.json"}.issubset(names):
            raise RuntimeError("备份文件清单无效，禁止额外文件或路径")
        if archive.getinfo("manifest.json").file_size > 65536:
            raise RuntimeError("备份清单过大")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("formatVersion") != BACKUP_FORMAT:
            raise RuntimeError("不支持的备份格式版本")
        if set(names) != set(manifest.get("files", {})) | {"manifest.json"}:
            raise RuntimeError("备份文件清单不一致")
        if sum(info.file_size for info in archive.infolist()) > MAX_DATABASE_BYTES + 65536:
            raise RuntimeError("备份总量超过允许大小")
        for info in archive.infolist():
            if info.filename not in {"qabot.db", "secrets.json", "manifest.json"} and not re.fullmatch(r"sources/[a-f0-9]{32}\.(?:sqlite|xlsx|xlsm)", info.filename):
                raise RuntimeError("备份包含不安全路径")
            limit = MAX_DATABASE_BYTES if info.filename == "qabot.db" else (MAX_SOURCE_BYTES if info.filename.startswith("sources/") else 65536)
            if info.file_size > limit or info.file_size < 0:
                raise RuntimeError("备份文件超过允许大小")
            target_path = destination / info.filename
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    for name in manifest["files"]:
        expected = manifest.get("files", {}).get(name, {})
        if expected.get("sha256") != _digest(destination / name) or expected.get("bytes") != (destination / name).stat().st_size:
            raise RuntimeError("备份校验失败，文件已损坏或内容不一致")
    metadata = validate_database(destination / "qabot.db", destination / "secrets.json")
    if manifest.get("schemaVersion") != metadata["schemaVersion"]:
        raise RuntimeError("备份数据库版本与清单不一致")
    references = manifest.get("externalFiles", [])
    allowed = {item["id"] for item in metadata["externalFiles"]}
    for reference in references:
        if reference.get("id") not in allowed or (reference.get("entry") and (not reference["entry"].startswith("sources/") or reference["entry"] not in manifest["files"])):
            raise RuntimeError("备份数据源映射无效")
    missing = [item["id"] for item in references if not item.get("entry") and not Path(item.get("path") or "").is_file()]
    return {"valid": True, **manifest, "missingExternalFileIds": missing,
            "message": "校验通过；已携带的文件会自动重新定位。" if manifest.get("externalFilesIncluded") else "校验通过；未携带外部数据文件，换机后需重新定位。"}


def inspect_backup(archive_path):
    with tempfile.TemporaryDirectory(prefix="qa-verify-") as temp:
        return _extract_verified(archive_path, Path(temp))


def restore_backup(data_dir, archive_path):
    """Caller must stop the backend. A rollback journal makes two-file replacement recoverable."""
    directory = Path(data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    recover_interrupted_restore(directory)
    with tempfile.TemporaryDirectory(prefix=".qa-restore-", dir=directory) as temp:
        staged = Path(temp)
        manifest = _extract_verified(archive_path, staged)
        relocated = None
        if (staged / "sources").exists():
            relocated = directory.parent / "sources" / f"restored-{uuid.uuid4().hex}"
            with _sqlite(staged / "qabot.db") as connection:
                for reference in manifest.get("externalFiles", []):
                    if reference.get("entry"):
                        connection.execute("UPDATE db_configs SET file_path = ? WHERE id = ?", (str(relocated / Path(reference["entry"]).name), reference["id"]))
        previous_backup = None
        if (directory / "qabot.db").is_file() and (directory / "secrets.json").is_file():
            try:
                validate_database(directory / "qabot.db", directory / "secrets.json")
            except Exception:
                pass  # A damaged workspace is precisely why a restore may be needed.
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                previous_backup = str(directory / "backups" / f"pre-restore-{stamp}.zip")
                create_backup(directory, previous_backup)
        rollback = directory / ".restore-rollback"
        rollback.mkdir(mode=0o700)
        originals = []
        try:
            for name in ("qabot.db", "secrets.json", "qabot.db-wal", "qabot.db-shm", "qabot.db-journal"):
                if (directory / name).exists():
                    shutil.copyfile(directory / name, rollback / name)
                    originals.append(name)
            with (rollback / "state.json").open("w", encoding="utf-8") as journal:
                json.dump({"originals": originals, "relocatedSources": relocated.name if relocated else None}, journal)
                journal.flush()
                os.fsync(journal.fileno())
            if relocated:
                relocated.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(staged / "sources", relocated)
            for name in ("qabot.db-wal", "qabot.db-shm", "qabot.db-journal"):
                (directory / name).unlink(missing_ok=True)
            for name in ("qabot.db", "secrets.json"):
                os.chmod(staged / name, 0o600)
                os.replace(staged / name, directory / name)
            validate_database(directory / "qabot.db", directory / "secrets.json")
            # Rename commits the restore atomically. Partial cleanup can then
            # never be mistaken for an incomplete replacement on next launch.
            completed = directory / f".restore-completed-{uuid.uuid4().hex}"
            os.replace(rollback, completed)
            shutil.rmtree(completed, ignore_errors=True)
        except Exception:
            recover_interrupted_restore(directory)
            if relocated:
                shutil.rmtree(relocated, ignore_errors=True)
            raise
    return {"restored": True, "previousBackup": previous_backup, **manifest}


def recover_interrupted_restore(data_dir):
    directory = Path(data_dir)
    for completed in directory.glob(".restore-completed-*"):
        if re.fullmatch(r"\.restore-completed-[a-f0-9]{32}", completed.name):
            if completed.is_symlink():
                completed.unlink()
            else:
                shutil.rmtree(completed, ignore_errors=True)
    rollback = directory / ".restore-rollback"
    if not rollback.exists():
        return
    state = rollback / "state.json"
    if not state.is_file():
        # Replacement only begins after the journal has been written.
        shutil.rmtree(rollback)
        return
    recorded = json.loads(state.read_text(encoding="utf-8"))
    originals = recorded["originals"]
    for name in ("qabot.db", "secrets.json", "qabot.db-wal", "qabot.db-shm", "qabot.db-journal"):
        target = directory / name
        if name in originals:
            shutil.copyfile(rollback / name, target)
        else:
            target.unlink(missing_ok=True)
    relocated = recorded.get("relocatedSources")
    if isinstance(relocated, str) and re.fullmatch(r"restored-[a-f0-9]{32}", relocated):
        source_directory = directory.parent / "sources" / relocated
        if source_directory.is_symlink():
            source_directory.unlink()
        else:
            shutil.rmtree(source_directory, ignore_errors=True)
    shutil.rmtree(rollback)


def export_diagnostics(data_dir, destination):
    import platform
    directory = Path(data_dir).resolve()
    output = Path(destination).resolve()
    if output.is_relative_to(directory):
        raise RuntimeError("请将诊断包保存到数据目录之外")
    report = {"createdAt": _now(), "schemaVersion": CURRENT_SCHEMA_VERSION,
              "platform": platform.system(), "architecture": platform.machine(),
              "python": platform.python_version(), "databaseExists": (directory / "qabot.db").is_file(),
              "keysExist": (directory / "secrets.json").is_file(),
              "excluded": ["database", "secrets", "source files", "environment variables", "raw log text", "SQL", "query results"]}
    logs = []
    for file in sorted((directory.parent / "logs").glob("desktop.log*")):
        if not file.is_file() or not re.fullmatch(r"desktop\.log(?:\.[0-9]+)?", file.name):
            continue
        with file.open("rb") as stream:
            stream.seek(max(0, file.stat().st_size - 1024 * 1024))
            content = stream.read().decode("utf-8", errors="replace")
        # Arbitrary traceback messages may contain SQL, cells or personal data.
        # Export only numeric summaries, never message text or guessed redaction.
        logs.append({"name": file.name, "bytes": file.stat().st_size, "sampledLines": len(content.splitlines()),
                     "backendRecords": content.count(" [backend] "), "rendererRecords": content.count(" [renderer] "),
                     "tracebacks": content.count("Traceback (most recent call last)"),
                     "unexpectedExits": content.count("本地服务意外退出")})
    report["logSummary"] = logs
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(report, ensure_ascii=False, indent=2))
    os.chmod(output, 0o600)
    return {"path": str(output), "createdAt": report["createdAt"]}


def cli(argv):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", choices=["backup", "inspect", "restore", "diagnostics"], required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--include-sources", action="store_true")
    options = parser.parse_args(argv)
    operations = {"backup": lambda: create_backup(options.data_dir, options.file, include_sources=options.include_sources),
                  "inspect": lambda: inspect_backup(options.file),
                  "restore": lambda: restore_backup(options.data_dir, options.file),
                  "diagnostics": lambda: export_diagnostics(options.data_dir, options.file)}
    try:
        result = operations[options.workspace]()
        print("QA_WORKSPACE_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print("QA_WORKSPACE_RESULT " + json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)
        return 1
