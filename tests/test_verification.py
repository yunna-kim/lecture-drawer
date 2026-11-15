import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.config import atomic_json
from app.store import Store
from app.verification import Verification


class VerificationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)/'personal')
        self.verification = Verification(self.store)
        self.path = self.store.home.with_name('LectureDrawer-TeamPreview')/'benchmarks'/'bench-fixture-v3.json'
        atomic_json(self.path, {'status': 'completed', 'quality_issues': [], 'asr_unchanged': True})
        self.body = {'report_id': self.path.name, 'report_sha': hashlib.sha256(self.path.read_bytes()).hexdigest(),
                     'verdict': 'needs-fix', 'note': '인물 혼동 확인'}

    def test_review_is_version_bound_and_preserves_previous_decisions(self):
        self.verification.review(self.body)
        self.verification.review({**self.body, 'verdict': 'passed', 'note': '원문 대조 완료'})
        history = self.store.meta('verification_reviews')
        self.assertEqual([r['verdict'] for r in history], ['needs-fix', 'passed'])
        self.assertFalse(self.verification.review({**self.body, 'note': '재확인'})['model_adopted'])
        atomic_json(self.path, {'status': 'failed'})
        with self.assertRaisesRegex(ValueError, '바뀌었습니다'):
            self.verification.review(self.body)

    def test_failed_structure_cannot_be_approved(self):
        atomic_json(self.path, {'status': 'completed', 'quality_issues': ['누락'], 'asr_unchanged': True})
        body = {**self.body, 'report_sha': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'verdict': 'passed'}
        with self.assertRaises(ValueError): self.verification.review(body)
        self.assertIsNone(self.store.meta('verification_reviews'))

    def test_review_rejects_traversal_and_empty_reason(self):
        for change in ({'report_id': '../private.json'}, {'note': ''}, {'verdict': 'auto-pass'}):
            with self.assertRaises(ValueError): self.verification.review({**self.body, **change})

    def test_interrupted_run_does_not_restart_itself(self):
        self.store.meta('verification', {'status': 'running', 'job_id': 'fixture'})
        second = Verification(self.store)
        self.assertEqual(second.status()['status'], 'interrupted')
        self.assertFalse(second.running)

    @patch('app.local_notes.check_model', return_value='fixture')
    @patch('app.verification.threading.Thread')
    def test_only_selected_completed_jobs_and_finite_ranges_start(self, thread, model):
        with patch.object(self.store, 'job', return_value={'id': 'selected', 'status': 'completed'}):
            for change in ({'start': float('nan')}, {'seconds': 0}, {'seconds': float('inf')}, {'start': -1}):
                with self.assertRaises(ValueError): self.verification.start({'job_id': 'selected', **change})
            self.verification.start({'job_id': 'selected', 'start': 1200, 'seconds': 300})
            self.assertEqual(self.verification.status()['status'], 'running')
            with self.assertRaises(ValueError): self.verification.start({'job_id': 'selected'})
        self.assertEqual(thread.call_count, 1)
        thread.return_value.start.assert_called_once()

    def test_unselected_job_and_busy_queue_are_blocked(self):
        with self.assertRaises(ValueError): self.verification.start({'job_id': 'missing'})
        with patch.object(self.store, 'busy', return_value=True):
            with self.assertRaisesRegex(ValueError, '진행 중'): self.verification.start({})

    @patch('tools.benchmark_local_notes.run_benchmark', side_effect=RuntimeError('시험 오류'))
    def test_failure_is_recorded_without_retry(self, run):
        self.verification.running = True
        self.verification._run(Mock())
        self.assertEqual(self.verification.status()['status'], 'failed')
        self.assertFalse(self.verification.running)
        run.assert_called_once()

    @patch('app.local_notes.check_model', return_value='fixture')
    @patch('app.verification.threading.Thread')
    def test_whole_lecture_always_starts_at_zero(self, thread, model):
        with patch.object(self.store, 'job', return_value={'id': 'selected', 'status': 'completed'}):
            self.verification.start({'job_id': 'selected', 'start': 1800, 'whole': True})
        args=thread.call_args.kwargs['args'][0]
        self.assertEqual(args.start,0)
        self.assertIsNone(args.seconds)

    @patch('app.local_notes.check_model', side_effect=ConnectionRefusedError('fixture'))
    def test_offline_server_explains_local_fix_without_starting(self, model):
        with patch.object(self.store, 'job', return_value={'id': 'selected', 'status': 'completed'}):
            with self.assertRaisesRegex(ValueError, 'start_local_notes.ps1'):
                self.verification.start({'job_id':'selected'})
        self.assertFalse(self.verification.running)
