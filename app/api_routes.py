"""Explicit API routes without shared credentials or automatic retries."""
import json
import os
from pathlib import Path
import time

MODEL = 'claude-opus-5-5'
PROFILE_ID = 'claude-api'


class ApiWaiting(Exception):
    pass


def key_path(home, provider='claude-api'):
    return Path(home)/('openai-api-key.txt' if provider == 'openai-api' else 'anthropic-api-key.txt')


def shared_key():
    return ''


def store_key(home, key, provider='claude-api'):
    key = key.strip()
    if not key.startswith('sk-') or any(c.isspace() for c in key):
        raise ValueError('API 키 형식을 확인하세요. 키는 채팅이나 문서에 붙이지 마세요.')
    path = key_path(home, provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, 'utf-8')
    os.chmod(path, 0o600)


def read_key(home, provider='claude-api'):
    path = key_path(home, provider)
    if not path.is_file():
        raise ValueError('이 PC의 강의서랍 전용 API 키를 먼저 등록하세요.')
    return path.read_text('utf-8').strip()


def register(settings, model=MODEL, provider='claude-api'):
    if not model or len(model) > 150 or any(c.isspace() for c in model):
        raise ValueError('본인 계정에서 사용할 수 있는 API 모델 ID를 입력하세요.')
    settings.setdefault('note_ai_profiles', {})[provider] = {
        'id':provider,'label':'OpenAI API' if provider == 'openai-api' else 'Claude API',
        'provider':provider,'available':True,'model':model}


def use(settings, identity):
    profile = settings.get('note_ai_profiles', {}).get(identity)
    if not profile or not profile.get('available'):
        raise ValueError('먼저 API 키 또는 로컬 정리 모델을 등록하세요.')
    settings['note_ai'] = dict(profile)


def route_status(settings, home):
    profile = settings.get('note_ai', {})
    provider = profile.get('provider', 'disabled')
    if provider in ('claude-api','openai-api'):
        return {'kind':'api','text':profile.get('label',provider)+' · 전용 키',
                'ready':key_path(home,provider).is_file()}
    if provider == 'ollama':
        return {'kind':'local','text':'PC 로컬','ready':True}
    return {'kind':'none','text':'AI 미설정 · 전사 원문 저장 가능','ready':False}


def credit_status(settings, home):
    # Account grants, renewal dates and actual balance are only in the Console.
    return None


def generate(prompt, profile, home):
    provider = profile['provider']
    key = read_key(home, provider)
    try:
        if provider == 'openai-api':
            import openai
            client = openai.OpenAI(api_key=key, max_retries=0, timeout=600)
            response = client.responses.create(model=profile['model'], input=prompt,
                                              max_output_tokens=16000, store=False)
            if response.status != 'completed':
                raise ValueError('AI 응답이 끝까지 생성되지 않았습니다. 원문을 보존했습니다.')
            answer, model = response.output_text, response.model
            usage = response.usage.model_dump() if response.usage else {}
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=600)
            with client.messages.stream(model=profile['model'], max_tokens=16000,
                    messages=[{'role':'user','content':prompt}]) as stream:
                response = stream.get_final_message()
            if response.stop_reason != 'end_turn':
                raise ValueError('AI 응답이 끝까지 생성되지 않았습니다. 원문을 보존했습니다.')
            answer = ''.join(b.text for b in response.content if b.type == 'text')
            model = response.model
            usage = response.usage.model_dump()
    except ImportError as error:
        raise ValueError('AI 라이브러리가 없습니다. requirements-ai.txt 설치 단계를 실행하세요.') from error
    except ValueError:
        raise
    except Exception as error:
        raise ApiWaiting('정리 AI 연결·크레딧·한도·모델 권한을 확인하세요. 전사는 보존했습니다. 같은 작업 재시도는 API 사용량을 다시 쓸 수 있습니다.') from error
    with (Path(home)/'api-usage.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'time':time.time(),'provider':provider,'model':model,'usage':usage})+'\n')
    return {'answer':answer,'generation':{'provider':provider,'models':[model],'usage':usage}}
