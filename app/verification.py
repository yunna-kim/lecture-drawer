"""Explicit local validation, separate from publishing or adopting a model."""
import hashlib
import json
import math
import re
import threading
import time
from types import SimpleNamespace


class Verification:
    def __init__(self, store):
        self.store = store
        self.running = False
        previous = self.status()
        if previous.get('status') == 'running':
            self.store.meta('verification', {**previous, 'status': 'interrupted',
                'error': '프로그램이 중단되었습니다. 자동 재실행하지 않습니다.'})

    def status(self):
        return self.store.meta('verification') or {'status': 'idle'}

    def start(self, body):
        if self.running or self.store.busy():
            raise ValueError('전사 또는 검증이 진행 중입니다. 완료 후 실행하세요.')
        job = self.store.job(body.get('job_id', ''))
        if not job or job['status'] != 'completed':
            raise ValueError('완료한 강의를 직접 선택하세요.')
        try:
            start = float(body.get('start', 0))
            seconds = None if body.get('whole') is True else float(body.get('seconds', 300))
        except (TypeError, ValueError):
            raise ValueError('검증 시작·길이는 초 단위 숫자여야 합니다.')
        if body.get('whole') is True:start = 0.0
        if not math.isfinite(start) or start < 0 or (seconds is not None and (not math.isfinite(seconds) or not 0 < seconds <= 3600)):
            raise ValueError('올바른 검증 구간을 입력하세요. 부분 검증은 최대 1시간입니다.')
        # Fail before starting a background task if the manually started local runtime is absent.
        from .local_notes import check_model
        try:
            check_model('qwen3.5:4b', port=11435)
        except (OSError, ValueError) as error:
            raise ValueError('로컬 시험 서버11435 또는 Qwen 모델을 확인하세요. tools/start_local_notes.ps1을 실행한 뒤 다시 시도하세요. '+str(error)) from error
        args = SimpleNamespace(job=job['id'], start=start, seconds=seconds)
        self.running = True
        self.store.meta('verification', {'status': 'running', 'job_id': job['id'],
            'started_at': time.time(), 'message': '로컬 검증 중 · 기존 원문·노트 보존'})
        threading.Thread(target=self._run, args=(args,), daemon=True).start()
        return self.status()

    def _run(self, args):
        try:
            from tools.benchmark_local_notes import run_benchmark
            from .gpu_schedule import SummaryDeferred,voice_pending
            while True:
                try:
                    report = run_benchmark(args, self.store.home)
                    break
                except SummaryDeferred:
                    self.store.meta('verification',{'status':'waiting_voice','message':'음성 전사 우선 · 로컬 검증 대기'})
                    while self.store.busy():threading.Event().wait(.5)
                    threading.Event().wait(.5)
            self.store.meta('verification', {'status': report['status'], 'finished_at': time.time(),
                'message': '시험 완료 · 내용 검토 필요' if report['status'] == 'completed' else report.get('error', '검증 실패')})
        except Exception as error:
            self.store.meta('verification', {'status': 'failed', 'finished_at': time.time(), 'message': str(error)[:600]})
        finally:
            self.running = False

    def review(self, body):
        identity = body.get('report_id', '')
        if not isinstance(identity, str) or not re.fullmatch(r'bench-[a-zA-Z0-9_.-]+\.json', identity):
            raise ValueError('올바른 시험 결과를 선택하세요.')
        path = self.store.home.with_name('LectureDrawer-TeamPreview')/'benchmarks'/identity
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512*1024:
            raise ValueError('시험 결과 파일을 확인할 수 없습니다.')
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != body.get('report_sha'):
            raise ValueError('시험 결과가 바뀌었습니다. 새로고침 후 검토하세요.')
        result = json.loads(raw)
        verdict = body.get('verdict')
        note = body.get('note', '')
        if verdict not in ('passed', 'needs-fix') or not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
            raise ValueError('검토 판정과 근거 메모를 입력하세요.')
        if verdict == 'passed' and (result.get('status') != 'completed' or result.get('quality_issues') or not result.get('asr_unchanged')):
            raise ValueError('실행·구조·원문 보존 검사를 통과한 결과만 내용 검토 통과로 기록할 수 있습니다.')
        history = self.store.meta('verification_reviews') or []
        history.append({'report_id': identity, 'report_sha': digest, 'verdict': verdict,
                        'note': note.strip(), 'reviewer': 'human-local-user', 'at': time.time()})
        self.store.meta('verification_reviews', history)
        return {'saved': True, 'model_adopted': False}
