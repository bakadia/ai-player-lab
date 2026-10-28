@echo off
rem Convenience wrapper so you can run "bph.cmd restart" without PowerShell flags.
rem Run it from an elevated cmd if the deploy step says Access Denied.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bph.ps1" %*
