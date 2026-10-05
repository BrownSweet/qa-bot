"""Host-only checks; these never execute bundled Windows binaries."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "build_windows_backend.py"
spec = importlib.util.spec_from_file_location("windows_builder", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WindowsBundleTests(unittest.TestCase):
    def test_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "runtime.zip"
            archive.write_bytes(b"test-runtime")
            expected = hashlib.sha256(b"test-runtime").hexdigest()
            self.assertEqual(builder.verify_archive(archive, expected), expected)
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                builder.verify_archive(archive, "0" * 64)

    def test_archive_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("../outside.py", "invalid")
            with self.assertRaisesRegex(RuntimeError, "Unsafe runtime archive path"):
                builder.extract_runtime(archive, root / "destination")
            self.assertFalse((root / "outside.py").exists())

    def test_only_backend_source_files_are_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "backend"
            (source / "app/routers").mkdir(parents=True)
            for relative in ("main.py", "desktop_server.py", "app/__init__.py", "app/routers/chat.py"):
                (source / relative).write_text("# source\n")
            for relative in (".env", "app/data.db", "app/config.json"):
                (source / relative).write_text("must not be copied\n")
            destination = root / "output"
            copied = builder.copy_backend_sources(source, destination)
            self.assertEqual(set(copied), {"main.py", "desktop_server.py", "app/__init__.py", "app/routers/chat.py"})
            self.assertFalse((destination / ".env").exists())
            self.assertFalse((destination / "app/data.db").exists())

    def test_pe_header_and_architecture_are_read_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "extension.pyd"
            header = bytearray(64)
            header[:2] = b"MZ"
            struct.pack_into("<I", header, 60, 64)
            binary.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x8664))
            self.assertEqual(builder.pe_machine(binary), 0x8664)
            binary.write_bytes(b"not a PE binary")
            with self.assertRaisesRegex(RuntimeError, "Not a Windows PE"):
                builder.pe_machine(binary)

    def test_wheel_tags_require_python313_compatible_windows_x64(self):
        for tag in ("cp313-cp313-win_amd64", "cp39-abi3-win_amd64", "py3-none-any", "py2.py3-none-any"):
            with self.subTest(tag=tag):
                self.assertTrue(builder.compatible_tag(tag))
        for tag in ("cp313-cp313-macosx_11_0_arm64", "cp313-cp313-win32", "cp313-cp313t-win_amd64", "cp312-cp312-win_amd64", "cp314-abi3-win_amd64"):
            with self.subTest(tag=tag):
                self.assertFalse(builder.compatible_tag(tag))

    def test_windows_output_is_separate_from_native_backend(self):
        self.assertEqual(builder.OUTPUT, builder.ROOT / "build/backend-windows/qa-backend")
        self.assertNotEqual(builder.OUTPUT_PARENT, builder.ROOT / "build/backend")


if __name__ == "__main__":
    unittest.main()
