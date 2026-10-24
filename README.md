# TDX TQ SDK CLI

通达信 TQ SDK (`tqcenter.py`) 的 Windows 命令行工具，100% 覆盖本机 TQ SDK 的行情、股票、基金/ETF、指数、板块、专业财务、交易统计、技术指标/选股/专家公式、客户端联动及实盘/模拟交易接口，并在原生 SDK 之上提供零侵入缺陷热修补、批量完整性校验与结构化 JSON/文件导出契约。长命令 `cli-anything-tdx-tq` 与短命令 `tdx-tq` 使用同一入口。

## 1. 安装与环境配置

需要 Windows、Python 3.12/3.13。读取数据前需启动并登录支持 TQ 的通达信客户端，确认安装目录中存在 `PYPlugins/sys/tqcenter.py` 及配套 `TPythClient.dll` / `TPyth.dll`；默认路径为 `D:\tdx`。

~~~powershell
uv sync --locked
uv run --locked tdx-tq --version
uv run --locked tdx-tq status --json
~~~

自定义路径可通过全局 `--tdx-path`、环境变量 `TDX_PATH` 或配置文件指定，优先级依次降低：

~~~powershell
uv run --locked tdx-tq config set --tdx-path D:\tdx --json
uv run --locked tdx-tq config show --json
uv run --locked tdx-tq --tdx-path D:\tdx market snapshot -s 600519.SH --json
~~~

## 2. 证券代码与全市场品种后缀

证券代码须带市场后缀（如 `600519.SH`）。多代码参数支持逗号分隔、空白分隔及 `-` 标准输入（支持按行文本或 JSON 字符串数组）。支持官方全部 **8 大数据领域 / 26 种市场后缀**：

| 品种类别 | 市场后缀 | 示例 | `stock list -m` 常用集合代码 |
|---|---|---|---|
| 股票 (A/B/港/美/新三板) | `.SH` `.SZ` `.BJ` `.HK` `.US` `.NQ` | `600519.SH`、`00700.HK`、`AAPL.US`、`830799.NQ` | `5`=全部A股，`0`=深A，`1`=沪A，`2`=京A，`6`=B股，`7`=科创，`13`=创业板，`23`=沪深300(默认)，`24`=中证500，`25`=中证1000，`49`=北证50，`56`=港股，`57`=美股 |
| 基金 (ETF/LOF/REITs/场外) | `.SH` `.SZ` `.OF` | `510300.SH`、`161725.SZ`、`110011.OF` | `30`=REITs，`31`=ETF，`33`=LOF，`34`=其他基金，`36`=T+0基金，`96`=场外基金 |
| 指数 (沪深/中证/国证/华证/港/扩展) | `.SH` `.SZ` `.CSI` `.CNI` `.SSI` `.HI` `.UZ` | `000300.SH`、`000905.CSI`、`990001.SSI`、`HSI.HI`、`HK0201.UZ` | `3`=深证指数，`4`=上证指数，`95`=重要指数，`16`~`18`=行业一至三级指数 |
| 期货与黄金 (中金/郑商/大商/上期/能源/广期/上金) | `.CFF` `.CZC` `.DCE` `.SHF` `.INE` `.GFE` `.QHZ` `.SGE` | `IF2612.CFF`、`RB2610.SHF`、`IMCI.QHZ`、`AU9999.SGE` | `91`=品种指数(`.QHZ`)，`92`=主力合约，`101`=国内期货，`102`=国际期货 |
| 可转债与期权 | `.SH` `.SZ` `.SHO` `.SZO` `.CFFO` `.CZCO` `.DCEO` `.SHFO` `.GFEO` | `123076.SZ`、`10008001.SHO` | `32`=可转债，`21`=个股期权 |
| 宏观指标 | `.HG` | `CPI.HG`、`GDP.HG` | 通过 `stock search -k GDP` 搜索代码 |

