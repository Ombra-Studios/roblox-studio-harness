@echo off
rem Optional: pentru cine foloseste doar Studio, fara un terminal Claude Code/Codex deschis (pluginul porneste daemon-ul singur
rem din sesiunea CLI). Daemon-ul asculta pe 127.0.0.1:34871, se conecteaza singur la hub-ul central si afiseaza doar caile
rem fisierelor de stare (%LOCALAPPDATA%\StudioHarness), niciodata token-urile. Argumente optionale: --no-hub, --port, --state-dir.
cd /d "%~dp0"
python -u scripts\studio_bridge.py %*
if errorlevel 1 pause
