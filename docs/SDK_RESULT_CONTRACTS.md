# SDK 返回值与执行状态契约

本表覆盖当前支持的双代际 Windows TQ SDK（`user_tpythclient` 与 `sys_unified`），适配点为 `core/sdk.py` 与 `core/protocol.py`。供应商 Python 层会把不同错误折叠为 `-1`、`{}`、`[]`、`None` 或普通中文提示字符串，适配器在原生 DLL 函数与分页 JSON 解析边界保存单次调用的协议证据。

| 接口组 | 成功结果 | 合法空结果 | 失败、不支持或无法确认 |
| --- | --- | --- | --- |
| 当前 SDK 缺失的可选方法 (`KNOWN_OPTIONAL_SDK_METHODS`) | — | — | 未进入 DLL，立即抛出 `UNSUPPORTED_SDK_CAPABILITY`（附 `method` 与 `sdk_profile`），绝不伪造返回数据 |
| `stock_account` | 非布尔的非负整数句柄 | 账户查询原生 `GetOrderStr/type=0` 的 `ErrorId=9`，或成功回执 `Value=-1`，可返回 `-1` | 无对应依据的 `-1`、错误类型、空响应、其他原生错误：`ACCOUNT_STATE_UNKNOWN`；`user_tpythclient` 无该方法时返回 `UNSUPPORTED_SDK_CAPABILITY` |
| `query_stock_asset` | 非空字典，并存在成功原生回执 | 不接受 `{}` 作为可用资产信息 | 原生失败或数据形态错误：`SDK_DATA_ERROR` |
| `query_stock_positions`、`query_stock_orders` | 列表，并存在成功原生回执 | 成功回执确认的 `[]` 有效 | 传输失败与无可核实回执不能解释为空持仓或空委托 |
| `get_financial_data`、`get_financial_data_by_date`、`get_gpjy_value*`、`get_bkjy_value*`、`get_scjy_value*`、`get_gp_one_data`、`formula_exp`、`formula_process_mul_exp` | 字典，并存在成功原生回执 | 成功回执确认的 `{}` 可表达数据缺失 | 原生失败或数据类型错误必须失败；命令层进一步核对证券、请求字段与完整性 |
| `get_relation`、`get_ipo_info`、`get_trackzs_etf_info`、`get_gb_info_by_date` | 列表，并存在成功原生回执 | 成功回执确认的 `[]` 有效 | 原生失败或数据类型错误必须失败；`stock relation` 在 `user_tpythclient` 下改由本地 `T0002/hq_cache` 解析并标记 `source="local_tdx_cache"` |
| `get_market_snapshot`、`get_market_data`、`get_stock_info` | 可用的非空结果 | 不接受必要数据为空 | 原生错误、必要数据缺失必须失败 |
| `order_stock`、`cancel_order_stock` | 字典，且有效 `ErrorId=0`；代表 SDK 受理 | 无 | 明确负回执为 `SDK_DATA_ERROR`；发出后空响应、畸形回执、传输/执行中断为 `TRADE_STATE_UNKNOWN` |
| 客户端、板块、实时订阅与缓存/下载操作 (`send_message`、`send_file`、`send_warn`、`send_bt_data`、`send_user_block`、`subscribe_hq`、`unsubscribe_hq`、`create/delete/rename/clear_sector`、`refresh_cache`、`refresh_kline`、`download_file`) | 字典或 JSON 字符串包含有效 `ErrorId=0`，保留原结果类型 | 无 | 明确负回执失败；供应商返回非 JSON 异常字符串或发出后不能确认则 `OPERATION_STATE_UNKNOWN` / `SDK_DATA_ERROR` |

## 双代际原生函数映射

映射用于识别副作用边界与协议确认，不会替用户自动重试写操作。

| SDK 方法 | `sys_unified` 原生函数 (`type`) | `user_tpythclient` 原生函数 |
| --- | --- | --- |
| `order_stock` | `GetOrderStr` (`4`) | `SetNewOrder` |
| `cancel_order_stock` | `GetOrderStr` (`5`) | *(未提供)* |
| `send_message` / `send_file` / `send_warn` / `send_bt_data` / `send_user_block` | `SetMsgToMain` (`2` / `3` / `4` / `5` / `1`) | `SetResToMain` (`MSG\|\|` / `FILE\|\|` / `WARN\|\|` / `BTR\|\|` / `XG,`) |
| `subscribe_hq` / `unsubscribe_hq` | `SetMsgToMain` (`6` / `7`) | `SubscribeHQDUpdate` (`flag=0` / `flag=1`) |
| `create_sector` / `delete_sector` / `rename_sector` / `clear_sector` | `SetMsgToMain` (`11` / `12` / `13` / `14`) | `UserBlockControl` (`op=1` / `2` / `3` / `4`) |
| `refresh_cache` / `refresh_kline` / `download_file` | `GetTdxDataStr` (`16` / `17` / `18`) | `ReFreshCacheAll` / `ReFreshCacheKLine` / `DownLoadFiles` |
| `exec_to_tdx` | `SetMsgToMain` (`15`) | *(未提供)* |

