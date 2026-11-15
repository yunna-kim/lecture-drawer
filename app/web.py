from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file, session

from . import __version__
from .catalog import safe_audio, approved_source
from .config import ROOT, load_settings
from .profiles import activate
from .requests import cancel, enqueue, retry
from .service import Service
from .store import Store
from .verification import Verification
from .presentation import selection as presentation_selection, filtered_status
from .obsidian import note_uri
from .revise import revise_completed
from .note_templates import catalog as template_catalog, read_template


def create_app(home=None):
    settings = load_settings(home)
    home = Path(settings["state_home"])
    store = Store(home)
    service = Service(store)
    verification = Verification(store)
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    key = home / "session.key"
    if not key.exists():
        key.write_bytes(secrets.token_bytes(32))
    app.secret_key = key.read_bytes()
    app.config.update(MAX_CONTENT_LENGTH=128 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_NAME="lecture_drawer")
    app.extensions.update(store=store, service=service)
    mutation_lock = threading.Lock()

    @app.before_request
    def boundary():
        host = request.host.split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"} or request.remote_addr not in {"127.0.0.1", "::1"}:
            abort(403, "첫 버전은 이 PC에서만 열 수 있습니다.")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf", "!")):
                abort(403)

    @app.before_request
    def presentation_boundary():
        config=presentation_selection(store)
        if not config:return
        if not request.path.startswith('/api/'):
            if request.path=='/guide':abort(403)
            return
        if request.path in ('/api/status','/api/presentation/exit','/api/team-preview'):return
        allowed=set(config.get('visible_job_ids',[]))
        parts=request.path.strip('/').split('/')
        if len(parts)>=3 and parts[:2]==['api','jobs'] and parts[2] in allowed:
            if request.method=='GET' or (len(parts)==4 and parts[3] in ('open','cancel','retry')):return
        if len(parts)==3 and parts[:2]==['api','audio']:
            if parts[2] in set(config.get('visible_asset_ids',[])):return
            for identity in allowed:
                job=store.job(identity)
                if job and job['asset_id']==parts[2]:return
        abort(403,'시연 화면에서는 선택한 기록만 표시합니다.')

    @app.post('/api/presentation/exit')
    def presentation_exit():
        store.meta('presentation',{'enabled':False})
        return jsonify(ok=True)

    @app.after_request
    def privacy(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.errorhandler(ValueError)
    def value_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(OSError)
    def file_error(error):
        return jsonify(error="파일에 접근할 수 없습니다. 녹음 폴더 연결·파일 권한·저장 폴더를 확인해 주세요."), 400

    @app.get("/")
    def index():
        session.setdefault("csrf", secrets.token_hex(24))
        return render_template("index.html", csrf=session["csrf"], version=__version__)

    @app.get("/favicon.ico")
    def favicon():
        return send_file(ROOT / "assets" / "lecture-drawer-flat.ico", mimetype="image/x-icon")

    @app.get('/api/team-preview')
    def team_preview():
        from .workspaces import preview_status
        value=preview_status(home)
        presentation=presentation_selection(store)
        if presentation:
            allowed=set(presentation.get('visible_job_ids',[]))
            value['reports']=[r for r in value['reports'] if r.get('source_job') in allowed]
            aliases=presentation.get('display_aliases',{})
            for report in value['reports']:
                if report['source_job'] in aliases:report['title']=aliases[report['source_job']]
            value['paths']={'state':'로컬 시험 공간 · 경로 숨김','input':'시험 입력 폴더 · 경로 숨김','output':'시험 출력 폴더 · 경로 숨김'}
        value['verification']=verification.status()
        reviews=store.meta('verification_reviews') or []
        for report in value['reports']:
            report['review']=next((r for r in reversed(reviews) if r['report_id']==report['report_id'] and r['report_sha']==report['report_sha']),None)
        return jsonify(value)

    @app.post('/api/verification/run')
    def verification_run():
        with mutation_lock:
            return jsonify(verification.start(request.get_json() or {}))

    @app.post('/api/verification/review')
    def verification_review():
        with mutation_lock:
            return jsonify(verification.review(request.get_json() or {}))

    @app.get("/api/status")
    def status():
        from .note_api import credit_status, route_status
        cfg = load_settings(home)
        route = route_status(cfg, home)
        profiles = [{k: p[k] for k in ("id", "label", "engine", "device", "tested")}
                    for p in cfg["profiles"].values()]
        return jsonify(filtered_status(store,dict(version=__version__, catalog=store.meta("catalog") or {},
                       assets=store.assets(), jobs=store.jobs(), profiles=profiles,
                       templates=template_catalog(), note_ai_provider=cfg.get("note_ai", {}).get("provider", "disabled"), note_ai_default=cfg.get("note_ai", {}).get("id"), note_ai_route=route, api_credit=credit_status(cfg, home) if route["kind"] == "api" else None,
                       note_ai_profiles=[{k: p.get(k) for k in ("id", "label", "available")} for p in cfg.get("note_ai_profiles", {}).values()],
                       settings={k: cfg[k] for k in ("nas_root", "wiki_root", "active_profile", "previous_profile")})))

    @app.get("/api/templates/<identity>")
    def template(identity):
        return jsonify(read_template(identity))

    @app.post("/api/scan")
    def refresh():
        threading.Thread(target=service.scan, daemon=True).start()
        return jsonify(started=True)

    @app.post('/api/assets/<identity>/plan')
    def asset_plan(identity):
        body=request.get_json() or {}
        with mutation_lock:
            store.set_candidate(identity,body.get('transcribe'),body.get('size'),body.get('mtime_ns'))
        return jsonify(ok=True)

    @app.post("/api/jobs")
    def jobs():
        body = request.get_json() or {}
        with mutation_lock:
            return jsonify(jobs=enqueue(store, load_settings(home), body.get("selections"), body.get("profile_id")))

    @app.post("/api/jobs/<identity>/<action>")
    def job_action(identity, action):
        if action == "cancel":
            cancel(store, identity)
        elif action == "retry":
            with mutation_lock:
                retry(store, identity)
        elif action == "open":
            job = store.job(identity)
            if not job or job["status"] != "completed" or not job["output_path"]:
                raise ValueError("아직 저장된 전사 노트가 없습니다.")
            path = Path(job["output_path"]).resolve(strict=True)
            if path.suffix.lower() != ".md" or not path.is_relative_to(Path(job["params"]["wiki_root"]).resolve(strict=True)):
                abort(403)
            os.startfile(note_uri(path))
        else:
            abort(404)
        return jsonify(ok=True)

    @app.get("/api/jobs/<identity>/result")
    def result(identity):
        return jsonify(service.result(identity))

    @app.get("/api/jobs/<identity>/titles")
    def titles(identity):
        job=store.job(identity)
        if not job:
            raise ValueError("작업을 찾을 수 없습니다.")
        saved=home/'work'/identity/'note-result.json'
        note=json.loads(saved.read_text('utf-8')) if saved.is_file() else {}
        return jsonify(display_title=job.get('display_title') or note.get('title') or job['params']['title'],
                       file_title=note.get('title') or job['params']['title'],
                       file_editable=job['status']=='completed')

    @app.post("/api/jobs/<identity>/titles")
    def change_titles(identity):
        body=request.get_json() or {}
        display=body.get('display_title')
        file_title=body.get('file_title')
        if display is not None and (not isinstance(display,str) or len(display.strip())>180):
            raise ValueError("작업 제목은 180자 이내로 입력하세요.")
        if file_title is not None and (not isinstance(file_title,str) or not 1<=len(file_title.strip())<=100):
            raise ValueError("파일에 쓸 강의 제목은 1~100자로 입력하세요.")
        with mutation_lock:
            job=store.job(identity)
            if not job:
                raise ValueError("작업을 찾을 수 없습니다.")
            if file_title is not None:
                saved=home/'work'/identity/'note-result.json'
                previous=json.loads(saved.read_text('utf-8')).get('title') if saved.is_file() else None
                if file_title.strip()!=previous:
                    if job['status']!='completed':
                        raise ValueError("파일 제목은 전사·저장 완료 후 수정할 수 있습니다. 작업 제목은 지금 바꿀 수 있습니다.")
                    revise_completed(store,identity,file_title.strip())
                    service.scan()
            if display is not None:
                store.change_job(identity,display_title=display.strip() or None)
        return jsonify(ok=True)

    @app.get("/api/audio/<identity>")
    def audio(identity):
        asset = store.asset(identity)
        job_id = request.args.get("job")
        if job_id:
            job = store.job(job_id)
            if not job or job["asset_id"] != identity:
                abort(404)
            path = approved_source(job, {"nas_root": job["params"]["nas_root"]})
        else:
            path = safe_audio(Path(load_settings(home)["nas_root"]), asset["relative_path"])
        return send_file(path, conditional=True, etag=False)

    @app.post("/api/profiles/activate")
    def switch_profile():
        data = request.get_json() or {}
        with mutation_lock:
            target = activate(home, store, data.get("profile_id"), data.get("reviewed_job"), data.get("rollback", False))
        return jsonify(active_profile=target)

    @app.get("/guide")
    def guide():
        return send_file(ROOT / "README.md", mimetype="text/plain; charset=utf-8")

    return app
