from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.gpu_schedule import voice_session,summary_session,SummaryDeferred,unload_notes


class GPUScheduleTest(unittest.TestCase):
    @patch('app.gpu_schedule.unload_notes',return_value=[])
    def test_voice_owns_resource_and_summary_resumes_after_release(self,unload):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            with voice_session(home):
                with self.assertRaises(SummaryDeferred):
                    with summary_session(home):self.fail('summary ran during voice')
            with summary_session(home):pass
        unload.assert_called_once()

    @patch('app.gpu_schedule.voice_pending',return_value=True)
    def test_queued_voice_defers_summary_without_generating(self,pending):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SummaryDeferred):
                with summary_session(Path(folder)):self.fail('summary ran')

    @patch('app.local_notes.request')
    def test_unload_only_known_local_qwen_and_verify_return(self,request):
        request.side_effect=[{'models':[{'name':'qwen3.5:4b','size_vram':10},{'name':'other-model'}]},
                             {'done':True},{'models':[{'name':'other-model'}]},ConnectionRefusedError()]
        self.assertEqual(unload_notes(),[{'port':11434,'model':'qwen3.5:4b','unloaded':True}])
        self.assertEqual(request.call_args_list[1].args[1],{'model':'qwen3.5:4b','keep_alive':0})

    @patch('app.local_notes.request')
    def test_failed_unload_stops_voice_before_it_uses_gpu(self,request):
        request.side_effect=[{'models':[{'name':'qwen3.5:4b','size_vram':10}]},{'done':True},
                             {'models':[{'name':'qwen3.5:4b','size_vram':10}]}]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                with voice_session(Path(folder)):self.fail('voice ran despite unload failure')
        # Exception released both OS locks.
        with tempfile.TemporaryDirectory() as folder:
            with patch('app.gpu_schedule.unload_notes',return_value=[]):
                with voice_session(Path(folder)):pass

    @patch('app.gpu_schedule.unload_notes',return_value=[])
    def test_resource_lock_is_respected_by_another_process(self,unload):
        import subprocess,sys
        script="from pathlib import Path;import sys;from app.gpu_schedule import try_lock;h=try_lock(Path(sys.argv[1]));print('free' if h else 'busy');h and h.close()"
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);command=[sys.executable,'-B','-c',script,str(home/'gpu-resource.lock')]
            with voice_session(home):
                self.assertEqual(subprocess.check_output(command,text=True).strip(),'busy')
            self.assertEqual(subprocess.check_output(command,text=True).strip(),'free')
