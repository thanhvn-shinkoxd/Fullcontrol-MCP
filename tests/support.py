"""Test directories inherit the workspace ACL on restricted Windows runners."""

import shutil
import uuid
from pathlib import Path


class TestDirectory:
    def __init__(self):
        self.path = Path.cwd() / ".test-data" / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        self.name = str(self.path)

    def cleanup(self):
        shutil.rmtree(self.path)
