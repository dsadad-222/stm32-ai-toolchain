@echo off
chcp 65001 >nul
title STM32 嵌入式工具箱
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0toolbox.ps1"
if errorlevel 1 pause