## 3. 完整命令与 SDK 数据接口矩阵（9 组 49 个叶子命令）

| 命令组 | 命令 |
|---|---|
| 全局 | init、close、status |
| market | get、snapshot、divid、zdt、exday、pv、refresh |
| stock | info、more、list、calendar、search、gb、kzz、relation、ipo、etf |
| sector | list、stocks、user、create、delete、rename、clear、update-stocks |
| financial | get、by-date、trading、one |
| formula | list、info、zb、xg、exp |
| client | link、warn、msg、download |
| trade | account、asset、positions、orders、order、cancel |
| config | show、set |

### 3.1 全局与配置 (`全局` / `config` — 5 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `status` | *(离线诊断)* | `--json` | 检查配置路径、`tqcenter.py` 与 `TPythClient.dll` 状态，不建立客户端连接 |
| `init` | `tq.initialize` | `--json` | 在当前进程显式建立 TQ 连接并返回 `run_id` |
| `close` | `tq.close` | `--json` | 关闭当前进程连接 |
| `config show` | *(本地配置)* | `--json` | 显示当前通达信路径来源（CLI 参数 / `TDX_PATH` / 配置文件 / 默认值）及有效性 |
| `config set` | *(本地配置)* | `--tdx-path <DIR>` `--json` | 校验并原子保存通达信安装目录至 `~/.tdx_tq/config.json` |

### 3.2 行情数据 (`market` — 7 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `market get` | `get_market_data` | `-s <stocks>` `-p <1m/5m/15m/30m/1h/1d/1w/1mon/45d/1q/1y>` `-c <count>` `--start` `--end` `-d <none/front/back>` `-f <fields>` `--allow-partial` `-o <path>` `--json` | 获取历史/盘中 K 线；强制 `fill_data=False` 杜绝虚假前值填充；`--start` 与 `-c` 互斥；支持 `-f` 按字段筛选与 `-o` 原子导出 `.csv/.json/.xlsx` |
| `market snapshot` | `get_market_snapshot` | `-s <stocks>` `-f <fields>` `--json` | 获取单只或多只证券实时五档快照（现价、开高低收、买卖五档、成交量额等），支持 `-f` 字段筛选与 `-` 管道输入 |
| `market divid` | `get_divid_factors` | `-s <stock>` `--start` `--end` `--json` | 获取除权除息数据（送转股、配股、现金分红、前/后复权因子），保留真实除权日期索引 |
| `market zdt` | `get_zdt_data` + `get_more_info` | `-s <stocks>` `--json` | 获取当日涨跌停状态明细，并结合客户端近期涨跌停日期（`last_limit_up_date` / `last_limit_down_date`）输出 2 年窗口对账状态 |
| `market exday` | `get_exday_data` | `-s <stock>` `-c <count=10>` `--json` | 获取最近 $N$ 日日线成交与买卖委托笔数/撤单统计（交易日历请用 `stock calendar`） |
| `market pv` | `get_pricevol` | `-s <stocks>` `--json` | 获取分价表（各成交价格档位的买量、卖量与聚合统计） |
| `market refresh` | `refresh_cache` / `refresh_kline` | `-m <AG/HK/US/QH/ZS>` `--force` `-s <stocks>` `-p <1m/5m/1d>` `--json` | 刷新客户端市场行情缓存或指定证券的历史 K 线缓存（K 线刷新仅支持 `1m/5m/1d`） |

