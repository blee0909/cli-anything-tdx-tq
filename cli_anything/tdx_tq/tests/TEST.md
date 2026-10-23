# 测试与验收

所有命令在仓库根目录执行，使用 Python 3.12/3.13 和 uv.lock。

~~~powershell
uv sync --locked --extra test
uv run --locked --extra test pytest -m "not live" --cov=cli_anything/tdx_tq --cov-report=json:coverage.json
uv run --locked python scripts/check_coverage.py coverage.json
uv run --locked --extra test pytest --live -m live -q
uv build
~~~

默认测试不需要通达信。--live 显式启用只读客户端测试，要求 SDK 路径正确且客户端已登录。已启用真实测试时，接口失败必须失败。取得明确无可用账户的协议回执时返回 logged_in=false、state=unavailable；无法确认时应失败，不能仅凭 -1 推断未登录。任何实际下单、撤单、板块写入和 GUI 通知均不属于真实集成测试。

| 文件 | 验证内容 |
|---|---|
| test_config.py、test_resilience.py | 配置来源、损坏诊断、原子替换、异常处理 |
| test_output.py、test_export.py | 文本和严格 JSON、索引保留、CSV/JSON/XLSX |
| test_session.py、test_sdk.py | 惰性加载、SDK 来源、兼容补丁、初始化和日历 |
| test_regressions.py | 评估发现的交易、时间和序列化缺陷 |
| test_contracts.py | 全部叶子命令的 JSON/文本成功路径、JSON 错误契约及交易参数映射 |
| test_full_e2e.py | 真实进程入口和显式客户端读取 |

通过率和代码覆盖率分别统计。coverage 包含 CLI 子进程，排除测试源码；脚本门禁分别要求语句 90%、分支 80%。不使用通过数/测试数作为代码覆盖率。

默认缓存或临时目录受限时，为 uv 指定 --cache-dir，为 pytest 指定仓库内独立 --basetemp，并使用 -p no:cacheprovider。覆盖率报告及构建产物属于生成文件。

第二轮协议回归见 test_protocol.py：使用内存原生函数验证正常空集合、原生空响应/异常/超时、明确拒绝、畸形错误码、分页重组、账户状态、写操作回执和除权日期保留。真实除权日期读取纳入 test_full_e2e.py 的 --live 场景。

第三轮回归见 test_data_quality.py：87 个新增场景覆盖数量/区间互斥、日期精度与边界、无填充、实际可用证券、缺字段/缺值、严格导出保护、部分结果信封、整数成功码在供应商字符串比较前规范化、Value 丢失检测和跨页面 Errors 保留。test_full_e2e.py 新增历史单日区间与批量缺数据的严格/部分模式真实读取。2026-10-21 本轮快照为 Python 3.12/3.13 各 477 项离线通过，13 项真实只读通过；补充长区间实测取得 190 条，供应商原生函数全部替换的补充兼容探针 17 项通过。详细边界与证据见[历史实施记录](../../../docs/archive/2026-10-21/FOUNDATION_OPTIMIZATION_ROUND3_2026-10-21.md)。
