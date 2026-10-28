@echo off
rem セットアップ（Windows）: .venv を作って harness と fieldcheck、doc2md（harness convert 用）を入れる
rem   setup.cmd               すべて（[dev] と [convert]）
rem   setup.cmd --no-convert  Office の変換（harness convert）を使わないとき
rem   使う Python: py ランチャーがあれば py -3、無ければ python。環境変数 SETUP_PYTHON で指定もできる
rem   環境変数 CI があるとき（GitHub Actions など）は最後の pause をしない
cd /d "%~dp0"
set EXTRAS=dev,convert
if "%~1"=="--no-convert" set EXTRAS=dev
set C=
if exist constraints.txt set C=-c constraints.txt
set PY=python
where py >nul 2>nul && set PY=py -3
if defined SETUP_PYTHON set PY=%SETUP_PYTHON%
%PY% -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul || goto :oldpy
%PY% -m venv .venv || goto :err
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q %C% -e ".[%EXTRAS%]" || goto :err
echo 完了: .venv\Scripts\activate のあと harness --help
if not defined CI pause
exit /b 0
:oldpy
echo Python 3.12 以上が必要です（%PY% で起動できないか、版が古い）。Python 3.12 以上を入れてから実行してください。
if not defined CI pause
exit /b 1
:err
echo セットアップに失敗しました。Python 3.12 以上と Git が入っているか確認してください。
if not defined CI pause
exit /b 1
