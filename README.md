# TDX TQ SDK CLI

通达信 TQ SDK (`tqcenter.py`) 的 Windows 命令行工具，围绕本机真实 `tqcenter.py` 与底层 DLL 能力提供行情读取、实时订阅流 (`subscribe_hq`)、股票/基金/可转债查询、本地码表与板块关联高速检索、板块管理、技术公式、客户端多表看板投屏 (`print_to_tdx`) / 文件投递 / 回测曲线 / 预警联动与委托接口封装，内置双代际 SDK 能力画像、零侵入缺陷修补、批量完整性校验与结构化 JSON/文件导出契约。长命令 `cli-anything-tdx-tq` 与短命令 `tdx-tq` 使用同一入口。

> [!IMPORTANT]
> **免责声明**：本项目仅作为通达信量化接口（TQ SDK）的工程封装与开源技术分享，所有功能与数据主要用于量化行情读取、数据分析与策略研究交流，**绝不构成任何投资建议、买卖推荐或交易引导**。股市有风险，入市需谨慎。开发者不对任何依据本工具产生的投资决策、交易行为或资金损益承担任何责任。

## 1. 安装与环境配置

需要 Windows、Python 3.12/3.13。读取实时/客户端数据前需启动并登录支持 TQ 的通达信客户端，确认安装目录中存在 `PYPlugins/sys/tqcenter.py` 或 `PYPlugins/user/tqcenter.py` 及配套 `TPythClient.dll`；支持自动探测本机常见通达信安装目录（如 `D:\TDX64`、`D:\tdx`）及正在运行的 `tdxw.exe` 客户端路径。

~~~powershell
uv sync
uv run tdx-tq --version
uv run tdx-tq status --json
uv run tdx-tq config detect --json
~~~

自定义路径可通过全局 `--tdx-path`、环境变量 `TDX_PATH`、`config detect --apply` 或 `config set` 指定，优先级依次为：命令行 `--tdx-path` > `TDX_PATH` > 配置文件 > 默认值（`D:\tdx`）：

~~~powershell
uv run tdx-tq config detect --apply --json
uv run tdx-tq config set --tdx-path D:\TDX64 --json
uv run tdx-tq config show --json
uv run tdx-tq --tdx-path D:\TDX64 market snapshot -s 600519.SH --json
~~~

## 2. 证券代码与本机校准分类字典

证券代码须带市场后缀（如 `600519.SH`、`00700.HK`、`IF2612.CFF`）。多代码参数支持逗号分隔、空白分隔及 `-` 标准输入（按行文本或 JSON 字符串数组）。针对本机 `tqcenter.py` (`TPythClient.dll`) 校准后的 `stock list -m` 常用分类代码（可通过 `tdx-tq stock list --categories` 离线查看完整字典）：

| 品种与功能分类 | 市场后缀示例 | 示例标的 | 本机 `stock list -m` 校准分类代码 |
|---|---|---|---|
| 客户端自选与持仓 | `.SH` `.SZ` `.BJ` | `600519.SH`、`000001.SZ` | **`0`=客户端自选股**，**`1`=客户端持仓股** |
| 股票 (沪深京主板/双创) | `.SH` `.SZ` `.BJ` | `600519.SH`、`300750.SZ`、`830799.BJ` | `5`=所有A股，`50`=沪深A股，`7`=上证主板，`8`=深证主板，`51`=创业板，`52`=科创板，`53`=北交所，`21`=含H股，`22`=含可转债，`49`=金融类企业 |
| 核心宽基指数成分股 | `.SH` `.SZ` `.CSI` `.CNI` | `000300.SH`、`000905.CSI` | `6`=上证指数成份股，`9`=重点指数，`23`=沪深300(默认)，`24`=中证500，`25`=中证1000，`26`=国证2000，`27`=中证2000，`28`=中证A500 |
| 系统与研究行业板块 | `.SH` | `880381.SH`、`880515.SH` | `10`=所有板块指数，`11`=缺省行业，`12`=概念，`13`=风格，`14`=地区，`15`=行业+概念，`16`~`18`=研究行业一至三级 |
| 基金与可转债 | `.SH` `.SZ` | `510300.SH`、`113050.SH` | `30`=REITs，`31`=ETF基金，`32`=可转债，`33`=LOF基金，`34`=所有可交易基金，`35`=所有沪深基金，`36`=T+0基金 |
| 期货、港股与美股 | `.CFF` `.SHF` `.DCE` `.CZC` `.HK` `.US` | `IF2612.CFF`、`00700.HK`、`AAPL.US` | `101`=国内期货，`102`=港股，`103`=美股 |

