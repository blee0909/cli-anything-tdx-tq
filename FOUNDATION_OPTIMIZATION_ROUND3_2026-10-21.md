# 第三轮底座优化与实测验收（2026-10-21）

已完成第三轮评估确定的 A 批：行情数量/区间模式、批量可用性、财务字段完整性，以及整数/字符串成功码一致性。继续保留上一轮协议与交易状态约束。本次不是对所有底座风险的完成声明，生命周期与运行预算仍需下一批处理。

## 已落地的行为

| 原问题 | 当前行为 | 核验方式 |
| --- | --- | --- |
| --start 被默认 count=60 覆盖 | MarketRequest 明确尾部/区间模式；区间内部 count=-1，显式 --start 与 --count 冲突在连接前失败 | 正式请求测试、真实单日区间及 190 条长区间 |
| 八位截止日与时间精度不清晰 | K 线八位 --end 展开为当天 23:59:59，比较真实日期时间；拒绝返回区间外数据 | 不同日期精度、日内边界与越界响应回归 |
| 无数据证券有键、有日期、有行数却被视为成功 | 至少有有限 close 的记录才可用；关闭 SDK 前值填充；默认严格完整性失败 | 真实混合批次与全空/非有限/零值测试 |
| 请求 FN1 却只返回元数据或 -- | 从请求字段逐值反向核验；缺字段、缺值和无可用行显式报告 | 真实 FN1 缺失和占位响应、DataFrame/字典/数组测试 |
| 供应商丢弃逐证券 Errors | 单次调用 read_with_metadata 提取并传递错误；前页错误不能被后页成功清除 | 正式协议/分页回归和供应商桩化实现 |
| 原生整数 ErrorId=0 被供应商字符串比较误判 | 在供应商解析方法返回后、业务判断前，把有效整数码规范为数字字符串 | 正式替身恢复真实字符串判断；实际供应商方法注入 0/'0' 对照 |
| 非空原生读取结果折叠为空集合 | 受保护读取方法额外拒绝非空 Value 丢失 | 正式 Value 丢失回归 |
| SDK 非数字价格被误判为用户参数错误 | 数据转换失败归 SDK_DATA_ERROR；拒绝空时间、大小写字段冲突 | 正式异常数据回归 |
| 财务空字段、非法日期/报告期连接后才失败 | 先校验证券、非空字段、日期、年份和季度报告期再初始化 | 连接次数为零的正式回归 |

## 结果契约与兼容性

market get、financial get、financial by-date 的完整结果继续以证券为键。严格模式不完整时退出 1，error_code=DATA_INCOMPLETE、retryable=false；details 列出 requested_stocks、available_stocks、missing_stocks、missing_fields、missing_values、record_counts、dropped_records、errors、unexpected_stocks 和 warnings，data 保留可用部分。

--allow-partial 在至少存在部分可用数据时允许成功，始终返回 schema_version=1 的 JSON 信封，即使结果完整或未加 --json。信封的 partial 明确说明完整性。全部无可用数据仍失败；不会把空数据包装为成功。

财务无任何请求字段可用的行被排除，部分值缺失规范为 null，不原地修改 SDK 数据。数值零和字符串零是有效财务数据。财务默认 RangeIndex 不再输出人为 index 字段，有意义的日期索引继续保留。

严格导出先验证后写入，不完整时不会替换已存在文件。显式部分导出只写有效记录，控制台信封返回缺失说明及 output/format/records；保存 CSV/XLSX/JSON 数据文件时，应同时保存控制台信封以保留完整性信息。

K 线的最低可用标准为有限 close，未宣称所有可选字段齐备。当前校验针对观察到的结果，不证明整个交易日历覆盖、不证明证券不存在、不解释缓存/权限/字段不支持的具体根因。financial trading、公式、其他读取不统一套用本批信封。

使用示例：

~~~powershell
uv run --locked tdx-tq market get -s 600519.SH --start 20261001 --end 20261020 --json
uv run --locked tdx-tq market get -s 600519.SH,999999.SZ -c 2 --allow-partial --json
uv run --locked tdx-tq financial get -s 600519.SH -f FN1,FN4 --allow-partial --json
~~~

## uv 实际验收结果

环境为 Windows、uv 0.12.17。主环境 Python 3.12.14；第二套既有锁定测试环境为 Python 3.13.15。本轮通过 uv run --no-sync 明确选择解释器执行，不把它描述成重新创建并同步的测试环境。uv lock --check --offline 通过。

