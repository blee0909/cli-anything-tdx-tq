# SDK 返回值与执行状态契约

本表对应当前支持的 Windows TQ SDK，适配点为 core/sdk.py 和 core/protocol.py。供应商 Python 层会把不同错误折叠为 -1、{}、[] 或 None，适配器在五个原生 RPC 函数及分页 JSON 解析边界保存单次调用的协议证据。

| 接口组 | 成功结果 | 合法空结果 | 失败或无法确认 |
| --- | --- | --- | --- |
| stock_account | 非布尔的非负整数句柄 | 账户查询原生 GetOrderStr/type=0 的 ErrorId=9，或成功回执 Value=-1，可返回 -1 | 无对应依据的 -1、错误类型、空响应、其他原生错误：ACCOUNT_STATE_UNKNOWN |
| query_stock_asset | 非空字典，并存在成功原生回执 | 不接受 {} 作为可用资产信息 | 原生失败或数据形态错误：SDK_DATA_ERROR |
| query_stock_positions、query_stock_orders | 列表，并存在成功原生回执 | 成功回执确认的 [] 有效 | 传输失败与无可核实回执不能解释为空持仓或空委托 |
| get_financial_data、get_financial_data_by_date、get_gpjy_value、get_bkjy_value、get_scjy_value | 字典，并存在成功原生回执 | 成功回执确认的 {} 可表达数据缺失 | 原生失败或数据类型错误必须失败；financial get/by-date 在命令层进一步核对证券、请求字段与值；trading 统计仍使用既有契约 |
| get_market_snapshot、get_market_data、get_stock_info | 可用的非空结果 | 不接受必要数据为空 | 原生错误、必要数据缺失必须失败 |
| 其他读取 | 保留 SDK 既有结果结构 | 不统一禁止空集合 | 捕获到原生错误时失败；本表尚未逐字段约束全部供应商返回类型 |
| order_stock、cancel_order_stock | 字典，且有效 ErrorId=0；代表 SDK 受理 | 无 | 明确负回执为 SDK_DATA_ERROR；发出后空响应、畸形回执、传输/执行中断为 TRADE_STATE_UNKNOWN |
| 客户端、板块、缓存操作 | 字典或 JSON 字符串包含有效 ErrorId=0，保留原结果类型 | 无 | 明确负回执失败；发出后不能确认则 OPERATION_STATE_UNKNOWN |

ErrorId 必须为整数或规范的数字字符串；布尔值、null、对象、非数字字符串均不是可靠确认。有效整数码在供应商方法比较前规范为数字字符串（例如整数 0 转为字符串 0），避免供应商仅接受字符串成功码而丢失 Value。原生 Value 非空而受保护读取方法返回空值时额外拒绝。SDK 传输分页 TPythPaged 可不带最终 ErrorId，其重组后的 JSON 仍需核对；分页外层明确错误不会被忽略。发生过原生失败后，后续成功或连接恢复不能抹去前面的失败证据。

客户端、板块与缓存操作及本机 SDK 的原生类型如下。映射用于识别操作边界，不会替用户发起操作或自动重试。

| 方法 | 原生函数 | type |
| --- | --- | --- |
| order_stock / cancel_order_stock | GetOrderStr | 4 / 5 |
| exec_to_tdx | SetMsgToMain | 15 |
| send_message / send_warn / send_user_block | SetMsgToMain | 2 / 4 / 1 |
| create_sector / delete_sector / rename_sector / clear_sector | SetMsgToMain | 11 / 12 / 13 / 14 |
| refresh_cache / refresh_kline | GetTdxDataStr | 16 / 17 |

原生操作接口不存在时，在调用 SDK 方法前拒绝执行。已知 SDK 在进入原生函数前拒绝参数或终止时，不会被误报为已提交后的未知状态；对于没有原生证据的替代后端，服务层在调用后发生异常时保守标记未知。

账户查询 JSON 保留 account_id、logged_in 和 message，并新增 state：ready 表示取得可用句柄，unavailable 表示明确无可用账户。“未登录或不存在”不等价于证明用户刚刚退出登录。无法确认时退出非零，输出 ACCOUNT_STATE_UNKNOWN，不输出确定的 logged_in=false。ErrorId=9 的解释仅适用于上述账户查询协议，不能复用于其他方法或无验证的新供应商版本。

