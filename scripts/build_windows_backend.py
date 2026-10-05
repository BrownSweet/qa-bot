"""Assemble a Windows x64 backend on any host, without executing Windows code.

Uses the official CPython embeddable runtime and prebuilt Windows wheels. This is
resource assembly, not a claim that the resulting application was run on Windows.
Requires `uv` on PATH. Output is separate from native PyInstaller builds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.16"
ARCHIVE_NAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
ARCHIVE_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{ARCHIVE_NAME}"
RELEASE_URL = "https://www.python.org/downloads/release/python-31316/"
# Published in the official release page's Windows embeddable package (64-bit)
# SHA-256 column, checked 2026-10-03. An actual digest match is required below.
ARCHIVE_SHA256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
OUTPUT_PARENT = Path(os.getenv("QA_BUILD_ROOT", str(ROOT / "build"))).resolve() / "backend-windows"
OUTPUT = OUTPUT_PARENT / "qa-backend"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path: Path, expected: str = ARCHIVE_SHA256) -> str:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"Python archive SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def download_runtime() -> Path:
    cache = ROOT / "build/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / ARCHIVE_NAME
    if destination.is_file():
        verify_archive(destination)
        print(f"Using verified runtime archive: {destination}", flush=True)
        return destination
    partial = destination.with_suffix(".zip.part")
    try:
        print(f"Downloading {ARCHIVE_URL}", flush=True)
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "qa-robot-desktop-build"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as target:
            if not response.geturl().startswith("https://"):
                raise RuntimeError("Runtime download redirected outside HTTPS")
            shutil.copyfileobj(response, target)
        verify_archive(partial)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def extract_runtime(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as runtime:
        for member in runtime.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or ":" in member.filename:
                raise RuntimeError(f"Unsafe runtime archive path: {member.filename}")
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError(f"Runtime archive must not contain symlinks: {member.filename}")
        runtime.extractall(destination)


def copy_backend_sources(source: Path, destination: Path) -> list[str]:
    # Intentionally enumerate only source files; never copy .env, virtualenvs,
    # user files, development databases, test data or repository build outputs.
    files = [source / "main.py", source / "desktop_server.py"]
    files.extend(sorted((source / "app").rglob("*.py")))
    copied = []
    for file in files:
        if not file.is_file() or file.is_symlink():
            raise RuntimeError(f"Expected a regular backend source file: {file}")
        relative = file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        copied.append(relative.as_posix())
    return copied


def install_windows_wheels(destination: Path, requirements: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required; install it before building Windows resources")
    target = destination / "Lib/site-packages"
    subprocess.run([
        uv, "pip", "install", "--python-version", "3.13",
        "--python-platform", "x86_64-pc-windows-msvc", "--only-binary", ":all:",
        "--target", str(target), "--default-index", "https://pypi.org/simple",
        "--no-config", "-r", str(requirements),
    ], cwd=ROOT, check=True)
    # Cross-host uv may produce host-specific console wrappers. This backend
    # imports uvicorn directly, so none of these wrappers belongs in the bundle.
    for folder in (target / "bin", target / "Scripts"):
        if folder.is_dir():
            shutil.rmtree(folder)


def pe_machine(path: Path) -> int:
    with path.open("rb") as binary:
        header = binary.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise RuntimeError(f"Not a Windows PE binary: {path}")
        offset = struct.unpack_from("<I", header, 60)[0]
        binary.seek(offset)
        coff = binary.read(6)
        if len(coff) < 6 or coff[:4] != b"PE\0\0":
            raise RuntimeError(f"Invalid Windows PE header: {path}")
        return struct.unpack_from("<H", coff, 4)[0]


def compatible_tag(tag: str) -> bool:
    python, abi, platform = tag.split("-", 2)
    if platform == "any":
        return abi == "none" and bool({"py3", "py313"}.intersection(python.split(".")))
    if platform != "win_amd64":
        return False
    if python == "cp313" and abi in {"cp313", "abi3", "none"}:
        return True
    if python.startswith("cp3") and abi == "abi3":
        return python[2:].isdigit() and 32 <= int(python[2:]) <= 313
    return False


def validate_windows_bundle(destination: Path) -> dict:
    for required in ("python.exe", "python313.dll", "python313.zip", "main.py", "desktop_server.py", "app/__init__.py"):
        if not (destination / required).is_file():
            raise RuntimeError(f"Missing runtime file: {required}")
    native = []
    pyd_count = 0
    for path in sorted(destination.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".so", ".dylib"}:
            raise RuntimeError(f"Non-Windows native library in bundle: {path}")
        if path.suffix.lower() in {".pyd", ".dll", ".exe"}:
            if pe_machine(path) != 0x8664:
                raise RuntimeError(f"Expected x64 PE binary: {path}")
            native.append(path.relative_to(destination).as_posix())
            pyd_count += path.suffix.lower() == ".pyd"
    packages = []
    for wheel in sorted((destination / "Lib/site-packages").glob("*.dist-info/WHEEL")):
        tags = [line[5:].strip() for line in wheel.read_text().splitlines() if line.startswith("Tag: ")]
        if not tags or not any(compatible_tag(tag) for tag in tags):
            raise RuntimeError(f"Wheel is not compatible with CPython 3.13 Windows x64: {wheel}: {tags}")
        if any(tag.rsplit("-", 1)[-1] not in {"win_amd64", "any"} for tag in tags):
            raise RuntimeError(f"Foreign platform wheel: {wheel}: {tags}")
        metadata = Parser().parsestr((wheel.parent / "METADATA").read_text())
        packages.append({"name": metadata["Name"], "version": metadata["Version"], "tags": tags})
    if not packages or not pyd_count:
        raise RuntimeError("Windows dependency installation is incomplete")
    return {"native_files": native, "pyd_count": pyd_count, "packages": packages}


def main() -> None:
    requirements = ROOT / "backend/requirements-desktop.lock"
    if not requirements.is_file():
        raise RuntimeError(f"Missing desktop requirements lock: {requirements}")
    archive = download_runtime()
    OUTPUT_PARENT.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qa-backend-", dir=OUTPUT_PARENT))
    try:
        extract_runtime(archive, staging)
        install_windows_wheels(staging, requirements)
        sources = copy_backend_sources(ROOT / "backend", staging)
        # import site is required for vendor .pth files such as psycopg2.libs.
        (staging / "python313._pth").write_text(
            "python313.zip\n.\napp\nLib/site-packages\nimport site\n", encoding="utf-8"
        )
        validation = validate_windows_bundle(staging)
        info = {
            "platform": "win32", "architecture": "x64", "python": PYTHON_VERSION,
            "runtime": "python-embedded", "entrypoint": ["python.exe", "desktop_server.py"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "runtime_url": ARCHIVE_URL, "runtime_sha256": verify_archive(archive),
            "runtime_checksum_source": RELEASE_URL, "requirements_sha256": sha256(requirements),
            "source_files": sources, "validation": validation, "windows_execution_tested": False,
            "source_sha256": {name: sha256(staging / name) for name in sources},
        }
        (staging / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if OUTPUT.exists():
            shutil.rmtree(OUTPUT)
        staging.replace(OUTPUT)
        size = sum(file.stat().st_size for file in OUTPUT.rglob("*") if file.is_file())
        print(json.dumps({
            "output": str(OUTPUT), "bytes": size, "mib": round(size / 1024**2, 2),
            "packages": len(validation["packages"]), "pyd_count": validation["pyd_count"],
            "native_pe_x64_files": len(validation["native_files"]), "windows_execution_tested": False,
        }, indent=2), flush=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    main()