## 会话生命周期与关闭确认契约

`Session.state` 显式区分 `uninitialized`、`initializing`、`ready`、`closing`、`closed` 与 `failed`：
- 初始化中途异常时尽力执行底层释放并将状态置为 `failed`；
- `Session.close()` 调用供应商 `close()` 后额外检查 `raw._initialized`：若供应商 `_auto_close()` 内部捕获了 `CloseConnect` 异常并保持 `_initialized=True`，`Session.close()` 立即抛出 `SDK_UNAVAILABLE`（`底层 SDK 未确认关闭连接`），拒绝向 CLI 调用方返回虚假的 `success=true`。

## 常驻守护进程 (`daemon`) 与可抢占超时 (`--timeout`) 契约

- **`daemon start/stop/status/serve`**：在用户配置目录（与 `config.json` 同级）维护原子 `daemon.json` 状态文件，仅监听 `127.0.0.1` 并强制校验 256-bit 随机令牌与 `tdx_path` 一致性。守护进程内以互斥锁串行化所有 `TPythClient.dll` 调用并持久保持 `Session` 连接不释放，空闲超时或收到 `stop` 指令时自动调用 `session.close()` 并清理状态文件。
- **`--timeout <秒>` 可抢占截止时间**：
  - 读取类命令超时终止后返回 `SDK_TIMEOUT`（`retryable=true`）；
  - 交易写操作（`trade order --confirm`、`trade cancel`）发出后超时或连接中断，严格返回 `TRADE_STATE_UNKNOWN`（`retryable=false`），绝不自动回退重发；
  - 客户端/板块/缓存写操作发出后超时或连接中断，严格返回 `OPERATION_STATE_UNKNOWN`（`retryable=false`）。

## 实时订阅流、分块批处理与宽表契约

- **`market watch`**：订阅上限为 100 只标的（超出在连接前返回 `INVALID_PARAMETER`）。C 回调函数 `on_data(datas)` 仅将原始事件推入内存队列，由主线程按 `--debounce-ms` 防抖后拉取 `get_market_snapshot`，并在 `finally` 块保证调用 `unsubscribe_hq`。
- **`market get --chunk-size` 与多格式导出**：大批量证券支持按 `chunk_size` 切片调用并向 `stderr` 输出进度；部分停牌空分块不会中断其余有效分块，最终统一交由 `assess_market` 执行严格或 `--allow-partial` 核验。原子导出支持 `.csv`、`.json`、`.jsonl`（`.ndjson`）、`.xlsx` 与 `.parquet`（需安装可选依赖 `pyarrow`）。
- **`market get --pivot`**：在完成严格或 `--allow-partial` 数据完整性核验后，将可用 K 线重排为以 `date` 为索引、请求证券顺序为列的宽表；`--fill` 仅允许 `none`（默认）或 `ffill`，禁止 `bfill` 未来数据回填。
- **批量完整性信封（`schema_version=1`）**：适用于 `market get`、`market snapshot`、`financial get`、`financial by-date`、`financial one` 与 `financial trading` 的 `--allow-partial` 模式，字段语义保持向后兼容。

## 本地向量化公式引擎与策略联动契约 (`formula eval` / `formula * --local`)

- **显式引擎边界**：未传 `--local` 时，`formula list/info/zb/xg/exp` 仅调用底层 SDK 原生接口，缺失时抛出 `UNSUPPORTED_SDK_CAPABILITY`；显式传入 `--local` 或使用 `formula eval` 时，基于 `get_market_data` 真实 K 线在本地执行零 `eval` AST 向量化计算，并在响应中明确标识 `engine="local_vectorized"`。
- **语法与安全隔离**：词法分析器与递归下降解析器仅允许算术/比较/逻辑运算与注册表内的 33 个通达信算子，拒绝属性访问、下标或任意代码执行；除零运算返回 `null`（内部 `NaN`），语法或参数错误在连接客户端前返回 `INVALID_PARAMETER`（退出码 `2`）。
- **零误清空保护**：`formula eval` 搭配 `--push-sector` 或 `--push-temp` 时，若本次选股命中数为 `0`（`selected_stocks=[]`），跳过 `send_user_block` 并在 `actions.sector_push` 记录 `pushed=false, reason="no_selected_stocks"`，防止空结果意外清空用户既有板块。
