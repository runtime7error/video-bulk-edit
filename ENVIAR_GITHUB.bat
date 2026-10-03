@echo off
cd /d "%~dp0"
set "PATH=%PATH%;C:\Program Files\Git\cmd"
echo ========================================================
echo Enviando codigo para:
echo https://github.com/runtime7error/video-bulk-edit
echo ========================================================
echo.
git push -u origin main
echo.
pause