错误 JSON 保留 success、error、error_type、error_code、retryable，取得原生证据时附加 details：method、native_function、request_type、failure_kind、sdk_error_id、dispatch_started。dispatch_started 只表示已进入可能发送操作的原生边界，不证明服务端已经执行。details 不保存账户号、请求参数、委托内容或原始响应正文；供应商自身的诊断仍可能进入 stderr。

TRADE_STATE_UNKNOWN 与 OPERATION_STATE_UNKNOWN 的 retryable 为 false。读取超时为 SDK_TIMEOUT、retryable=true，表示调用方可以在核对条件后决定重试；CLI 本身不自动重试。未知委托必须先查询核对；查询本身失败时，不能推断该委托不存在。

除权除息 JSON 通过统一序列化器保留 SDK 的 Date 索引，日期使用 ISO 格式。K 线、财务等其他接口继续保留各自已有字段结构。

当前仍没有跨进程幂等提交、持久化操作日志、账户并发协调或可抢占的整体 deadline。真实集成测试只读取客户端；交易、撤单、板块和 GUI 写操作使用内存原生函数替身。协议兼容性仅针对已验证本机 SDK，其他版本须补充兼容验证。

## 批量读取完整性与日期模式

market get、financial get、financial by-date 使用单次调用返回的 metadata，不通过全局 last_result 保存批量错误。适配器在原生回执和重组解析边界提取 Errors 字典，保留逐证券 code/message（消息最长 500 字符）；后续页面成功不能抹掉先前的逐项错误。此处错误消息属于读取诊断，不包含交易请求参数或原始回执正文。

market get 默认尾部数量 60；提供 --start 后切换为区间模式，内部 count=-1。显式 --count 与 --start 同时出现时，在连接 SDK 前返回 INVALID_PARAMETER。八位 --end 展开至 23:59:59；返回日期必须有效且位于请求边界内。关闭供应商 fill_data，不用前值补齐行情缺口。

完整结果继续返回既有证券字典。K 线可用记录至少具有有限 close 值，全空列不算可用证券；财务按大小写不敏感的请求字段逐值核对，None/NA/NaT/非有限数值/空串/-- 不算可用，零为有效数据。财务字段空列表在连接前失败。没有任何请求字段值的行被排除，原数据不会被原地修改。财务的有意义索引保留，默认 RangeIndex 不额外生成人为 index 字段。

默认不完整结果退出 1，error_code=DATA_INCOMPLETE、retryable=false，details 包含完整性说明，data 保留已验证的可用部分。--allow-partial 仅在至少有部分可用数据时允许退出 0；该选项的成功输出始终为 JSON 信封，即使没有 --json。完整结果使用该选项时也使用同一信封，partial=false。

| 信封或 details 字段 | 含义 |
| --- | --- |
| schema_version / success / data | 成功信封版本 1、成功标志、可用数据；错误输出沿用既有错误顶层结构 |
| partial | 是否发现缺失证券/字段/值、被丢弃记录、额外证券或 SDK 逐项错误 |
| requested_stocks / available_stocks / missing_stocks | 请求顺序及实际可用/不可用证券；键存在不证明有数据 |
| missing_fields / missing_values | 全部不可用的请求字段，以及各请求字段缺失值数量；K 线最低必要字段为 close |
| record_counts / dropped_records | 各证券可用记录数及因必要数据缺失排除的记录数 |
| errors / unexpected_stocks / warnings | 原生逐项错误、响应中额外证券、可读说明 |

严格模式在导出前验证，失败不替换目标文件。--allow-partial 导出只写可用记录，控制台信封附带 output/format/records 和完整性元数据；CSV/XLSX/JSON 数据文件保持记录结构，元数据需从控制台信封保存。

上述校验不证明完整交易日历覆盖、证券不存在、字段含义正确或客户端已同步全部财务缓存；不能把不可用数据归因为某个未经验证的根因。financial trading、公式及其他查询尚未统一套用此信封。