### 3.3 证券、基金、可转债与日历 (`stock` — 10 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `stock info` | `get_stock_info` | `-s <stock>` `-f <fields>` `--json` | 获取证券基础财务与属性字典（约 82 个字段，含名称、交易单位、总股本、流通股、资产负债表摘要等），支持 `-f` 筛选 |
| `stock more` | `get_more_info` | `-s <stock>` `-f <fields>` `--json` | 获取扩展估值与盘口特征（约 72 个字段，含 `ZTPrice`、`DTPrice`、`DynaPE`、`PB_MRQ`、封单额、连板数等），支持 `-f` 筛选 |
| `stock list` | `get_stock_list` | `-m <code='23'>` `-t <0/1>` `--json` | 获取指定分类证券列表（默认 `-m 23` 沪深300，`-m 5` 全部A股，`-t 0` 仅代码，`-t 1` 代码+名称） |
| `stock calendar` | `get_trading_dates` | `-m <SH/SZ/AG>` `--start` `--end` `-c <count=20>` `--json` | 查询市场真实交易日历列表（未传起止日期时自动回溯最近 `-c` 个交易日） |
| `stock search` | `get_match_stkinfo` | `-k <keyword>` `--json` | 按中文简称、拼音或代码模糊搜索全市场证券、指数、期货及宏观指标 |
| `stock gb` | `get_gb_info` / `get_gb_info_by_date` | `-s <stock>` `-c <count=5>` `-d <dates>` `--start` `--end` `--json` | 查询股本变动历史（总股本 `Zgb`、流通股 `Ltgb` 等）；支持按最近记录数 `-c`、指定日期列表 `-d` 或起止区间 `--start/--end` 查询 |
| `stock kzz` | `get_kzz_info` | `-s <stock>` `-f <fields>` `--json` | 获取可转债基本属性（转股价、正股代码、赎回/回售条款、到期日等），支持 `-f` 字段筛选 |
| `stock relation` | `get_relation` | `-s <stock>` `--json` | 获取股票所属全部板块关联关系（所属行业、地区、概念、风格、指数板块代码/名称及成分股数量） |
| `stock ipo` | `get_ipo_info` | `-t <0=全部/1=新股/2=新债>` `-d <0=今日/1=未来>` `--json` | 获取今日或未来新股、新发可转债申购日历与发行价/申购上限信息 |
| `stock etf` | `get_trackzs_etf_info` | `-z/--index <zs_code>` `--json` | 获取跟踪指定指数（如 `000300.SH`、`950162.CSI`）的全部 ETF 现价、IOPV 净值、总份额与最新规模 |

### 3.4 板块查询与自定义板块管理 (`sector` — 8 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `sector list` | `get_sector_list` | `-c <all/industry/concept/style/region/industry-concept/research1..3/ext>` `-t <0/1>` `--json` | 获取系统板块列表；支持 `-c` 按 8 大板块类别精确筛选，`-t 0` 仅返回代码、`-t 1` 返回代码与名称 |
| `sector stocks` | `get_stock_list_in_sector` | `-s <block>` `--block-type <0=普通/1=自定义简称/2=期货>` `-t <0/1>` `--json` | 获取系统板块、指数（`88XXXX.SH`）或自定义板块（如 `ZXG` 需加 `--block-type 1`）的成分股列表 |
| `sector user` | `get_user_sector` | `--json` | 获取当前客户端全部自定义板块列表（含 `ZXG` 自选股及用户新建板块） |
| `sector create` | `create_sector` | `-c <code>` `-n <name>` `--json` | *(写操作)* 创建自定义板块（需同时指定板块简称代码 `-c` 与显示名称 `-n`） |
| `sector update-stocks` | `send_user_block` | `-c <code>` `-s <stocks>` `--json` | *(写操作)* 向自定义板块写入/更新成分股列表 |
| `sector rename` | `rename_sector` | `-c <code>` `-n <new_name>` `--json` | *(写操作)* 重命名自定义板块 |
| `sector clear` | `clear_sector` | `-c <code>` `--json` | *(写操作)* 清空自定义板块内全部成分股 |
| `sector delete` | `delete_sector` | `-c <code>` `--json` | *(写操作)* 删除自定义板块 |