> **量纲说明（依据本机 `tqcenter.py` / `tdxdata_test.py`）**：`market get` K 线开高低收单位为**元**、成交量为**手**、成交额为**万元**；`market snapshot` 总成交额为**万元**；`stock info` 股本、资产、负债、利润与现金流字段单位均为**万**。

## 3. 完整命令与 SDK 数据接口矩阵（10 组 59 个叶子命令）

| 命令组 | 命令 |
|---|---|
| 全局 | init、close、status |
| daemon | start、stop、status、serve |
| market | get、snapshot、watch、divid、zdt、exday、pv、refresh |
| stock | info、more、list、calendar、search、gb、kzz、relation、ipo、etf |
| sector | list、stocks、user、create、delete、rename、clear、update-stocks |
| financial | get、by-date、trading、one |
| formula | list、info、zb、xg、exp、eval |
| client | link、warn、msg、file、bt、panel、download |
| trade | account、asset、positions、orders、order、cancel |
| config | show、set、detect |

### 3.1 全局、常驻守护进程与配置 (`全局` / `daemon` / `config` — 10 个命令)

全局选项支持 `--tdx-path <DIR>`、`--debug`、`--timeout <秒>`（可抢占式端到端截止时间；读超时返回 `SDK_TIMEOUT` 且 `retryable=true`，交易/写操作超时返回 `TRADE_STATE_UNKNOWN` / `OPERATION_STATE_UNKNOWN` 且绝不自动重试）以及 `--no-daemon`（跳过本地常驻守护进程）。

| CLI 命令 | 底层接口 / 数据源 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `status` | *(离线诊断)* | `--json` | 检查会话状态、守护进程状态、配置路径、自动发现的通达信目录、运行中的 `tdxw.exe` 路径一致性及 SDK 能力画像，不建立客户端连接 |
| `init` | `tq.initialize` | `--json` | 在当前进程显式建立 TQ 连接并返回 `run_id` |
| `close` | `tq.close` | `--json` | 关闭当前进程连接并核验底层 `_initialized` 复位状态 |
| `daemon start` | *(本地 IPC 守护)* | `--idle-timeout <1800>` `--port <0>` `--json` | 启动后台单线程长连接守护进程，持久复用同一 `run_id` 会话消除每次命令冷启动与 DLL 握手开销，后续 CLI 命令自动走毫秒级本地令牌转发 |
| `daemon stop` | *(本地 IPC 守护)* | `--json` | 安全通知守护进程释放 `tq.close()` 连接并清理 `daemon.json` |
| `daemon status` | *(本地 IPC 守护)* | `--json` | 查询后台守护进程存活状态、已服务请求数、会话 `run_id` 与空闲时长（自动清理陈旧状态文件） |
| `daemon serve` | *(本地 IPC 守护)* | `--idle-timeout <1800>` `--port <0>` `--json` | 在前台运行守护服务主循环（供调试或外部进程管理器托管） |
| `config show` | *(本地配置)* | `--json` | 显示当前通达信路径来源、目录有效性及自动发现的候选安装路径 |
| `config set` | *(本地配置)* | `--tdx-path <DIR>` `--json` | 校验并原子保存通达信安装目录至 `~/.tdx_tq/config.json` |
| `config detect` | *(离线探测)* | `--apply` `--json` | 自动扫描常见通达信目录与运行中的 `tdxw.exe` 进程路径，加 `--apply` 原子写入最佳匹配路径 |

