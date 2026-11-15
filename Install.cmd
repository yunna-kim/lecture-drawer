@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.12이 필요합니다. README의 설치 단계를 확인하세요.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements-ai.txt
if errorlevel 1 goto fail
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo FFmpeg가 없습니다. README의 FFmpeg 설치 후 이 파일을 다시 실행하세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 cli.py setup
if errorlevel 1 goto fail
echo 전사 모델을 처음 한 번 내려받습니다. 준비되면 아무 키나 누르세요. 취소: Ctrl+C
pause >nul
".venv\Scripts\python.exe" -X utf8 cli.py prepare-model --device cpu
if errorlevel 1 goto fail
echo 설치 완료. 이제 Start.cmd를 실행하세요.
pause
exit /b 0
:fail
echo 설치를 마치지 못했습니다. 바로 위 오류와 README의 해결 방법을 확인하세요.
pause
exit /b 1
