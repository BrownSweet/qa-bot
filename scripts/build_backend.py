"""Build a native backend with the active Python 3.12 environment (one OS/architecture per build)."""
import json
import platform
import os
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.getenv("QA_BUILD_ROOT", str(ROOT / "build"))).resolve()


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("桌面构建请使用 Python 3.12，并安装 backend/requirements-build.txt")
    def source_hashes_now():
        source_files = [ROOT / "backend/main.py", ROOT / "backend/desktop_server.py", *(ROOT / "backend/app").rglob("*.py")]
        return {file.relative_to(ROOT / "backend").as_posix(): hashlib.sha256(file.read_bytes()).hexdigest() for file in source_files}
    source_hashes = source_hashes_now()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
           "--name", "qa-backend", "--distpath", str(BUILD / "backend"),
           "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
           "--paths", str(ROOT / "backend"),
           "--collect-all", "sqlglot", "--collect-submodules", "sqlalchemy.dialects.sqlite",
           "--collect-submodules", "sqlalchemy.dialects.mysql", "--collect-submodules", "sqlalchemy.dialects.postgresql",
           "--hidden-import", "pymysql", "--hidden-import", "psycopg2", "--hidden-import", "uvicorn.logging",
           "--hidden-import", "uvicorn.protocols.http.h11_impl", "--hidden-import", "uvicorn.loops.asyncio",
           "--hidden-import", "uvicorn.lifespan.on", str(ROOT / "backend/desktop_server.py")]
    subprocess.run(cmd, cwd=ROOT, check=True)
    if source_hashes_now() != source_hashes:
        raise RuntimeError("Backend source changed during build; rebuild after edits finish")
    (BUILD / "backend/qa-backend/build-info.json").write_text(json.dumps({
        "platform": sys.platform, "architecture": platform.machine(), "python": platform.python_version(),
        "source_sha256": source_hashes,
        "requirements_sha256": hashlib.sha256((ROOT / "backend/requirements-desktop.lock").read_bytes()).hexdigest(),
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
