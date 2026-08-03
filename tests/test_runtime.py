import tempfile
import unittest
from pathlib import Path

from quota_ring.runtime import InstanceLock


class RuntimeTests(unittest.TestCase):
    def test_instance_lock_rejects_a_second_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota-ring.lock"
            first = InstanceLock(path)
            second = InstanceLock(path)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()
            self.assertTrue(second.acquire())
            second.release()