### 3.2 行情数据与实时订阅 (`market` — 8 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `market get` | `get_market_data` | `-s <stocks>` `-p <period>` `-c <count>` `--start` `--end` `-d <none/front/back>` `-f <fields>` `--chunk-size <n>` `--pivot <field>` `--fill <none/ffill>` `--allow-partial` `-o <path>` `--json` | 获取历史/盘中 K 线；支持 `--chunk-size 100` 全市场自动分块拉取（向 `stderr` 输出进度且容忍停牌空分块）、`--pivot close` 宽表矩阵，以及原子导出 `.csv/.json/.jsonl/.xlsx/.parquet` |
| `market snapshot` | `get_market_snapshot` | `-s <stocks>` `-f <fields>` `--allow-partial` `-o <path>` `--json` | 获取单只或多只证券实时五档盘口快照；多标的批量支持 `--allow-partial` 完整性信封与 `-o` 导出 `.csv/.json/.jsonl/.xlsx/.parquet` |
| `market watch` | `subscribe_hq` + `unsubscribe_hq` | `-s <stocks>` `-f <fields>` `--with-snapshot/--no-snapshot` `--debounce-ms <500>` `--duration <sec>` `--max-events <n>` `--jsonl` `--json` | 实时行情更新流（最多 100 只标的）；主线程队列解耦 C 回调与按标的防抖，支持 `--jsonl` 流式输出 NDJSON，退出必调 `unsubscribe_hq` 清理 |
| `market divid` | `get_divid_factors` | `-s <stock>` `--start` `--end` `--json` | 获取历史除权除息送转配与分红数据，保留真实 `Date` 索引 |
| `market refresh` | `refresh_cache` / `refresh_kline` | `-m <AG/HK/US/QH/ZS>` `--force` `-s <stocks>` `-p <1m/5m/1d>` `--json` | 刷新客户端全推行情缓存或指定证券的历史 K 线缓存（K 线缓存仅支持 `1m/5m/1d`） |
| `market zdt` | `get_zdt_data` + `get_more_info` | `-s <stocks>` `--json` | *(需 `sys_unified` SDK)* 当日涨跌停明细与客户端近期涨跌停日期；`user_tpythclient` 下明确返回 `UNSUPPORTED_SDK_CAPABILITY` |
| `market exday` | `get_exday_data` | `-s <stock>` `-c <count>` `--json` | *(需 `sys_unified` SDK)* 日线成交与委托统计 |
| `market pv` | `get_pricevol` | `-s <stocks>` `--json` | *(需 `sys_unified` SDK)* 分价表统计 |

### 3.3 证券、本地缓存检索、可转债与日历 (`stock` — 10 个命令)

| CLI 命令 | 底层接口 / 数据源 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `stock info` | `get_stock_info` | `-s <stock>` `-f <fields>` `--json` | 获取证券免费基础属性与股本/资产摘要字典（单位：万），支持 `-f` 字段筛选 |
| `stock list` | `get_stock_list` | `-m <code='23'>` `-t <0/1>` `--categories` `--json` | 获取分类证券列表（`-m 0` 自选股、`-m 1` 持仓股、`-m 5` 全部A股）；`--categories` 离线打印完整分类表 |
| `stock calendar` | `get_trading_dates` | `-m <SH/SZ/AG>` `--start` `--end` `-c <count=20>` `--json` | 查询真实交易日历（需客户端已下载上证指数 `999999` 盘后日线，自动兼容 `SH` 市场） |
| `stock search` | `get_match_stkinfo` / 本地 `.tnf` 码表 | `-k <keyword>` `--limit <50>` `--local` `--json` | 支持按**代码、中文简称或拼音首字母**（如 `GZMT`）检索证券；`user_tpythclient` 或 `--local` 下直接毫秒级解析本地 `T0002/hq_cache/*.tnf` |
| `stock relation` | `get_relation` / 本地板块缓存 | `-s <stock>` `--local` `--json` | 查询个股所属行业、研究行业、概念、风格与指数板块及成分股数；`user_tpythclient` 或 `--local` 下精准解析本地 `tdxhy.cfg` / `tdxzs3.cfg` / `infoharbor_block.dat` |
| `stock kzz` | `get_cb_info` / `get_kzz_info` | `-s <stock>` `-f <fields>` `--json` | 获取可转债基本属性与转股条款，兼容双代际接口命名与 `-f` 不区分大小写筛选 |
| `stock ipo` | `get_ipo_info` | `-t <0/1/2>` `-d <0/1>` `--json` | 获取今日或未来新股、新发可转债申购信息 |
| `stock more` | `get_more_info` | `-s <stock>` `-f <fields>` `--json` | *(需 `sys_unified` SDK)* 扩展盘口与估值指标 |
| `stock gb` | `get_gb_info` / `*_by_date` | `-s <stock>` `-c <count>` `-d <dates>` `--start` `--end` `--json` | *(需 `sys_unified` SDK)* 历史股本序列（当前最新股本可直接用免费的 `stock info -f J_zgb,ActiveCapital`） |
| `stock etf` | `get_trackzs_etf_info` | `-z/--index <zs_code>` `--json` | *(需 `sys_unified` SDK)* 指数跟踪 ETF 列表 |

