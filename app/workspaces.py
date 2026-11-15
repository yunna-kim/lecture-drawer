"""Prepare a separate, disabled team workspace; this is not OS isolation."""
from pathlib import Path
import sys
from .config import atomic_json, load_settings, state_home
import json
import hashlib


def team_allowed(settings):
    return settings.get('mode') != 'team-preview' or (
        settings.get('shared_processing_enabled') is True
        and settings.get('isolation_verified') is True)


def prepare_team(home=None):
    personal = state_home().resolve()
    home = Path(home or personal.with_name('LectureDrawer-TeamPreview')).resolve()
    if home == personal or personal in home.parents:
        raise ValueError('공동 시험 상태는 개인 앱 저장소와 분리해야 합니다.')
    settings_file = home / 'settings.json'
    if settings_file.exists():
        cfg = load_settings(home)
        if cfg.get('mode') != 'team-preview':
            raise ValueError('기존 개인 설정을 공동 설정으로 덮어쓰지 않습니다.')
        return cfg
    if home.exists() and any(home.iterdir()):
        raise ValueError('비어 있는 별도 공동 시험 폴더를 지정하세요.')
    source, output = home / 'input', home / 'output'
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    cfg = {'schema': 1, 'mode': 'team-preview', 'source_type': 'local-folder',
           'shared_processing_enabled': False, 'isolation_verified': False,
           'nas_root': str(source), 'wiki_root': str(output),
           'runtime_python': sys.executable, 'port': 8768,
           'scan_seconds': 30, 'stable_seconds': 15, 'chunk_seconds': 300,
           'profiles': {}, 'active_profile': None, 'previous_profile': None,
           'note_ai': {'provider': 'disabled'}, 'note_ai_profiles': {},
           'rename_selected_audio': False, 'sync_rename_ready': False}
    atomic_json(settings_file, cfg)
    return load_settings(home)


def require_team_allowed(settings):
    if not team_allowed(settings):
        raise ValueError('공동 자동 처리는 꺼져 있습니다. 별도 실행 계정의 실제 권한 검증과 운영 승인이 필요합니다.')


def preview_status(personal_home):
    """Read a fixed workbench, exposing no prompts, credentials or source files."""
    home=Path(personal_home).with_name('LectureDrawer-TeamPreview')
    cfg_file=home/'settings.json'
    cfg={}
    if cfg_file.is_file() and not cfg_file.is_symlink():
        cfg=json.loads(cfg_file.read_text('utf-8'))
    if cfg and cfg.get('mode')!='team-preview':raise ValueError('공동 시험 설정을 확인하세요.')
    reports=[]
    root=home/'benchmarks'
    if root.is_dir() and not root.is_symlink():
        for path in sorted(root.glob('bench-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:12]:
            if path.is_symlink() or path.stat().st_size>512*1024:continue
            try:
                raw=path.read_bytes()
                value=json.loads(raw)
                result=value.get('result') or {}
                reports.append({'report_id':path.name,'report_sha':hashlib.sha256(raw).hexdigest(),
                    'source_job':str(value.get('source_job',''))[:80],
                    'asr_unchanged':value.get('asr_unchanged',False),'status':value.get('status'),'whole_lecture':value.get('whole_lecture',False),
                    'start':value.get('start',0),'end':value.get('end',0),'seconds':value.get('seconds',0),
                    'pipeline':str(result.get('generation',{}).get('pipeline','hierarchical-v2'))[:40],
                    'cache_hits':result.get('generation',{}).get('cache_hits',0),
                    'new_calls':result.get('generation',{}).get('new_calls'),
                    'title':str(result.get('title','제목 생성 전'))[:180],
                    'summary':str(result.get('summary',''))[:12000],
                    'quality_issues':[str(x)[:300] for x in value.get('quality_issues',[])[:20]],
                    'error':str(value.get('error',''))[:600]})
            except (ValueError,TypeError,AttributeError):continue
    return {'prepared':bool(cfg),'processing_enabled':cfg.get('shared_processing_enabled',False),
            'isolation_verified':cfg.get('isolation_verified',False),
            'drive_connected':False,'model_adopted':False,'reports':reports,
            'paths':{'state':str(home),'input':str(home/'input'),'output':str(home/'output')}}
