from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import webbrowser

from app import __version__
from app.config import load_settings, save_settings, state_home
from app.profiles import register_whisper, runtime_versions
from app.instance import acquire


def doctor(home):
    cfg = load_settings(home)
    root = Path(cfg["nas_root"])
    # Read-only diagnostics; no private filenames or file contents in output.
    print(json.dumps({"app": __version__, "python": sys.executable,
                      "versions": runtime_versions(), "ffmpeg": bool(shutil.which("ffmpeg")),
                      "ffprobe": bool(shutil.which("ffprobe")), "nas_available": root.is_dir(),
                      "wiki_available": Path(cfg["wiki_root"]).is_dir(),
                      "profiles": [{"id": v["id"], "label": v["label"]} for v in cfg["profiles"].values()]},
                     ensure_ascii=False, indent=2))


def serve(home, open_browser):
    from werkzeug.serving import make_server
    from app.web import create_app
    cfg = load_settings(home)
    lock = acquire(home)
    if lock is None:
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{cfg['port']}")
        print("이미 실행 중입니다. 기존 화면을 열었습니다.")
        return
    app = create_app(home)
    server = make_server("127.0.0.1", cfg["port"], app, threaded=True)
    service = app.extensions["service"]
    service.start()
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{cfg['port']}")
    print(f"강의 서랍 v{__version__} · http://127.0.0.1:{cfg['port']}", flush=True)
    try:
        server.serve_forever()
    finally:
        service.close()
        server.server_close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="PC·NAS 폴더에서 선택한 녹음만 로컬 전사")
    parser.add_argument("--state", type=Path, default=state_home())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("setup")
    model_setup = sub.add_parser("prepare-model")
    model_setup.add_argument("--device", choices=["cpu","cuda"], default="cpu")
    paths = sub.add_parser("configure")
    paths.add_argument("--source", type=Path, required=True)
    paths.add_argument("--output", type=Path, required=True)
    paths.add_argument("--port", type=int, default=8767)
    rename = sub.add_parser("source-rename")
    rename.add_argument("--enable", action="store_true")
    rename.add_argument("--sync-ready", action="store_true")
    openai_route = sub.add_parser("register-openai-api")
    openai_route.add_argument("--model", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--open", action="store_true")
    register = sub.add_parser("register-whisper")
    register.add_argument("model_dir", type=Path)
    register.add_argument("--label", required=True)
    register.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    register.add_argument("--compute-type", choices=["float16","int8_float16","int8"])
    sub.add_parser("check-updates")
    team = sub.add_parser('prepare-team')
    team.add_argument('--home', type=Path)
    local = sub.add_parser("register-local-notes")
    local.add_argument("--model", default="qwen3.5:4b")
    local.add_argument('--port',type=int,choices=[11434,11435],default=11434)
    register_api = sub.add_parser("register-claude-api")
    register_api.add_argument("--own-key", action="store_true")
    register_api.add_argument("--model", default="claude-opus-5-5")
    route = sub.add_parser("use-note-ai")
    route.add_argument("profile_id")
    args = parser.parse_args()
    cfg = load_settings(args.state)
    if args.command == "setup":
        from app.setup import wizard
        wizard(args.state)
    elif args.command == "configure":
        from app.setup import configure
        configure(args.state, args.source, args.output, args.port)
        print("폴더 설정 완료. 다음 실행부터 적용됩니다.")
    elif args.command == "prepare-model":
        from app.setup import prepare_model
        print(prepare_model(args.state, args.device))
    elif args.command == "source-rename":
        if args.enable and not args.sync_ready:
            raise ValueError("FolderSync 재동기화 옵션 확인 후 --sync-ready를 함께 입력하세요.")
        cfg.update(rename_selected_audio=args.enable, sync_rename_ready=args.enable and args.sync_ready)
        save_settings(args.state,cfg)
        print("원음 이름 변경: " + ("켬" if args.enable else "끔"))
    elif args.command == "doctor":
        doctor(args.state)
    elif args.command == "serve":
        serve(args.state, args.open)
    elif args.command == "register-whisper":
        print(register_whisper(args.state, args.model_dir, args.label, args.device, args.compute_type))
    elif args.command == "register-local-notes":
        from app.local_notes import check_model
        fingerprint = check_model(args.model,port=args.port)
        cfg.setdefault("note_ai_profiles", {})["qwen-local"] = {
            "id": "qwen-local", "label": "Qwen3.5 4B · PC 로컬", "available": True,
            "provider": "ollama", "model": args.model, "model_digest": fingerprint,'port':args.port}
        save_settings(args.state, cfg)
        print("로컬 정리 모델 등록 완료. 요청 창에서 선택할 수 있습니다.")
    elif args.command in ("register-claude-api", "register-openai-api"):
        from getpass import getpass
        from app.note_api import register, store_key
        provider = "openai-api" if args.command == "register-openai-api" else "claude-api"
        register(cfg, args.model, provider)
        store_key(args.state, getpass("API 키를 붙여 넣고 Enter(입력이 보이지 않습니다): "), provider)
        save_settings(args.state, cfg)
        print("등록 완료. 새 요청의 기본값으로 쓰려면 use-note-ai " + provider + " 를 실행하세요.")
    elif args.command == "use-note-ai":
        from app.note_api import use
        use(cfg, args.profile_id)
        save_settings(args.state, cfg)
        print("새 요청의 기본 정리 AI: " + cfg["note_ai"]["label"])
    elif args.command == 'prepare-team':
        from app.workspaces import prepare_team
        team_cfg = prepare_team(args.home)
        print(json.dumps({'state_home': team_cfg['state_home'],
                          'shared_processing_enabled': team_cfg['shared_processing_enabled'],
                          'isolation_verified': team_cfg['isolation_verified']}, ensure_ascii=False))
    elif args.command == "check-updates":
        # No configured release publisher yet: don't execute arbitrary remote updates.
        print(json.dumps({"app": __version__, "automatic_updates": False,
                          "message": "배포 서버는 아직 없습니다. 새 코드는 로컬 변경 이력으로 검토·적용하고, 모델은 별도 등록·시험 후 적용합니다.",
                          "official_sources": ["https://github.com/SYSTRAN/faster-whisper/releases",
                                               "https://github.com/QwenLM/Qwen3-ASR/releases",
                                               "https://github.com/microsoft/VibeASR.cpp/releases"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if sys.stderr is not None:
            raise
        import traceback
        error_log = state_home() / "startup-error.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                "강의 서랍을 열지 못했습니다.\n오류 기록: " + str(error_log), "강의 서랍 실행 오류", 0x10)
        sys.exit(1)
