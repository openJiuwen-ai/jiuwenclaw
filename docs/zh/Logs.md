# 日志与排障（Logs）

本文档说明 JiuwenSwarm 的日志位置与排障方法。

## 日志位置

JiuwenSwarm 的日志保存在用户目录下：

- 运行日志：`~/.jiuwenswarm/logs/`
- Agent 日志：`~/.jiuwenswarm/agent/.logs`

## 查看日志

- Windows：`%USERPROFILE%\.jiuwenswarm\logs\`
- macOS / Linux：`~/.jiuwenswarm/logs/`

## 日志级别

- 默认输出到控制台与日志文件
- 可通过配置调整日志级别，便于定位问题

## 提交 Issue 时的日志要求

按 Bug 报告模板提交 Issue 时，请附上：

1. 版本号（`jiuwenswarm --version`）
2. 操作系统与 Python 版本
3. 复现步骤
4. 期望行为与实际行为
5. 相关日志（`~/.jiuwenswarm/logs/` 与 `~/.jiuwenswarm/agent/.logs` 中的关键片段）

## 常见问题

### 找不到日志目录

- 确认已执行过 `jiuwenswarm-init`
- 首次启动后日志目录才会创建

### 日志过多占用空间

- 可定期清理历史日志
- 反馈问题时只需提供关键时间段的日志片段