### 3.4 板块查询与自定义板块管理 (`sector` — 8 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `sector list` | `get_sector_list` / `get_stock_list` | `-c <all/industry/concept/style/region/industry-concept/research1..3/ext>` `-t <0/1>` `--json` | 获取全部或指定类别系统板块列表 |
| `sector stocks` | `get_stock_list_in_sector` | `-s <block>` `--block-type <0/1/2>` `-t <0/1>` `--json` | 获取系统板块代码/中文名称（`0`）或自定义板块简称（`--block-type 1`）成分股 |
| `sector user` | `get_user_sector` | `--json` | 获取当前客户端自定义板块列表 |
| `sector create` | `create_sector` | `-c <code>` `-n <name>` `--json` | *(写操作)* 创建自定义板块 |
| `sector update-stocks` | `send_user_block` | `-c <code>` / `--temp` `-s <stocks>` / `--clear` `--show/--no-show` `--json` | *(写操作)* 向自定义板块或客户端**临时条件股**（`--temp`）推送股票列表，支持 `--clear` 清空与 `--no-show` 静默更新 |
| `sector rename` | `rename_sector` | `-c <code>` `-n <new_name>` `--json` | *(写操作)* 重命名自定义板块显示名称 |
| `sector clear` | `clear_sector` | `-c <code>` `--json` | *(写操作)* 清空自定义板块成分股 |
| `sector delete` | `delete_sector` | `-c <code>` `--json` | *(写操作)* 删除自定义板块 |

### 3.5 专业财务与市场交易统计 (`financial` — 4 个命令，需对应数据权限)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `financial get` | `get_financial_data` | `-s <stocks>` `-f <FN_fields>` `--start` `--end` `--report-type` `--allow-partial` `--json` | 按日期区间获取专业财务指标并校验字段可用性 |
| `financial by-date` | `get_financial_data_by_date` | `-s <stocks>` `-f <FN_fields>` `-y <year>` `-m <mmdd>` `--allow-partial` `--json` | 按报告期获取专业财务指标 |
| `financial one` | `get_gp_one_data` | `-s <stocks>` `-f <GO_fields>` `--allow-partial` `--json` | 获取最新单条一致预期/解禁/机构持仓指标 |
| `financial trading` | `get_gpjy/bkjy/scjy_value*` | `-t <stock/sector/market>` `--target <codes>` `-f <GP/BK/SC_fields>` `--start` `--end` `-y` `-m` `--allow-partial` `--json` | 获取个股、板块或全市场交易统计数据 |

### 3.6 技术指标、条件选股、专家系统与本地向量化公式闭环 (`formula` — 6 个命令)

支持双引擎模式：在无 `TdxFuncMain` 的客户端（如 `user_tpythclient`）上，可通过 **`formula eval`** 或为既有命令追加 **`--local`** 启用零 `eval` 纯本地向量化通达信公式编译器（内置 33 个算子与 30 个经典指标/选股/专家公式，标注 `engine="local_vectorized"`）；不加 `--local` 调用原生接口时严格返回 `UNSUPPORTED_SDK_CAPABILITY`，绝不静默伪造。