| 验证项 | 结果 |
| --- | --- |
| Python 3.12.14 全量离线回归 | 477 passed，13 个 live 场景未选中 |
| Python 3.13.15 全量离线回归 | 477 passed，13 个 live 场景未选中 |
| 本次新增正式离线回归 | 87 项，纳入以上 477 项 |
| 真实客户端只读集成 | 13 passed，包含 3 项新增区间/批量场景 |
| 补充探针 | 17 passed：15 项使用真实供应商 Python 方法且原生函数全部替换，2 项使用内存会话 |
| 语句覆盖率 | 1687 / 1783 = 94.62%，超过 90% 门槛 |
| 分支覆盖率 | 477 / 550 = 86.73%，超过 80% 门槛 |
| 构建、sdist 重建、wheel 内容比较 | 全部通过；比较时排除 RECORD 清单自身 |
| 两个全新 wheel 安装环境 | 3.12/3.13 均通过资源、版本、双入口、离线诊断、订单预览、新质量契约冒烟 |
| 安装依赖检查 | 两环境 uv pip check 通过 |
| 锁文件与差异格式检查 | uv lock --check --offline、git diff --check 均通过 |

测试中的真实客户端操作仅为读取和当前进程连接的建立/关闭。交易、撤单、板块、缓存和 GUI 写操作全部使用替身，没有实际发送。没有触发远端 CI；以上“通过”均为本地实际执行结果。

### 真实数据复核

- 600519.SH，2026-10-20 单日区间：只返回 1 条、日期为 2026-10-20。
- 600519.SH，2026-01-01 至 2026-10-20：返回 190 条，确认不再被默认 60 条截断；不据此证明缓存覆盖了所有交易日。
- 600519.SH 与 999999.SZ，尾部 2 条：严格模式退出 1，DATA_INCOMPLETE；允许部分时退出 0，partial=true，只保留前者的 2 条，并列出后者不可用。
- 600519.SH，2024 年 FN1 历史请求：当前客户端缺少 FN1，改为 DATA_INCOMPLETE。
- 600519.SH，2025 年年报 FN1：当前客户端返回 --，改为 DATA_INCOMPLETE。
- 显式 --start 与 -c 同时提供：退出 2，INVALID_PARAMETER，未连接客户端。

供应商原生函数桩化对照覆盖委托/持仓查询、下单/撤单回执、行情、财务历史和按报告期财务的整数/字符串成功码。真实客户端当前返回字符串成功码；注入测试不代表实际账户曾发送整数码或发生数据丢失。

## 尚需继续夯实的方向

| 顺序 | 当前剩余问题 | 下一批验收重点 |
| --- | --- | --- |
| B，P1 | 部分初始化后清理、关闭失败状态矛盾；公式模式下忽略选项；客户端跳转丢市场身份；剩余命令参数与配置 stat 异常边界 | 状态与资源一致、关闭失败不确认成功、参数生效或明确拒绝、保留市场身份、文件系统错误进入可修复诊断 |
| C，P1 | SDK 可长时间阻塞，没有整体 deadline、证券/字段/输出体积预算 | 超大请求先拒绝，阻塞读取可终止并诊断；交易中断继续保持未知而不自动重试 |
| D，P2 | 发布安装仍浮动解析依赖、构建工具未固定、SDK 版本能力约束不足 | 明确构建/运行约束及兼容矩阵，记录 SDK/DLL 指纹与能力，再按基准优化 |
| 交易扩展前 | 多次原生写尝试的状态聚合、未知操作持久化核对、账户并发协调 | 任何未确认写尝试影响整体状态；本地操作 ID 不宣称服务端恰好一次 |

本次独立安装仍出现 3.12 numpy 2.5.4、3.13 numpy 2.5.3；两者通过冒烟，但不等于发布运行环境与锁定回归完全一致。此问题保留在 D 批，没有因本次构建成功而标记解决。

本轮没有扩展并发、自动重试、后台守护或交易执行能力。代码改动保留在工作区，未提交、推送或发布。

## 代码与证据

- 请求与行情规范化：[core/market.py](E:/allmyrepos/cli-anything-tdx-tq/cli_anything/tdx_tq/core/market.py)
- 完整性规则：[core/quality.py](E:/allmyrepos/cli-anything-tdx-tq/cli_anything/tdx_tq/core/quality.py)
- 协议和适配：[core/protocol.py](E:/allmyrepos/cli-anything-tdx-tq/cli_anything/tdx_tq/core/protocol.py)、[core/sdk.py](E:/allmyrepos/cli-anything-tdx-tq/cli_anything/tdx_tq/core/sdk.py)
- 正式回归：[test_data_quality.py](E:/allmyrepos/cli-anything-tdx-tq/cli_anything/tdx_tq/tests/test_data_quality.py)
- 公共契约：[SDK_RESULT_CONTRACTS.md](E:/allmyrepos/cli-anything-tdx-tq/SDK_RESULT_CONTRACTS.md)
- 全量离线日志：[3.12](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/offline312.json)、[3.13](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/offline313.json)
- 真实读取：[集成日志](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/live312.json)、[补充结果](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/live-quality-probes.json)
- 供应商兼容探针：[原探针选定场景](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/vendor-native-stubs.json)、[行情/财务成功码](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/vendor-batch-codes.json)
- 覆盖率：[coverage312.json](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/coverage312.json)
- 发布验收：[安装](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/release-installs.json)、[冒烟](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/release-smoke.json)、[产物/依赖检查](E:/allmyrepos/cli-anything-tdx-tq/temp/optimization-round3-20261021/release-checks.json)
