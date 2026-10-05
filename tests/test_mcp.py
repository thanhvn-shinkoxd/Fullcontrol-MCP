"""Real MCP protocol tests; skipped only if the optional test environment lacks the SDK."""

import base64
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from support import TestDirectory

from fullremote_mcp.config import Settings

HAS_MCP = importlib.util.find_spec("mcp") is not None


class FakeDesktop:
    def close(self):
        pass

    def observe(self, monitor, max_size, include_ui):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII="
        )
        return {"observation_id": "test-frame", "image": {"width": 1, "height": 1}}, png


@unittest.skipUnless(HAS_MCP, "MCP SDK not installed; install .[dev] to run protocol tests")
class MCPTests(unittest.TestCase):
    def setUp(self):
        from fullremote_mcp.http_api import create_http_app
        from fullremote_mcp.server import build_runtime

        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(token="test-token-with-more-than-32-characters", workspace=root,
                                 data_dir=root / "data")
        self.runtime = build_runtime(self.settings, desktop=FakeDesktop())
        self.client = self.enterContext(TestClient(create_http_app(self.runtime, self.settings),
                                                  base_url="http://localhost:8765"))
        self.headers = {"Authorization": f"Bearer {self.settings.token}",
                        "Accept": "application/json, text/event-stream"}
        self.next_id = 0
        initialized = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "tests", "version": "1"}})
        self.headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        response = self.client.post("/mcp", headers=self.headers,
                                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(response.status_code, 202, response.text)

    def rpc(self, method, params=None):
        self.next_id += 1
        response = self.client.post("/mcp", headers=self.headers,
                                    json={"jsonrpc": "2.0", "id": self.next_id,
                                          "method": method, "params": params or {}})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertNotIn("error", data)
        return data["result"]

    def call(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertFalse(result.get("isError"), result)
        return result

    def test_tool_discovery_and_file_round_trip(self):
        tools = {tool["name"] for tool in self.rpc("tools/list")["tools"]}
        self.assertTrue({"observe", "mouse", "keyboard", "exec", "job_status", "upload", "download"} <= tools)
        self.call("write_text", {"path": "via-mcp.txt", "text": "hello MCP"})
        result = self.call("read_text", {"path": "via-mcp.txt"})
        text = json.loads(next(content["text"] for content in result["content"] if content["type"] == "text"))
        self.assertEqual(text["text"], "hello MCP")

    def test_observe_returns_real_mcp_image_content(self):
        result = self.call("observe", {})
        images = [item for item in result["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(images[0]["data"]).startswith(b"\x89PNG"))

    def test_job_survives_between_stateless_mcp_requests(self):
        result = self.call("run_process", {"program": sys.executable,
                                          "arguments": ["-c", "import time; time.sleep(.2); print('done')"],
                                          "wait_seconds": 0})
        job = json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
        deadline = time.monotonic() + 10
        while job["state"] in {"starting", "running"} and time.monotonic() < deadline:
            time.sleep(0.05)
            result = self.call("job_status", {"job_id": job["job_id"]})
            job = json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
        self.assertEqual(job["state"], "completed", job)
        self.assertIn("done", job["stdout"]["text"])
