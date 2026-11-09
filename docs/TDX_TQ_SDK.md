# TDX TQ CLI 架构与执行契约

当前目标平台为 Windows，Python 3.12/3.13。源码版本由 `cli_anything.tdx_tq.__version__` 提供，构建元数据与 CLI 共用该来源。

| 层 | 位置 | 职责 |
|---|---|---|
| 命令 | `tdx_tq_cli.py` | Click 参数校验、双路径分发（原生 SDK / 本地缓存）、文本/JSON/NDJSON 呈现 |
| 行情与宽表服务 | `core/market.py` | `MarketRequest` 数量/区间模式、校准分类字典 `STOCK_LIST_CATEGORIES`、向量化 K 线重排与 `pivot_kline_data` 宽表 |
| 实时订阅流 | `core/watch.py` | `subscribe_hq` / `unsubscribe_hq` 队列解耦、100 标的上限校验、按标的防抖节流与退出退订保障 |
| 客户端桌面联动 | `core/client_ui.py` | `print_to_tdx` 临时目录隔离面板生成（`.xml`/`.jsn`/`.sp`）、`send_file`、`send_bt_data` 与完整 8 列表 `send_warn` |
| 本地缓存与下载解析 | `core/local_cache.py` | 离线解析 `T0002/hq_cache/*.tnf`（含拼音首字母）、`tdxhy.cfg`/`tdxzs3.cfg`/`infoharbor_block.dat` 板块关系、`PYPlugins/data` 股东/申赎文件及 `tdxw.exe` 进程检测 |
| 本地通达信公式编译器 | `core/local_formula.py` | 零 `eval` 词法/递归下降 AST 编译器、33 个向量化算子（含真实递归 `SMA`/`DMA`/`BARSLAST`）与 30 个内置指标/选股/专家公式 |
| 交易服务 | `core/trading.py` | `OrderRequest` 校验、双代际 `order_stock`（v1 `SetNewOrder` 字符串账号 / v2 `GetOrderStr` 整数句柄）签名桥接及未知状态保护 |
| 数据完整性 | `core/quality.py` | 证券实际可用性、请求字段逐值核验、严格/部分结果契约 |
| SDK 适配与能力画像 | `core/sdk.py`、`core/protocol.py` | 双代际加载（`sys` / `user`）、`sdk_profile` 能力探测、零侵入原生缺陷修补、原生回执证据与返回契约 |
| 会话 | `core/session.py` | 显式生命周期状态机（`uninitialized`/`initializing`/`ready`/`closing`/`closed`/`failed`）、关闭确认核验与交易日历市场标准化 |
| 配置 | `core/config.py` | 优先级、文件系统异常安全隔离、本机通达信安装路径自动发现与原子持久化 |
| 输出/导出 | `utils/output.py`、`utils/cli.py`、`core/export.py` | 递归严格 JSON、退出码和原子文件输出（`.csv`/`.json`/`.jsonl`/`.xlsx`/`.parquet`） |
| 常驻守护与超时控制 | `core/daemon.py` | 本机 Loopback 令牌鉴权长连接会话复用、串行 DLL 调度、空闲自动回收与读写分类的可抢占 `--timeout` 截止时间 |

## 双代际 SDK 识别与零侵入适配

加载器按 `<tdx_path>/PYPlugins/sys/tqcenter.py` $\rightarrow$ `<tdx_path>/PYPlugins/user/tqcenter.py` 顺序解析目标文件，拒绝在同一进程中混用不同目录的 SDK，且**绝不修改磁盘上的原始 `tqcenter.py`**。

`TqAdapter` 根据底层 `dll` 导出符号与 `tq` 方法签名自动识别 `sdk_profile`：
- **`user_tpythclient`（如 `D:\TDX64\PYPlugins\user\tqcenter.py` + `TPythClient.dll`）**：包含 23 个原生 C 函数，支持 K 线/快照/复权/列表/日历/可转债/IPO、板块增删改查、`subscribe_hq` 实时订阅、`print_to_tdx` 面板导出、`send_message`/`send_file`/`send_warn`/`send_bt_data`、`refresh_cache`/`refresh_kline`、`download_file` 与 `SetNewOrder`；不包含 `TdxFuncMain`（公式引擎）和 `GetOrderStr`（资产/持仓/委托查询与撤单）。
- **`sys_unified`（统一协议 `PYPlugins\sys\tqcenter.py`）**：基于 `GetTdxDataStr`、`TdxFuncMain`、`GetOrderStr`、`SetMsgToMain` 提供扩展行情、C 公式计算与交易查询接口。

对于当前加载 SDK 未提供的可选方法，适配器统一抛出 `TdxUnsupportedCapabilityError`（`error_code="UNSUPPORTED_SDK_CAPABILITY"`），**严禁使用假数据或硬编码占位值冒充成功结果**。`stock search` 与 `stock relation` 在 SDK 未提供对应方法（或显式传入 `--local`）时，使用 `core/local_cache.py` 直接读取通达信真实本地缓存文件并标注 `source="local_tdx_cache"`。

针对 `user/tqcenter.py` 已核实的原生问题，进程内修补仅做最小必要修正：
1. 静默 `get_market_snapshot` 残留的 `print("json:", result_str)` 调试输出（`--debug` 时保留）；
2. 将 `check_stock_code_format` / `_check_stock_code_format_batch` 替换为不主动调用 `tq.close()` 的多市场代码校验器；
3. 拦截 `refresh_cache` 空指针与 `unsubscribe_hq` 异常返回纯中文字符串的情况，统一转为结构化异常；
4. 在 `publish_panel_to_tdx` 中通过独立临时工作目录包裹 `print_to_tdx`，避免临时 `.xml`/`.jsn` 污染调用方当前目录。

会话管理维护显式状态机：初始化中途失败会尽力释放底层句柄；`close()` 后若检测到供应商捕获异常并保留 `raw._initialized=True`，立即标记 `failed` 并抛出 `TdxInitError("底层 SDK 未确认关闭连接")`，不输出虚假成功。完整协议与返回值契约见 [SDK_RESULT_CONTRACTS.md](SDK_RESULT_CONTRACTS.md)。
