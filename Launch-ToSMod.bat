@echo off
:: ToSMod Launcher  —  double-click this file to start
:: Delegates to Launch-ToSMod.ps1 for robust Python detection.
:: Passes any argument through: quick / full / docker / help
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-ToSMod.ps1" %*
pause