### 3.5 专业财务、一致预期与市场交易统计 (`financial` — 4 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `financial get` | `get_financial_data` | `-s <stocks>` `-f <FN_fields>` `--start` `--end` `--report-type <report_time/announce_time/tag_time>` `--allow-partial` `--json` | 按日期区间获取专业财务报表指标（`FN1..FN589`），支持报告期/公告日(PIT)/标签日三口径及完整性校验 |
| `financial by-date` | `get_financial_data_by_date` | `-s <stocks>` `-f <FN_fields>` `-y <year>` `-m <mmdd>` `--allow-partial` `--json` | 按指定报告期（如 `-y 2025 -m 1231`）获取专业财务指标并校验字段完整性 |
| `financial one` | `get_gp_one_data` | `-s <stocks>` `-f <GO_fields>` `--allow-partial` `--json` | 获取股票最新单条财务指标（`GO1..GO25` 一致预期、`GO26..GO27` 最新解禁、`GO29..GO32` 机构/基金持仓、`GO35..GO47` 业绩预告/快报） |
| `financial trading` | `get_gpjy/bkjy/scjy_value` 及 `*_by_date` | `-t <stock/sector/market>` `--target <code_list>` `-f <GP/BK/SC_fields>` `--start` `--end` `-y <year>` `-m <mmdd>` `--allow-partial` `--json` | 获取个股（`GP01..GP52` 龙虎榜/大宗/陆股通/股东数）、板块（`BK01..BK19`）或全市场（`SC01..SC21` 两融/期货持仓）交易统计；支持起止日期或 `-y/-m` 报告期路由 |

### 3.6 技术指标、条件选股与专家系统公式 (`formula` — 5 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `formula list` | `formula_get_all` | `-t <0=指标/1=选股/2=专家>` `--json` | 枚举客户端内全部可用公式名称与描述 |
| `formula info` | `formula_get_info` | `-c <name>` `-t <0=指标/1=选股>` `--json` | 查询指定公式的参数定义、默认值及公式源码说明 |
| `formula zb` | `formula_zb` / `formula_process_mul_zb` | `-n <name>` `-a <args>` `-s <stocks>` `-p <period>` `-c <count>` `--return-count` `--return-date` `--xsflag` `--start` `--end` `--dividend <0/1/2>` `--json` | 计算技术指标公式（如 `MA`、`MACD`）；支持多股批量、多期返回（`--return-count`）、附带日期（`--return-date`）、精度与复权控制 |
| `formula xg` | `formula_xg` / `formula_process_mul_xg` | `-n <name>` `-a <args>` `-s <stocks>` `-p <period>` `-c <count>` `--return-count` `--return-date` `--xsflag` `--start` `--end` `--dividend <0/1/2>` `--json` | 运行条件选股公式（如 `UPN`），筛选命中信号（`1`/`0`）及历史序列 |
| `formula exp` | `formula_exp` / `formula_process_mul_exp` | `-n <name>` `-a <args>` `-s <stocks>` `-p <period>` `-c <count>` `--return-count` `--return-date` `--xsflag` `--start` `--end` `--dividend <0/1/2>` `--json` | 运行专家系统公式（与 `formula list -t 2` 配套），计算多空买卖信号序列 |

### 3.7 客户端桌面联动与静态数据包下载 (`client` — 4 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `client link` | `exec_to_tdx` | `-s <stock>` `--json` | 联动通达信桌面端切换并展示指定证券 K 线窗口 |
| `client warn` | `send_warn` | `-s <stock>` `-m <msg>` `-p <price>` `--json` | 向通达信桌面端发送右下角预警弹窗 |
| `client msg` | `send_message` | `-m <msg>` `--json` | 向通达信桌面端发送通知消息 |
| `client download` | `download_file` | `-s <stock>` `-t <1..7>` `-d <date>` `--json` | 下载 7 类盘后静态数据文件至 `PYPlugins/data`（`1`=十大股东，`2`=ETF申赎，`3`=最近龙虎榜，`4`=重点监控，`5`=增减持，`6`=高管持股，`7`=停复牌） |