| CLI 命令 | 底层接口 / 计算引擎 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `formula eval` | `core/local_formula.py` + `get_market_data` | `-e <expr>` / `--formula-file <path>` / `-n <name>` `-t <0/1/2>` `-a <args>` `-s <stocks>` / `--sector <block>` / `--market-list <code_id>` `-p <period>` `-c <count>` `--start` `--end` `-d <none/front/back>` `--chunk-size` `--select` `--push-sector <code>` `--push-temp` `--push-panel <sp>` `--push-warn` `-o <path>` `--json` | **全链路本地公式计算与选股闭环**：支持通达信多语句语法（`{注释}`、`:=` 中间变量、`:` 输出线及绘图修饰符剥离），支持对股票池/板块/自选股拉取真实 K 线高速算因子或条件选股，并可一键联动写入自定义板块/临时条件股、发布 `.sp` 看板、推送买入预警与导出 `.csv/.json/.jsonl/.xlsx/.parquet` |
| `formula list` | `formula_get_all` / `--local` 内置库 | `-t <0/1/2>` `--local` `--json` | 枚举公式列表；`--local` 离线列出全部 30 个内置指标(`0`)、选股(`1`)与专家系统(`2`)公式 |
| `formula info` | `formula_get_info` / `--local` 内置库 | `-c <name>` `-t <0/1/2>` `--local` `--json` | 查询公式参数定义、默认值与通达信公式源码（`--local` 离线可用） |
| `formula zb` | `formula_zb*` / `--local` 向量化引擎 | `-n <name>` `-a <args>` `-s <stocks>` `-p <period>` `-c <count>` `--return-count` `--return-date` `--xsflag` `--start` `--end` `--dividend` `--local` `--chunk-size` `--allow-partial` `--json` | 计算技术指标（如 `MA`、`MACD`、`KDJ`、`RSI`、`BOLL`、`ATR`、`CCI`） |
| `formula xg` | `formula_xg*` / `--local` 向量化引擎 | 同上 | 运行条件选股公式（如 `MA_CROSS`、`MACD_CROSS`、`KDJ_CROSS`、`VOL_BREAK`、`UPN`） |
| `formula exp` | `formula_exp*` / `--local` 向量化引擎 | 同上 | 运行专家交易系统公式（输出 `ENTERLONG`、`EXITLONG` 与 `SIGNAL` 多空信号） |

### 3.7 客户端桌面联动、看板投屏与数据包解析 (`client` — 7 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `client panel` | `print_to_tdx` | `-i <csv/json/- ...>` `-n <sp_name>` `--layout <vertical/horizontal>` `--titles` `--heights` `--xml-name` `--json` | 将一组或多组因子/结果表编译为 `.xml` + `.jsn` + `.sp` 定制看盘面板写入 `T0002/pad`，隔离临时目录不污染当前工作区 |
| `client warn` | `send_warn` | `-s <stocks>` `-m <reasons>` `-p <prices>` `--close` `--volume` `--bs-flag <0/B/1/S/2>` `--warn-type` `--time` `--json` | 向通达信“TQ策略信号”面板推送单条或批量买卖信号弹窗（自动执行 50 字节 GBK 安全截断） |
| `client msg` | `send_message` | `-m <msg>` `--json` | 向通达信 TQ 策略管理器输出日志消息（支持 `|` 分条显示） |
| `client file` | `send_file` | `-f <path>` `--stage/--no-stage` `--json` | 向客户端 TQ 策略面板发送结果文件供打开（`--stage` 自动复制到 `PYPlugins/file` 并传相对文件名） |
| `client bt` | `send_bt_data` | `-s <stock>` `--times <t1,t2>` `--data <rows>` `--count` `--json` | 向客户端 TQ 策略面板投递时间序列回测指标（每时点校验 1~16 个数值） |
| `client download` | `download_file` + 本地解析器 | `-s <stock>` `-t <1..7>` `-d <date>` `--parse` `--parse-only` `--json` | 下载盘后静态文件至 `PYPlugins/data`（`1`=十大股东，`2`=ETF申赎清单）；`--parse` 或离线 `--parse-only` 直接解析出结构化股东/申赎 JSON |
| `client link` | `exec_to_tdx` | `-s <stock>` `--json` | *(需 `sys_unified` SDK)* 客户端窗口跳转指定证券 |

