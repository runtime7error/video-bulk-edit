@echo off
REM Clique duas vezes para processar todos os videos da pasta "entrada",
REM ou arraste arquivos de video em cima deste arquivo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0processar.ps1" %*
pause