### 3.8 实盘与模拟交易 (`trade` — 6 个命令)

| CLI 命令 | 底层 `tqcenter.py` 接口 | 核心参数 | 功能与返回契约 |
|---|---|---|---|
| `trade account` | `stock_account` | `--json` | 查询当前客户端已登录的证券账户 ID 与状态（严格区分未登录与 RPC 异常） |
| `trade asset` | `query_stock_asset` | `-a <account_id>` `--json` | 查询账户总资产、可用资金、可取资金与持仓市值 |
| `trade positions` | `query_stock_positions` | `-a <account_id>` `--json` | 查询账户当前股票持仓明细（持仓量、可用量、成本价、浮动盈亏） |
| `trade orders` | `query_stock_orders` | `-a <account_id>` `-s <stock>` `--cancelable-only` `--json` | 查询当日委托与成交订单明细，支持 `--cancelable-only` 仅筛选可撤委托 |
| `trade order` | `order_stock` | `-s <stock>` `-t <buy/sell>` `-v <vol>` `-p <price>` `--price-type <0=限价/1=市价>` `-a <id>` `--confirm` `--json` | 股票委托下单；**默认仅执行离线参数校验与安全预演**，必须显式传入 `--confirm` 才向客户端发送真实报单 |
| `trade cancel` | `cancel_order_stock` | `-o <order_id>` `-s <stock>` `-a <id>` `--json` | 撤销指定订单号的委托（无预览模式；SDK 受理后需通过 `trade orders` 核对最终撤单状态） |

## 4. 典型场景示例

~~~powershell
# 1. K 线查询：最近 60 条日线（带字段筛选）或指定日期区间（--start 与 -c 互斥）
uv run --locked tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 -f open,high,low,close,volume --json
uv run --locked tdx-tq market get -s 600519.SH --start 20261001 --end 20261020 --json

# 2. 实时快照、涨跌停对账、估值扩展信息与可转债属性
uv run --locked tdx-tq market snapshot -s 600519.SH,510300.SH -f Now,Open,Max,Min,Volume --json
uv run --locked tdx-tq market zdt -s 600519.SH,000001.SZ --json
uv run --locked tdx-tq stock more -s 600519.SH -f HqDate,ZTPrice,DTPrice,DynaPE,PB_MRQ --json
uv run --locked tdx-tq stock kzz -s 123076.SZ -f KZZCode,StockCode,ConvPrice --json

# 3. 股票列表、股本区间变动、个股板块归属、新股申购与指数跟踪 ETF
uv run --locked tdx-tq stock list -m 5 --json
uv run --locked tdx-tq stock gb -s 600519.SH --start 20260101 --end 20261023 --json
uv run --locked tdx-tq stock relation -s 600519.SH --json
uv run --locked tdx-tq stock ipo -t 0 -d 0 --json
uv run --locked tdx-tq stock etf -z 000300.SH --json

# 4. 板块分类筛选与成分股读取（普通板块用 0，自定义板块简称用 --block-type 1）
uv run --locked tdx-tq sector list -c industry -t 1 --json
uv run --locked tdx-tq sector stocks -s ZXG --block-type 1 --list-type 1 --json
uv run --locked tdx-tq sector stocks -s CSBK --block-type 1 --json

# 5. 专业财务（支持 PIT 公告日口径）、一致预期单条指标与交易统计
uv run --locked tdx-tq financial get -s 600519.SH -f FN1,FN4 --start 20250101 --end 20251231 --report-type announce_time --json
uv run --locked tdx-tq financial one -s 600519.SH,000001.SZ -f GO1,GO2,GO29 --json
uv run --locked tdx-tq financial trading -t stock --target 600519.SH -f GP1 --start 20261001 --end 20261023 --json
uv run --locked tdx-tq financial trading -t stock --target 600519.SH -f GP16 -y 2025 -m 1231 --json