### 3.8 委托与账户接口 (`trade` — 6 个命令，仅供协议对齐与安全预演)

> **安全提示**：本项目不做实盘交易指引。`trade order` 默认仅执行离线参数校验与预演，必须显式传入 `--confirm` 才调用底层接口；本机 `user/tqcenter.py` 仅包含 `SetNewOrder`（官方源码标注“暂无实际功能”），不含撤单与持仓查询（持仓代码列表可通过免费的 `stock list -m 1` 获取）。

| CLI 命令 | 底层接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `trade order` | `order_stock` (`SetNewOrder` / `GetOrderStr`) | `-s <stock>` `-t <buy/sell>` `-v <vol>` `-p <price>` `--price-type <0/1>` `-a <int_id>` `--account <str_id>` `--strategy-name` `--remark` `--confirm` `--json` | 默认离线安全预演；兼容 v1 字符串账号签名与 v2 整数句柄签名 |
| `trade account` | `stock_account` | `--json` | *(需 `GetOrderStr`)* 查询当前登录证券账户状态 |
| `trade asset` | `query_stock_asset` | `-a <account_id>` `--json` | *(需 `GetOrderStr`)* 查询账户资金资产 |
| `trade positions` | `query_stock_positions` | `-a <account_id>` `--json` | *(需 `GetOrderStr`)* 查询账户持仓明细 |
| `trade orders` | `query_stock_orders` | `-a <account_id>` `-s <stock>` `--cancelable-only` `--json` | *(需 `GetOrderStr`)* 查询当日委托记录 |
| `trade cancel` | `cancel_order_stock` | `-o <order_id>` `-s <stock>` `-a <id>` `--json` | *(需 `GetOrderStr`)* 撤销指定委托 |

## 4. 典型场景示例（非付费全链路可运行）

~~~powershell
# 1. 环境诊断与自动路径配置
uv run tdx-tq status --json
uv run tdx-tq config detect --json
uv run tdx-tq stock list --categories --json

# 2. 离线高速检索：按拼音首字母(GZMT)或名称搜索代码，以及解析本地个股所属行业/概念板块
uv run tdx-tq stock search -k GZMT --local --json
uv run tdx-tq stock relation -s 600519.SH --local --json

# 3. 直接读取通达信客户端当前自选股(-m 0)与持仓股(-m 1)
uv run tdx-tq stock list -m 0 --json
uv run tdx-tq stock list -m 1 --json

# 4. K 线获取、分块拉取、JSONL 导出与 [日期 x 股票] 宽表矩阵导出 (对标 tq.price_df)
uv run tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 -f open,high,low,close,volume --json
uv run tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 --chunk-size 100 --allow-partial -o kline_batch.jsonl --json
uv run tdx-tq market get -s 600519.SH,000001.SZ --start 20261001 --end 20261020 --pivot close --fill ffill -o close_matrix.csv --json

# 5. 常驻会话守护进程 (毫秒级复用同一 run_id 连接) 与端到端超时保护 (--timeout)
uv run tdx-tq daemon start --idle-timeout 1800 --json
uv run tdx-tq --timeout 10 market snapshot -s 600519.SH,000001.SZ --allow-partial -o snapshots.jsonl --json
uv run tdx-tq daemon status --json
uv run tdx-tq daemon stop --json

# 6. 实时行情订阅流 (subscribe_hq)：按 500ms 防抖推送 NDJSON 快照流，采集 5 条后自动退订退出
uv run tdx-tq market watch -s 600519.SH,000001.SZ -f Now,Volume,Buyp,Sellp --debounce-ms 500 --max-events 5 --jsonl

# 6. 免费基础数据：证券股本/资产摘要、可转债条款、历史分红送配与新股新债申购
uv run tdx-tq stock info -s 600519.SH -f J_zgb,ActiveCapital --json
uv run tdx-tq stock kzz -s 113050.SH --json
uv run tdx-tq market divid -s 600519.SH --start 20240101 --end 20251231 --json
uv run tdx-tq stock ipo -t 0 -d 0 --json

