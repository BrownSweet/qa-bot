"""Run two real desktop versions against one isolated synthetic profile."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from contextlib import closing


def executable(release):
    directory = Path(release).resolve()
    if os.name == "nt":
        return directory / "win-unpacked" / "QA Robot.exe"
    folder = "mac-arm64" if platform.machine() == "arm64" else "mac"
    return directory / folder / "QA Robot.app/Contents/MacOS/QA Robot"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-release", required=True)
    parser.add_argument("--to-release", required=True)
    args = parser.parse_args()
    profile = Path(tempfile.mkdtemp(prefix="qa-upgrade-smoke-"))
    succeeded = False
    try:
        def run(release):
            result = subprocess.run([str(executable(release))], capture_output=True, text=True, timeout=90,
                                    env={**os.environ, "QA_DESKTOP_SMOKE": "1", "QA_DESKTOP_USER_DATA": str(profile)})
            reports = []
            for line in result.stdout.splitlines():
                try:
                    reports.append(json.loads(line))
                except ValueError:
                    pass
            report = next((item for item in reports if item.get("event") == "QA_DESKTOP_SMOKE"), {})
            if result.returncode or report.get("ok") is not True:
                raise RuntimeError(f"Desktop version smoke failed: {report.get('message', result.returncode)}")
            return report["version"]

        old_version = run(args.from_release)
        database = profile / "data/qabot.db"
        keys = (profile / "data/secrets.json").read_bytes()
        with closing(sqlite3.connect(database)) as connection:
            original_sessions = {row[0] for row in connection.execute("SELECT id FROM sessions")}
            has_migrations = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone() is not None
            old_schema_version = (connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                                  if has_migrations else 1)
        assert original_sessions, "Old desktop did not save a synthetic session"
        new_version = run(args.to_release)
        assert keys == (profile / "data/secrets.json").read_bytes(), "Upgrade replaced the original keys"
        with closing(sqlite3.connect(database)) as connection:
            retained = {row[0] for row in connection.execute("SELECT id FROM sessions")}
            assert original_sessions.issubset(retained), "Upgrade lost an existing session"
            schema_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            assert schema_version >= old_schema_version
        snapshots = list((profile / "data/backups").glob(f"pre-upgrade-v{old_schema_version}-*.zip"))
        assert snapshots, "Upgrade did not save a rollback backup"
        with zipfile.ZipFile(snapshots[0]) as archive:
            assert json.loads(archive.read("manifest.json"))["schemaVersion"] == old_schema_version
        succeeded = True
        print(json.dumps({"desktopUpgradeSmoke": "passed", "from": old_version, "to": new_version,
                          "fromSchemaVersion": old_schema_version, "schemaVersion": schema_version,
                          "preservedSessions": len(original_sessions),
                          "checks": ["existing-keys", "existing-sessions", "pre-upgrade-backup", "real-packaged-ui"]}))
    finally:
        if succeeded:
            shutil.rmtree(profile)
        else:
            print(f"Upgrade smoke logs preserved: {profile}")


if __name__ == "__main__":
    main()
