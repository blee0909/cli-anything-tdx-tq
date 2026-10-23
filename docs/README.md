# 开发文档

| 资料 | 用途 |
|---|---|
| [架构与执行契约](TDX_TQ_SDK.md) | 当前模块职责、SDK 加载、连接和调用边界 |
| [SDK 返回契约](SDK_RESULT_CONTRACTS.md) | 原生回执、错误分类与交易状态语义 |
| [测试与验收](../cli_anything/tdx_tq/tests/TEST.md) | 测试入口、覆盖范围与质量门禁 |
| [2026-10-21 历史记录](archive/2026-10-21/) | 三轮评估和对应实施记录，保留当时证据，不作为当前使用说明 |

## 资料归类

- 根目录只保留使用入口、许可、构建与依赖配置；README 保持简明。
- 当前架构、协议和开发说明放在 `docs/`，继续纳入版本控制。
- 有长期参考价值的评估、设计和实施记录放在 `docs/archive/<日期>/`，继续跟踪，但不进入发布包。
- 单机实测输出、审计 JSON、日志、覆盖率和构建结果属于生成物，分别放在 `reports/`、`logs/`、`temp/` 等忽略目录；需要正式保存的结论整理成归档文档。
- 导出的行情、财务或账户数据建议写入 `exports/`，不作为源码提交。不要按 `.json/.csv/.xlsx` 后缀整体忽略，以免隐藏测试夹具或共享配置。

`cli_anything/tdx_tq/skills/SKILL.md` 是随 wheel 分发的资源，必须保留在原位置。测试源码、CI、LICENSE、MANIFEST.in、pyproject.toml、uv.lock 和 .python-version 同样继续跟踪。

`.gitignore` 只影响未跟踪文件，不能让已跟踪文件自动停止跟踪。本地生成报告停止跟踪时保留磁盘文件；此前已提交的内容仍保存在 Git 历史中。环境配置 `.env`、`.env.*`、`.tdx_tq/` 和 `config.local.json` 保留在本地，共享模板 `.env.example`、`.env.template` 可以提交。

归档中的本机 SDK 和 `temp/` 日志链接用于记录当时证据，不随仓库或发布包分发，其他环境中不保证可访问。