# 7. 下载并直接结构化解析十大股东数据 (PYPlugins/data/gp*.json)
uv run tdx-tq client download -s 600519.SH -t 1 -d 20251231 --parse --json
uv run tdx-tq client download -s 600519.SH -t 1 --parse-only --json

# 8. 本地向量化通达信公式计算与全链路选股闭环 (无需 C 公式引擎或付费权限)
uv run tdx-tq formula list -t 1 --local --json
uv run tdx-tq formula info -c MACD -t 0 --local --json
uv run tdx-tq formula zb -n MACD -a 12,26,9 -s 600519.SH,000001.SZ -c 60 --return-count 3 --return-date --dividend 1 --local --json
uv run tdx-tq formula eval -e "DIF:=EMA(C,12)-EMA(C,26); DEA:=EMA(DIF,9); CROSS(DIF,DEA) AND V>REF(MA(V,5),1)*1.2" --market-list 23 -c 60 --chunk-size 100 --select --allow-partial --push-temp --no-show -o macd_hits.jsonl --json

# 9. 策略结果联动通达信桌面：推送临时条件股、多表定制看板(.sp)、买卖信号预警与回测序列
uv run tdx-tq sector create -c AIPOOL -n "AI观察池" --json
uv run tdx-tq sector update-stocks -c AIPOOL -s 600519.SH,000001.SZ --json
uv run tdx-tq sector update-stocks --temp -s 600519.SH,000001.SZ --no-show --json
uv run tdx-tq client panel -i close_matrix.csv -n my_factor_pad --layout vertical --titles "收盘价矩阵" --json
uv run tdx-tq client warn -s 600519.SH -m "均线金叉放量" -p 1550.0 --bs-flag B --volume 1200 --json
uv run tdx-tq client bt -s 600519.SH --times 20261020150000,20261021150000 --data "1,100.5;0,102.3" --json
~~~

## 5. 执行契约与必要说明

- **真实能力契约（绝不静默造假）**：加载器根据本机 `tqcenter.py` 与 DLL 导出符号自动识别 `sdk_profile`（如 `user_tpythclient` 或 `sys_unified`）。当调用当前客户端 SDK 未提供的原生接口（且无法由通达信本地缓存精确推导）时，严格返回退出码 `1` 与 `error_code="UNSUPPORTED_SDK_CAPABILITY"`，绝不伪造选股列表或盘口指标。
- **零侵入热修补**：不对通达信安装目录中的 `tqcenter.py` 做任何磁盘修改；在进程内自动静默 `get_market_snapshot` 原生 `json:` 调试刷屏、放开多市场证券代码（港股/美股/期货）格式校验并阻止参数异常误调 `tq.close()`、修复 `refresh_cache` 空指针 `UnboundLocalError` 及 `print_to_tdx` 当前工作目录污染。
- **1 分钟线 (`1m`) 与交易日历前置条件**：历史 `1m` K 线需先在通达信客户端【系统 -> 盘后数据下载】勾选下载对应 1 分钟数据；`stock calendar` 依赖客户端已下载上证指数（`999999`）盘后日线数据。
- **批量完整性门禁 (`DATA_INCOMPLETE`)**：`market get` 等批量读取默认关闭前值填充（`fill_data=False`），遇到无数据标的或无效零日期时退出码为 `1` (`DATA_INCOMPLETE`)；显式传入 `--allow-partial` 可获取 `schema_version=1` 的完整性信封。

## 6. 输出格式、退出码与测试

所有叶子命令支持 `--json`（`market watch` 另支持流式 `--jsonl`），保证 `stdout` 仅输出合法 JSON，SDK 诊断与日志均定向至 `stderr`。退出码：**`0`** 成功、**`2`** 参数校验错误 (`INVALID_PARAMETER`)、**`1`** 操作/SDK/能力不支持或数据不完整错误、**`130`** 中止。

~~~powershell
uv sync --extra test
uv run --extra test pytest -m "not live" -q
~~~

真实客户端只读回归需显式添加 `--live -m live`。详细架构与协议契约见 [开发文档索引](docs/README.md)。许可为 MIT。