# 6. 技术指标、选股与专家系统公式（支持多期带日期返回）
uv run --locked tdx-tq formula zb -n MA -a 5,10,20 -s 600519.SH,000001.SZ -c 60 --return-count 3 --return-date --dividend 1 --json
uv run --locked tdx-tq formula xg -n UPN -a 3 -s 600519.SH,000001.SZ -c 60 --return-count 2 --return-date --json
uv run --locked tdx-tq formula exp -n MACD -a 12,26,9 -s 600519.SH -c 60 --return-count 2 --return-date --json

# 7. 下载盘后静态数据文件包（1=十大股东）
uv run --locked tdx-tq client download -s 688318.SH -t 1 -d 20251231 --json
~~~

自定义板块写操作与交易委托示例（`trade order` 默认仅预览，添加 `--confirm` 才实际提交）：

~~~powershell
uv run --locked tdx-tq sector create -c AIPOOL -n "AI观察池" --json
uv run --locked tdx-tq sector update-stocks -c AIPOOL -s 600519.SH,000001.SZ --json
uv run --locked tdx-tq trade order -s 600519.SH -t buy -v 100 -p 10 --json
~~~

## 5. 执行契约与必要说明

- **连接与会话**：`status` 与 `config show` 只做离线诊断；`init/close` 的连接仅在当前进程内有效，各业务子命令会自动按需初始化并在进程退出时释放连接。`--debug` 向 `stderr` 输出 SDK 方法调用耗时。
- **批量完整性门禁 (`DATA_INCOMPLETE`)**：`market get`、`financial get/by-date/one/trading` 默认执行严格完整性校验，遇到部分标的无数据、请求字段全缺失或无效占位行（如宏观指标零日期 `'0'`、交易统计 `'--'`）时返回退出码 `1` (`DATA_INCOMPLETE`)。若业务允许保留可用子集，可显式传入 `--allow-partial` 获取带 `available_stocks / missing_stocks / missing_fields / dropped_records` 的 `schema_version=1` JSON 信封。
- **涨跌停日期窗口 (`market zdt`)**：保留当日涨跌停明细，并返回 `last_limit_up_date` / `last_limit_down_date`（`YYYY-MM-DD`，来自客户端 `ZTDate_Recent` / `DTDate_Recent`）。官方描述为近两年；客户端返回更早日期时保留原值并在 `history.outside_documented_window` 标明。`history` 中 `found` 为查到日期，`no_record` 为 SDK 零值，`unavailable` 为缺失字段；后两者日期为 `null`，不能解释为历史上从未涨跌停。
- **缓存刷新边界**：`market refresh -s ... -p ...` 仅支持客户端缓存周期 `1m/5m/1d`，其余周期在连接前即拒绝（Exit `2`）；`market get` 的 11 种查询周期不受影响。
- **交易安全边界**：`trade order` 默认仅做离线预演，加 `--confirm` 才提交；`trade cancel` 无预览模式。SDK 返回受理回执不代表已成交或已撤单，遇到状态未知（`TRADE_STATE_UNKNOWN`）时须先通过 `trade orders` 查询核对，切勿盲目重复发单。

## 6. 输出格式、退出码与测试

所有叶子命令支持 `--json`（保证 `stdout` 仅输出合法 JSON，SDK 诊断与告警均重定向至 `stderr`）。退出码：**`0`** 成功、**`2`** 参数校验错误 (`INVALID_PARAMETER`)、**`1`** 操作或 SDK/数据完整性错误、**`130`** 中止。

~~~powershell
uv sync --locked --extra test
uv run --locked --extra test pytest -m "not live" -q
~~~

真实客户端只读回归需显式添加 `--live -m live`；离线回归使用内存替身，不触发任何真实客户端写入或交易。详细架构与协议契约见 [开发文档索引](docs/README.md)。许可为 MIT。
