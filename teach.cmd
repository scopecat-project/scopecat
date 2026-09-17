@echo off
cd /d "%~dp0"
if not "%~1"=="" goto arguments
echo 1. Source sandbox (fast, no GUI build)
echo 2. Build, verify and install a fixed release on this PC
echo 3. Build and verify an offline release for another PC
choice /c 123 /n /m "Choose 1-3: "
if errorlevel 3 goto offline
if errorlevel 2 goto install
uv run --no-project --python 3.14 python teach.py source
goto done
:install
uv run --no-project --python 3.14 python teach.py release --install
goto done
:offline
uv run --no-project --python 3.14 python teach.py release
goto done
:arguments
uv run --no-project --python 3.14 python teach.py %*
exit /b %errorlevel%
:done
pause
