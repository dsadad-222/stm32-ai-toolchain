@echo off
chcp 65001 >nul
title STM32_Project 代码版本管理
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0git_panel.ps1"
if errorlevel 1 pause
