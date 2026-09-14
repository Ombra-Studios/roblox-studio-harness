@echo off
rem Hub local Studio Harness (scripts\team_hub.py, port 34880), pentru self-hosting sau teste. Hub-ul central
rem https://lostcube.pro/roblox/harness nu are nevoie de el: daemon-ul se conecteaza acolo singur, fara configurare.
rem Un daemon de pe acest PC il foloseste cu {"hub_url": "http://127.0.0.1:34880"} in %LOCALAPPDATA%\StudioHarness\config.json
rem sau cu STUDIO_HARNESS_HUB_URL; din LAN/internet doar prin HTTPS (reverse proxy, vezi deploy\ubuntu).
rem Implicit hub-ul asculta doar pe 127.0.0.1; expunerea in retea se cere explicit (Start-Hub.cmd --listen 0.0.0.0)
rem si doar in spatele unui proxy HTTPS, pentru ca hub-ul vorbeste HTTP simplu.
rem Codul de administrator: %LOCALAPPDATA%\StudioHarness\hub-admin-token (afisat cu Start-Hub.cmd --show-admin-code).
rem Dispozitivele noi asteapta aprobarea din panou (fila Dispozitive) sau Start-Hub.cmd --approve-pending;
rem Start-Hub.cmd --open-enrollment le aproba automat.
cd /d "%~dp0"
python -u scripts\team_hub.py %*
if errorlevel 1 pause
