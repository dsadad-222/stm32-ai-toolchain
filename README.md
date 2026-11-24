# STM32 AI 工具链辅助脚本

自写的一组 STM32 开发辅助脚本，配合 Keil MCP（27 个工具）+ 串口 MCP（14 个工具）在 WorkBuddy 里使用。
MCP Server 本身是开源项目，本仓库只含自写脚本。

## 脚本

- serial_plot.py
- map_analyze.py
- check_code.py
- flash_diff.py
- toolbox.ps1
- git_panel.ps1
- selfcheck.py

## 环境

- Keil MDK + STM32F103ZE（ST-Link 调试）
- Python 3

## 验证

2026 年 9 月搭建并验证：Keil 工程 0 Error 0 Warning，HEX 回读与原文件字节一致。