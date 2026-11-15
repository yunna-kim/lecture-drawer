"""Optional local Ollama transport. Never downloads models or calls cloud endpoints."""
import http.client
import json


def request(path, payload=None, timeout=10, port=11434):
    if type(port) is not int or port not in (11434,11435):
        raise ValueError('등록된 로컬 시험 포트만 사용할 수 있습니다.')
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request("POST" if payload is not None else "GET", path, body,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(32 * 1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 32 * 1024 * 1024:
            raise ValueError("로컬 Ollama 응답을 확인할 수 없습니다. 모델 설치·서버 상태를 확인하세요.")
        return json.loads(raw)
    finally:
        connection.close()


def check_model(model, port=11434):
    if not isinstance(model, str) or model != "qwen3.5:4b":
        raise ValueError("검토한 로컬 Qwen3.5 4B 모델만 등록할 수 있습니다.")
    models = request("/api/tags", port=port).get("models", [])
    match = next((row for row in models if row.get("name") == model or row.get("model") == model), None)
    if not match or not match.get("digest"):
        raise ValueError("Qwen 모델이 설치되지 않았습니다. 모델은 자동 다운로드하지 않습니다.")
    details = request("/api/show", {"model": model}, port=port)
    if details.get("remote_host") or details.get("remote_model") or match.get("remote_host"):
        raise ValueError("클라우드 모델은 이 로컬 연결에서 사용하지 않습니다.")
    return match["digest"]


def generate(prompt, profile, *, max_output=3072, keep_alive=0, schema=None):
    model = profile.get("model")
    port=profile.get('port',11434)
    fingerprint = check_model(model,port=port)
    if fingerprint != profile.get("model_digest"):
        raise ValueError("로컬 모델 버전이 바뀌었습니다. 확인 후 다시 등록하세요.")
    needed=len(prompt.encode('utf-8'))+max_output+1024
    context=next((size for size in (4096,8192,12288,16384) if needed<=size),16384)
    # A byte upper bound avoids silently truncating Korean input to the context window.
    if len(prompt.encode("utf-8")) + max_output + 1024 > context:
        raise ValueError("현재 로컬 모델의 안전 입력 길이를 넘었습니다. 원문은 보존했으며 구간별 정리가 필요합니다.")
    from .gpu_schedule import summary_session
    with summary_session():
        result = request("/api/generate", {"model": model, "prompt": prompt, "stream": False,
                         "format": schema if schema is not None else "json", "think": False, "keep_alive": keep_alive,
                         "options": {"num_ctx": context, "num_predict": max_output, "temperature": 0,
                                     "num_thread":4,"num_batch":128}}, timeout=600,port=port)
    if not result.get("done") or result.get("done_reason") != "stop" or not result.get("response"):
        raise ValueError("로컬 AI 응답이 끝까지 생성되지 않았습니다. 원문을 보존했습니다.")
    return {"answer": result["response"], "generation": {"provider": "ollama-local", "models": [model],
             "model_digest": fingerprint, "prompt_eval_count": result.get("prompt_eval_count"),
             "eval_count": result.get("eval_count"),'context':context,
             'total_duration_ns':result.get('total_duration'),'load_duration_ns':result.get('load_duration'),
             'eval_duration_ns':result.get('eval_duration')}}
