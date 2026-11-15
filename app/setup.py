"""First-run configuration without editing JSON."""
from pathlib import Path
import sys
from .config import load_settings, save_settings
from .instance import acquire


def configure(home, source, output, port=8767):
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    lock = acquire(home)
    if lock is None:
        raise ValueError('강의서랍이 실행 중입니다. 실행 창에서 Ctrl+C로 종료한 뒤 설정하세요.')
    try:
        source = Path(source).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ValueError('녹음 폴더를 찾을 수 없습니다. 파일 탐색기에서 먼저 확인하세요.')
        output = Path(output).expanduser().resolve()
        if source == output or source in output.parents or output in source.parents:
            raise ValueError('녹음과 노트는 서로 포함하지 않는 별도 폴더로 골라주세요.')
        if not 1024 <= int(port) <= 65535:
            raise ValueError('접속 번호는 1024~65535 사이 숫자로 정하세요.')
        cfg = load_settings(home)
        from .store import Store
        if Store(home).busy():
            raise ValueError('대기 중인 전사가 있습니다. 작업 완료 후 폴더를 바꿔주세요.')
        output.mkdir(parents=True, exist_ok=True)
        cfg.update(nas_root=str(source), wiki_root=str(output), runtime_python=sys.executable,
                   port=int(port), source_type='local-folder', rename_selected_audio=False,
                   sync_rename_ready=False)
        save_settings(home, cfg)
        return cfg
    finally:
        lock.close()


def wizard(home):
    cfg = load_settings(home)
    print('\n강의서랍 첫 설정 · 입력 내용은 이 PC에만 저장됩니다.')
    print('1. PC 녹음 폴더 또는 Windows에서 열리는 NAS 공유 폴더를 고르세요.')
    source = input('녹음 폴더 주소: ').strip().strip('"')
    print('2. Obsidian 보관함 안의 노트 저장 폴더를 고르세요.')
    output = input('노트 폴더 주소: ').strip().strip('"')
    if not source or not output:
        raise ValueError('두 폴더 주소를 모두 입력하세요. 기존 설정은 바꾸지 않았습니다.')
    return configure(home, source, output, cfg['port'])


def prepare_model(home, device='cpu'):
    from huggingface_hub import snapshot_download
    from .profiles import register_whisper
    target = Path(home)/'models'/'whisper-turbo'
    print('전사 모델을 처음 한 번 내려받습니다. 네트워크와 저장 공간이 필요합니다.')
    snapshot_download('dropbox-dash/faster-whisper-large-v3-turbo', local_dir=str(target),
                     allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.json'])
    return register_whisper(Path(home), target, 'Whisper turbo · '+device.upper(), device,
                           'int8' if device == 'cpu' else 'int8_float16')
