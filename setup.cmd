@echo off
rem セットアップ（Windows）: .venv を作って harness と fieldcheck、doc2md（harness convert 用）を入れる
rem   setup.cmd               すべて（[dev] と [convert]）
rem   setup.cmd --no-convert  Office の変換（harness convert）を使わないとき
cd /d "%~dp0"
set EXTRAS=dev,convert
if "%~1"=="--no-convert" set EXTRAS=dev
set C=
if exist constraints.txt set C=-c constraints.txt
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q %C% -e ".[%EXTRAS%]" || goto :err
echo 完了: .venv\Scripts\activate のあと harness --help
pause
exit /b 0
:err
echo セットアップに失敗しました。Python 3.12 と Git が入っているか確認してください。
pause
exit /b 1
