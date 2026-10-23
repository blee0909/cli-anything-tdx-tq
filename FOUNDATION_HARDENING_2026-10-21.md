底座加固实施记录（2026-10-21）

本轮已将评估中的交易正确性、数据语义、SDK 加载、输出/导出及可复现质量门禁落实到代码。源码尚未提交或推送，原始评估报告保留为变更前快照。

**已完成的改动**

- 交易请求与提交映射拆入 core/trading.py。预览离线执行，校验证券、数量、有限价格和报价类型；只有 --confirm 提交。SDK -1 不再误报成功；ErrorId=0 标记 accepted；交易超时或无法确认的响应标记 TRADE_STATE_UNKNOWN，不自动重试。
- 行情归一化拆入 core/market.py。保留完整分钟时间并合并字段时间轴；拒绝重复索引、SDK 错误和空必要数据。60m 映射 1h，tick 明确拒绝。交易日历故障明确报错，股本查询不补造或重复日期。
- SDK 适配集中在 core/sdk.py。解析配置后按指定来源加载，捕获加载故障、核对模块来源、收窄兼容补丁，记录方法耗时。账户查询的 -1 单独表示未登录；只读超时可重试，静态 SDK 故障和交易未知状态不可自动重试。
- 统一递归严格 JSON、错误码和退出码。嵌套 DataFrame、日期、numpy 值保持结构，非有限值转换 null；所有叶子命令具备 --json。文件导出返回结构化信息。
- 配置增加来源及损坏诊断，配置和导出文件使用原子替换。补齐 openpyxl，真实 Excel 导出验证通过；命名日期索引不再丢失。
- 使用 __version__ 统一版本，移除重复 setup 元数据，补齐 MIT LICENSE、包资源和 MANIFEST。wheel 包含 skill 并排除测试，sdist 保留完整回归源码及 uv.lock。
- 新增 uv.lock、.python-version、Python 3.12/3.13 Windows CI、独立语句/分支门禁和安装后冒烟脚本。测试不依赖个人配置，真实客户端测试必须显式 --live。

**最终验证**

| 验证 | 结果 |
|---|---|
| Python 3.12.14 锁定环境离线测试 | 258 passed；9 个真实测试未选择；13.00 秒 |
| Python 3.13.15 独立锁定环境离线测试 | 258 passed；9 个真实测试未选择；10.24 秒 |
| 真实客户端只读测试 | 9 passed；15.17 秒；没有通过 skip 隐藏失败 |
| 语句覆盖率 | 93.76%，变更前为 53.12% |
| 分支覆盖率 | 83.16%，变更前为 29.45% |
| 门禁 | 语句至少 90%、分支至少 80%；两个环境通过 |
| 依赖与锁定 | 两环境 uv pip check 通过；uv lock --check --offline 通过 |
| 构建及安装 | wheel/sdist 成功；两个 Python 版本在源码目录之外完成 wheel 冒烟 |
| 真实 Excel 导出 | 2 条行情，工作簿 3 行（含表头）、10 列 |
| 错误路径/周期/证券 | 不再空对象退出 0，分别返回 SDK_UNAVAILABLE / INVALID_PARAMETER |
| git diff --check | 通过 |

真实验证只涉及读取与导出。下单、撤单、板块写入和 GUI 通知的契约测试使用内存替身，没有真实交易或通知。CI 配置已添加，本地执行了对应流程；尚未在 GitHub 运行。

**兼容性变化**

正式支持范围收敛为已验证的 Windows Python 3.12/3.13。tick、.xls、未知导出扩展名及非正数量会明确拒绝。限价订单价格必须大于零，市价需显式指定报价类型。预览未指定账户时 account_id 为 null，提交时再查询。Timestamp 索引输出完整 ISO 日期时间；CSV 不再附带默认 RangeIndex。Config 的路径赋值不再修改进程环境变量，命令覆盖和环境变量来源分开记录。损坏配置不再被静默当作默认配置使用。

**后续专项边界**

原生 SDK 调用仍可能阻塞至 SDK 自身超时，尚无所有调用统一的可抢占式端到端截止时间。实盘提交、撤单最终状态以及券商/市场对应的数量步长、价格精度需要受控环境专项验证。本轮没有增加常驻服务或多线程共享 SDK；批量性能优化应继续依据耗时日志与真实负载测量。

实施入口见 [README](E:/allmyrepos/cli-anything-tdx-tq/README.md)、[架构契约](E:/allmyrepos/cli-anything-tdx-tq/TDX_TQ_SDK.md)、[CI](E:/allmyrepos/cli-anything-tdx-tq/.github/workflows/ci.yml)。

证据：[3.12 测试日志](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/release312.log)、[3.13 测试日志](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/release313.log)、[真实读取日志](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/live-final.log)、[覆盖率](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/coverage-release312.json)、[安装后验证](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/wheel-smoke.json)、[额外真实检查](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/extra-checks.json)、[事实汇总](E:/allmyrepos/cli-anything-tdx-tq/temp/implementation-20261021/final-facts.json)。
