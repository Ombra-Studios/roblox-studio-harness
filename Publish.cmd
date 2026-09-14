@echo off
rem Publica o versiune noua: bump patch, build, scanare de secrete, apoi git add/commit/push pe repo-ul GitHub din update-channel.json (github_repo).
rem Prima data: python scripts\publish_release.py --github OWNER/REPO --bump patch (salveaza github_repo in update-channel.json).
cd /d "%~dp0"
python scripts\publish_release.py --github --bump patch %*
set CODE=%errorlevel%
if not "%CODE%"=="0" pause
exit /b %CODE%
