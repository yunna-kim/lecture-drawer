"""Explicit, read-only ASR reuse for the disabled local-notes workbench."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import re
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import state_home, atomic_json
from app.store import Store
from app.workspaces import prepare_team
from app.local_notes import check_model
from app.local_pipeline import compose_hierarchical
from app.gpu_schedule import SummaryDeferred


def run_benchmark(args, personal_home=None):
    original=Store(personal_home or state_home())
    if original.busy():raise SummaryDeferred('음성 작업 우선 · 전사가 끝난 뒤 로컬 검증을 이어갑니다.')
    job=original.job(args.job)
    if not job or job['status']!='completed':raise ValueError('완료한 명시적 선택 작업만 시험할 수 있습니다.')
    source=original.home/'work'/job['id']/'result.json'
    result=json.loads(source.read_text('utf-8'))
    if result['source_sha']!=job['source_sha'] or result['dedupe_key']!=job['dedupe_key']:
        raise ValueError('보존된 전사와 선택 원장이 다릅니다.')
    if args.start<0 or (args.seconds is not None and args.seconds<=0):raise ValueError('올바른 시험 구간이 필요합니다.')
    end=args.start+args.seconds if args.seconds else result['duration']
    segments=[s for s in result['segments'] if s['start']>=args.start and s['start']<end]
    cfg=prepare_team(original.home.with_name('LectureDrawer-TeamPreview'))
    profile={'provider':'ollama','model':'qwen3.5:4b','model_digest':check_model('qwen3.5:4b',port=11435),'port':11435}
    identity=f"bench-{job['id']}-{args.start:g}-{end:g}"
    params={k:job['params'][k] for k in ('title','title_context','hints','auto_title','note_template','original_filename')
            if k in job['params'] and job['params'][k] is not None}
    correction=original.home/'work'/job['id']/'note-result.json'
    context_basis='original-request'
    if correction.exists():
        corrected=json.loads(correction.read_text('utf-8')).get('title_context_override')
        if corrected:
            params['title']=corrected
            course=re.match(r'^(.*?\d+교시)',corrected)
            params['title_context']=course[1] if course else corrected
            context_basis='saved-user-correction'
    params['note_ai']=profile
    experiment={'id':identity,'relative_path':job['relative_path'],'params':params}
    report_file=Path(cfg['state_home'])/'benchmarks'/(identity+'-v3.json')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    started=time.time()
    try:
        answer=compose_hierarchical(SimpleNamespace(home=Path(cfg['state_home'])),experiment,segments)
        report={'status':'completed','source_job':job['id'],'start':args.start,'end':end,
                'whole_lecture':args.start==0 and end==result['duration'],
                'seconds':round(time.time()-started,2),'result':answer,
                'quality_issues':answer.get('quality_issues',[]),'quality_status':'needs-human-review'}
    except SummaryDeferred:
        raise
    except Exception as error:
        report={'status':'failed','source_job':job['id'],'start':args.start,'end':end,
                'seconds':round(time.time()-started,2),'error_type':type(error).__name__,'error':str(error)}
    report['asr_unchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==before
    report['context_basis']=context_basis
    report['shared_processing_enabled']=cfg['shared_processing_enabled']
    previous=json.loads(report_file.read_text('utf-8')) if report_file.exists() else {}
    report['runs']=previous.get('runs',[])+[{'seconds':report['seconds'],'status':report['status'],
        'at':time.time(),'cache_hits':report.get('result',{}).get('generation',{}).get('cache_hits',0),
        'new_calls':report.get('result',{}).get('generation',{}).get('new_calls')}]
    atomic_json(report_file,report)
    print(json.dumps({k:v for k,v in report.items() if k!='result'},ensure_ascii=False),flush=True)
    print('report',report_file,flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--start',type=float,default=0)
    parser.add_argument('--seconds',type=float)
    args=parser.parse_args()
    report=run_benchmark(args)
    if report['status']!='completed':raise SystemExit(1)


if __name__=='__main__':main()
