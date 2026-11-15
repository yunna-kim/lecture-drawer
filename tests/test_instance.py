import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from app.config import ROOT
from app.instance import acquire


@unittest.skipUnless(os.name == "nt", "Windows byte-range lock regression")
class InstanceLockTest(unittest.TestCase):
    def test_second_process_detects_existing_instance_without_reading_locked_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            first=acquire(home)
            self.assertIsNotNone(first)
            try:
                result=subprocess.run([sys.executable,'-X','utf8','-c',
                    'from pathlib import Path;from app.instance import acquire;import sys;'
                    'lock=acquire(Path(sys.argv[1]));print("duplicate" if lock is None else "new");'
                    'lock.close() if lock else None',folder],cwd=ROOT,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'duplicate')
            finally:
                first.close()
            again=acquire(home)
            self.assertIsNotNone(again)
            again.close()

    def test_new_empty_lock_file_can_be_started(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            lock=acquire(home)
            self.assertEqual((home/'service.lock').stat().st_size,1)
            lock.close()
