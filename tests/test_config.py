import os
import unittest
from unittest.mock import patch

from fullremote_mcp.config import Settings


class ConfigTests(unittest.TestCase):
    def test_http_requires_a_strong_nonempty_token(self):
        for token in ("", "short", "a" * 32 + " ", "\u1ebf" * 40):
            with self.subTest(token_length=len(token)), self.assertRaises(ValueError):
                Settings(token=token).validate_http()
        Settings(token="a" * 48).validate_http()

    def test_remote_binding_requires_reachable_host_allowlist(self):
        with self.assertRaisesRegex(ValueError, "ALLOWED_HOSTS"):
            Settings(host="0.0.0.0", token="a" * 48).validate_http()
        settings = Settings(host="0.0.0.0", token="a" * 48, allowed_hosts=("vm.example:8765",))
        settings.validate_http()
        self.assertIn("vm.example:8765", settings.trusted_hosts)

    def test_invalid_environment_limit_is_rejected(self):
        with patch.dict(os.environ, {"FULLREMOTE_MAX_JOBS": "0"}):
            with self.assertRaisesRegex(ValueError, "MAX_JOBS"):
                Settings.from_env()

    def test_token_does_not_appear_in_settings_repr(self):
        token = "a-unique-secret-which-should-not-be-in-logs"
        self.assertNotIn(token, repr(Settings(token=token)))
