import hashlib
import unittest
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient
from support import TestDirectory

from fullremote_mcp.config import Settings
from fullremote_mcp.files import FileService
from fullremote_mcp.http_api import BearerAuth, file_routes

TOKEN = "a-test-token-with-at-least-32-characters"


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = Settings(token=TOKEN, workspace=self.root, data_dir=self.root / "data")
        files = FileService(self.root, 1024, 4096)
        app = BearerAuth(Starlette(routes=file_routes(files)), settings)
        self.client = self.enterContext(TestClient(app, base_url="http://localhost:8765"))
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def test_every_route_requires_authentication(self):
        for path in ("/health", "/files/download?path=test", "/mcp"):
            self.assertEqual(self.client.get(path).status_code, 401)
        response = self.client.put("/files/upload?path=unauthorized.txt", content=b"no")
        self.assertEqual(response.status_code, 401)
        self.assertFalse((self.root / "unauthorized.txt").exists())
        self.assertEqual(self.client.get("/health", headers=self.headers).status_code, 200)

    def test_invalid_duplicate_credentials_and_bad_host_or_origin(self):
        response = self.client.get("/health", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        response = self.client.get("/health", headers=[("Authorization", f"Bearer {TOKEN}")] * 2)
        self.assertEqual(response.status_code, 401)
        response = self.client.get("/health", headers={**self.headers, "Origin": "https://untrusted.test"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/health", headers={**self.headers, "Host": "untrusted.test"})
        self.assertEqual(response.status_code, 421)

    def test_upload_download_range_and_conflict(self):
        data = b"0123456789"
        headers = {**self.headers, "X-Content-SHA256": hashlib.sha256(data).hexdigest()}
        response = self.client.put("/files/upload", params={"path": "a&b.bin"}, content=data, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        response = self.client.get("/files/download", params={"path": "a&b.bin"}, headers=self.headers)
        self.assertEqual(response.content, data)
        response = self.client.get("/files/download", params={"path": "a&b.bin"},
                                   headers={**self.headers, "Range": "bytes=2-5"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        response = self.client.put("/files/upload", params={"path": "a&b.bin"}, content=b"new", headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_checksum_and_size_failure_leave_no_partial_files(self):
        response = self.client.put("/files/upload?path=bad.bin", content=b"bad",
                                   headers={**self.headers, "X-Content-SHA256": "0" * 64})
        self.assertEqual(response.status_code, 400)
        response = self.client.put("/files/upload?path=large.bin", content=b"x" * 4097, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        response = self.client.put("/files/upload?path=chunked.bin", content=iter([b"x" * 3000, b"y" * 2000]),
                                   headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_files_and_bad_parameters(self):
        self.assertEqual(self.client.get("/files/download", headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get("/files/download?path=missing", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.put("/files/upload?path=x&overwrite=yes", headers=self.headers).status_code, 400)
