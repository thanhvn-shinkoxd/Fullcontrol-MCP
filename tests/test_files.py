import base64
import hashlib
import unittest
from pathlib import Path

from support import TestDirectory

from fullremote_mcp.files import ChecksumMismatch, FileService, UploadTooLarge


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = FileService(self.root, max_inline_bytes=1024, max_upload_bytes=4096)

    def test_binary_round_trip_and_pagination(self):
        data = bytes(range(256)) * 3
        digest = hashlib.sha256(data).hexdigest()
        result = self.files.upload("sample.bin", base64.b64encode(data).decode(), sha256=digest)
        self.assertEqual(result["sha256"], digest)
        chunks, offset = [], 0
        while True:
            result = self.files.download("sample.bin", offset, 113)
            chunks.append(base64.b64decode(result["content_base64"]))
            offset = result["next_offset"]
            if result["eof"]:
                break
        self.assertEqual(b"".join(chunks), data)

    def test_existing_file_requires_explicit_overwrite(self):
        self.files.write_text("file.txt", "original")
        with self.assertRaises(FileExistsError):
            self.files.write_text("file.txt", "changed")
        self.assertEqual((self.root / "file.txt").read_text(), "original")
        self.files.write_text("file.txt", "changed", overwrite=True)
        self.assertEqual(self.files.read_text("file.txt")["text"], "changed")

    def test_checksum_failure_preserves_original_and_cleans_partial(self):
        target = self.root / "file.bin"
        target.write_bytes(b"original")
        with self.assertRaises(ChecksumMismatch):
            self.files.upload("file.bin", "bmV3", overwrite=True, sha256="0" * 64)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_interrupted_upload_is_not_visible(self):
        sink = self.files.begin_upload("pending.bin")
        sink.write(b"partial")
        self.assertFalse((self.root / "pending.bin").exists())
        sink.abort()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_no_overwrite_race_is_atomic(self):
        sink = self.files.begin_upload("raced.bin")
        try:
            sink.write(b"upload")
            (self.root / "raced.bin").write_bytes(b"concurrent writer")
            with self.assertRaises(FileExistsError):
                sink.finish()
        finally:
            sink.abort()
        self.assertEqual((self.root / "raced.bin").read_bytes(), b"concurrent writer")

    def test_inline_and_stream_limits(self):
        with self.assertRaises(UploadTooLarge):
            self.files.upload("too-big.bin", base64.b64encode(b"x" * 1025).decode())
        sink = self.files.begin_upload("too-big.bin")
        try:
            sink.write(b"x" * 4000)
            with self.assertRaises(UploadTooLarge):
                sink.write(b"x" * 97)
        finally:
            sink.abort()
        self.assertFalse((self.root / "too-big.bin").exists())

    def test_utf8_text_with_partial_multibyte_character(self):
        self.files.write_text("unicode.txt", "a\u1ebf\U0001f338")
        clipped = self.files.read_text("unicode.txt", max_bytes=3)
        self.assertEqual(clipped["text"], "a")
        self.assertEqual(clipped["next_offset"], 1)
        self.assertFalse(clipped["eof"])
        self.assertEqual(self.files.read_text("unicode.txt")["text"], "a\u1ebf\U0001f338")

    def test_directory_pagination_and_encoded_transfer_path(self):
        for name in ("b.txt", "a.txt", "c.txt"):
            self.files.write_text(name, name)
        first = self.files.list_directory(limit=2)
        self.assertEqual([entry["name"] for entry in first["entries"]], ["a.txt", "b.txt"])
        second = self.files.list_directory(offset=first["next_offset"], limit=2)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(second["entries"][0]["name"], "c.txt")
        self.assertIn("a%26b", self.files.transfer_info("a&b.bin")["upload"]["path"])

    def test_invalid_base64_does_not_create_file(self):
        with self.assertRaises(ValueError):
            self.files.upload("bad.bin", "not base64!")
        self.assertEqual(list(self.root.iterdir()), [])
