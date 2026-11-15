import argparse
import json
from pathlib import Path
import sys
from .catalog import approved_source
from .config import atomic_json
from .note_ai import compose
from .store import Store


def run(identity, home):
    store = Store(home)
    job = store.job(identity)
    if not job or job["status"] != "saving" or job["cancel_requested"]:
        raise ValueError("정리할 수 있는 선택 요청이 없습니다.")
    approved_source(job, {"nas_root": job["params"]["nas_root"]})
    work = home / "work" / identity
    atomic_json(work / "progress.json", {"stage": "composing", "fraction": 1,
                                         "message": "템플릿 정리·제목을 준비하고 있습니다. 원문은 보존됩니다."})
    result = json.loads((work / "result.json").read_text("utf-8"))
    if result["source_sha"] != job["source_sha"] or result["dedupe_key"] != job["dedupe_key"]:
        raise ValueError("전사 원문이 선택 요청과 다릅니다.")
    from .presentation import fixed_title_job
    effective,title=fixed_title_job(store,job)
    value = compose(store, effective, result["segments"])
    if title:
        value.update(title=title,title_source='user',title_context_override=title)
        atomic_json(work/'title-override.json',{'title':title,'reason':'explicit-user-fixed-title'})
    approved_source(job, {"nas_root": job["params"]["nas_root"]})
    atomic_json(work / "note-result.json", {"dedupe_key": job["dedupe_key"], **value})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.job, args.state)
    except Exception as error:
        message = str(error)
        waiting = False
        if "anna_ai_budget" in sys.modules:
            from anna_ai_budget import AIBudgetUnavailable, wait_state
            if isinstance(error, AIBudgetUnavailable):
                message = wait_state(error)["message"]
                waiting = True
        if type(error).__name__ == "ApiWaiting":
            waiting = True
        if type(error).__name__ == "RunnerBusy":
            message = "기존 AI 작업이 진행 중입니다. 원문을 보존하고 정리를 기다립니다."
            waiting = True
        atomic_json(args.state / "work" / args.job / "failure.json",
                    {"type": type(error).__name__, "message": message[:1500], "waiting_ai": waiting, 'defer_summary':type(error).__name__=='SummaryDeferred'})
        sys.exit(1)
