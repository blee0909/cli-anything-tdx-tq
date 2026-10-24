# TDX TQ SDK CLI

通达信 TQ SDK (`tqcenter.py`) 的 Windows 命令行工具，100% 覆盖本机 TQ SDK 的行情、股票、基金/ETF、指数、板块、专业财务、交易统计、技术指标/选股/专家公式、客户端联动接口，并在原生 SDK 之上提供零侵入缺陷热修补、批量完整性校验与结构化 JSON/文件导出契约。长命令 `cli-anything-tdx-tq` 与短命令 `tdx-tq` 使用同一入口。

> [!IMPORTANT]
> **免责声明**：本项目仅作为通达信量化接口（TQ SDK）的工程封装与开源技术分享，所有功能与数据主要用于量化行情读取、数据分析与策略研究交流，**绝不构成任何投资建议、买卖推荐或交易引导**。股市有风险，入市需谨慎。开发者不对任何依据本工具产生的投资决策、交易行为或资金损益承担任何责任。

## 1. 安装与环境配置

需要 Windows、Python 3.12/3.13。读取数据前需启动并登录支持 TQ 的通达信客户端，确认安装目录中存在 `PYPlugins/sys/tqcenter.py` 及配套 `TPythClient.dll` / `TPyth.dll`；默认路径为 `D:\tdx`。

~~~powershell
uv sync
uv run tdx-tq --version
uv run tdx-tq status --json
~~~

自定义路径可通过全局 `--tdx-path`、环境变量 `TDX_PATH` 或配置文件指定，优先级依次降低：

~~~powershell
uv run tdx-tq config set --tdx-path D:\tdx --json
uv run tdx-tq config show --json
uv run tdx-tq --tdx-path D:\tdx market snapshot -s 600519.SH --json
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
| `market get` | `get_market_data` | `-s <stocks>` `-p <1m/5m/10m/15m/30m/1h/1d/1w/1mon/45d/1q/1y>` `-c <count>` `--start` `--end` `-d <none/front/back>` `-f <fields>` `--allow-partial` `-o <path>` `--json` | 获取历史/盘中 K 线；支持全部 12 大周期（含 `60m` 映射为 `1h`）；强制 `fill_data=False` 杜绝虚假前值填充；`--start` 与 `-c` 互斥；支持 `-f` 按字段筛选与 `-o` 原子导出 `.csv/.json/.xlsx` |
| `market snapshot` | `get_market_snapshot` | `-s <stocks>` `-f <fields>` `--json` | 获取单只或多只证券实时五档快照（现价、开高低收、买卖五档、成交量额等），支持 `-f` 字段筛选与 `-` 管道输入 |
| `market divid` | `get_divid_factors` | `-s <stock>` `--start` `--end` `--json` | 获取除权除息数据（送转股、配股、现金分红、前/后复权因子），保留真实除权日期索引 |
| `market zdt` | `get_zdt_data` + `get_more_info` | `-s <stocks>` `--json` | 获取当日涨跌停状态明细，并结合客户端近期涨跌停日期（`last_limit_up_date` / `last_limit_down_date`）输出 2 年窗口对账状态 |
| `market exday` | `get_exday_data` | `-s <stock>` `-c <count=10>` `--json` | 获取最近 $N$ 日日线成交与买卖委托笔数/撤单统计（交易日历请用 `stock calendar`） |
| `market pv` | `get_pricevol` | `-s <stocks>` `--json` | 获取分价表（各成交价格档位的买量、卖量与聚合统计） |
| `market refresh` | `refresh_cache` / `refresh_kline` | `-m <AG/HK/US/QH/ZS>` `--force` `-s <stocks>` `-p <1m/5m/1d>` `--json` | 刷新客户端市场行情缓存或指定证券的历史 K 线缓存（客户端缓存刷新仅支持 `1m/5m/1d`） |

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
| `client download` | `download_file` | `-s <stock>` `-t <1..7>` `-d <date>` `--json` | 下载 7 类盘后静态数据文件包至 `PYPlugins/data`（`1`=十大股东，`2`=ETF申赎，`3`=最近龙虎榜，`4`=重点监控，`5`=增减持，`6`=高管持股，`7`=停复牌） |

### 3.8 实盘与模拟交易 (`trade` — 6 个命令，仅供原生协议对齐与离线预演)

> **安全提示**：本项目不做实盘交易指引。以下接口仅用于对齐通达信 TQ 原生协议定义及离线参数校验，默认严禁任何自动化报单操作。

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
# 1.1 K 线查询：最近 60 条日线并指定筛选关键字段
# 参数说明：
#   -s, --stocks 600519.SH,000001.SZ  : 待查询证券代码，支持逗号分隔多只证券或管道输入
#   -p, --period 1d                   : K线周期（支持 1m/5m/10m/15m/30m/1h/1d/1w/1mon/45d/1q/1y，默认 1d）
#   -c, --count 60                    : 尾部数量（读取最近 60 根 Bar，未指定 --start 时默认 60）
#   -f, --fields open,high,...        : 筛选输出字段（如 open,high,low,close,volume），避免传输无关字段
#   --json                            : 以标准 JSON 结构输出到 stdout
uv run tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 -f open,high,low,close,volume --json

# 1.2 查询指定历史日期区间的日线（注意：--start 与 -c 严格互斥）
# 参数说明：
#   -s, --stocks 600519.SH            : 待查询证券代码
#   --start 20261001                  : 开始日期（格式 YYYYMMDD 或 YYYYMMDDHHMMSS）
#   --end 20261020                    : 结束日期（包含整日）
uv run tdx-tq market get -s 600519.SH --start 20261001 --end 20261020 --json

# 2.1 股票与 ETF 跨品种五档实时行情快照
# 参数说明：
#   -s, --stocks 600519.SH,510300.SH  : 跨股票与 ETF 批量查询实时五档
#   -f, --fields Now,Open,Max,...     : 筛选现价(Now)、开盘价(Open)、最高价(Max)、最低价(Min)、成交量(Volume)
uv run tdx-tq market snapshot -s 600519.SH,510300.SH -f Now,Open,Max,Min,Volume --json

# 2.2 当日涨跌停状态及 2 年窗口历史对账
# 参数说明：
#   -s, --stocks 600519.SH,000001.SZ  : 目标证券列表，返回封单额、连板数，并对账客户端近 2 年最新涨跌停日期
uv run tdx-tq market zdt -s 600519.SH,000001.SZ --json

# 2.3 查询个股估值与扩展盘口指标（72 个扩展字段按需筛选）
# 参数说明：
#   -s, --stocks 600519.SH            : 目标证券代码
#   -f, --fields HqDate,ZTPrice,...   : 筛选行情日期(HqDate)、涨停价(ZTPrice)、跌停价(DTPrice)、动态市盈率(DynaPE)、市净率(PB_MRQ)
uv run tdx-tq stock more -s 600519.SH -f HqDate,ZTPrice,DTPrice,DynaPE,PB_MRQ --json

# 2.4 可转债核心条款与转股价值属性
# 参数说明：
#   -s, --stocks 123076.SZ            : 可转债代码（如强力转债）
#   -f, --fields KZZCode,StockCode... : 筛选转债代码(KZZCode)、正股代码(StockCode)、转股价(ConvPrice)
uv run tdx-tq stock kzz -s 123076.SZ -f KZZCode,StockCode,ConvPrice --json

# 3.1 获取全市场全部 A 股证券代码列表
# 参数说明：
#   -m, --market 5                    : 分类集合代码（5=全部A股，23=沪深300默认，0=深A，1=沪A，31=ETF，32=可转债）
uv run tdx-tq stock list -m 5 --json

# 3.2 查询指定历史区间的股本变动记录
# 参数说明：
#   -s, --stocks 600519.SH            : 目标证券代码
#   --start 20260101 / --end 20261023 : 变动起止区间，返回总股本(Zgb)与流通股本(Ltgb)的历史序列
uv run tdx-tq stock gb -s 600519.SH --start 20260101 --end 20261023 --json

# 3.3 查询个股所属全部板块关联归属
# 参数说明：
#   -s, --stocks 600519.SH            : 目标证券代码，返回所属的行业、概念、地区、风格、指数等全部板块及成分股数
uv run tdx-tq stock relation -s 600519.SH --json

# 3.4 查询今日申购的新股与新债日历
# 参数说明：
#   -t, --type 0                      : 申购品类（0=全部新股新债，1=仅新股，2=仅新债）
#   -d, --date-type 0                 : 日期范围（0=今日申购，1=未来申购）
uv run tdx-tq stock ipo -t 0 -d 0 --json

# 3.5 查询跟踪指定指数的全部 ETF 规模与实时净值
# 参数说明：
#   -z, --index 000300.SH             : 被跟踪指数代码（如沪深300），返回全市场对应 ETF 现价、IOPV 及总份额
uv run tdx-tq stock etf -z 000300.SH --json

# 4.1 筛选系统全部行业板块（返回代码与名称）
# 参数说明：
#   -c, --category industry           : 板块大类（industry=行业，concept=概念，style=风格，region=地区，ext=扩展）
#   -t, --list-type 1                 : 返回模式（0=仅代码，1=代码加中文名称）
uv run tdx-tq sector list -c industry -t 1 --json

# 4.2 读取客户端系统自选股（ZXG）成分股列表
# 参数说明：
#   -s, --sector ZXG                  : 板块简称代码
#   --block-type 1                    : 板块类型（0=系统板块/指数，1=用户自定义板块简称代码，2=期货板块）
#   -t, --list-type 1                 : 返回包含证券代码与名称
uv run tdx-tq sector stocks -s ZXG --block-type 1 -t 1 --json

# 4.3 读取自定义选股板块成分股（仅返回代码）
# 参数说明：
#   -s, --sector CSBK                 : 用户自定义板块简称代码
#   --block-type 1                    : 声明按自定义板块简称检索
uv run tdx-tq sector stocks -s CSBK --block-type 1 --json

# 5.1 专业财务报表指标区间查询（PIT 点点时间公告日口径）
# 参数说明：
#   -s, --stocks 600519.SH            : 目标股票代码
#   -f, --fields FN1,FN4              : 财务指标字段代号（FN1=基本每股收益，FN4=每股净资产 等）
#   --start 20250101 / --end 20251231 : 查询区间
#   --report-type announce_time       : 时间口径（announce_time=公告日PIT，report_time=报告期，tag_time=标签日）
uv run tdx-tq financial get -s 600519.SH -f FN1,FN4 --start 20250101 --end 20251231 --report-type announce_time --json

# 5.2 获取股票最新单条财务与机构一致预期指标
# 参数说明：
#   -s, --stocks 600519.SH,000001.SZ  : 支持多股批量查询
#   -f, --fields GO1,GO2,GO29         : GO1=一致预期每股收益，GO2=一致预期营收，GO29=预测机构家数
uv run tdx-tq financial one -s 600519.SH,000001.SZ -f GO1,GO2,GO29 --json

# 5.3 查询个股交易统计（龙虎榜、大宗交易、股东户数）
# 参数说明：
#   -t, --type stock                  : 统计类型（stock=个股，sector=板块，market=全市场两融/期货）
#   --target 600519.SH                : 统计目标代码
#   -f, --fields GP1                  : 字段代码（如 GP1=龙虎榜买入金额 等）
#   --start 20261001 / --end 20261023 : 统计起止区间
uv run tdx-tq financial trading -t stock --target 600519.SH -f GP1 --start 20261001 --end 20261023 --json

# 5.4 按报告期年份季度查询个股交易特征
# 参数说明：
#   -y, --year 2025                   : 报告期年份
#   -m, --mmdd 1231                   : 报告期月份（如 0331 一季报，0630 半年报，0930 三季报，1231 年报）
#   -f, --fields GP16                 : 股东总户数变动等特征字段
uv run tdx-tq financial trading -t stock --target 600519.SH -f GP16 -y 2025 -m 1231 --json

# 6.1 批量计算技术指标公式（支持前复权与多期带日期返回）
# 参数说明：
#   -n, --name MA                     : 公式名称（如 MA、MACD、KDJ、BOLL 等）
#   -a, --args 5,10,20                : 公式入参列表，逗号分隔（对应 MA 的周期参数 N1,N2,N3）
#   -s, --stocks 600519.SH,000001.SZ  : 多股批量并行计算
#   -c, --count 60                    : 计算基准窗口 Bar 数量
#   --return-count 3                  : 仅截取并返回最后 3 期结果
#   --return-date                     : 返回结果附带精确日期时间戳序列
#   --dividend 1                      : 复权处理（0=不复权，1=前复权，2=后复权）
uv run tdx-tq formula zb -n MA -a 5,10,20 -s 600519.SH,000001.SZ -c 60 --return-count 3 --return-date --dividend 1 --json

# 6.2 运行条件选股公式（筛选命中信号序列）
# 参数说明：
#   -n, --name UPN                    : 选股公式名称（连涨选股）
#   -a, --args 3                      : 参数（如连续上涨 3 天）
#   --return-count 2                  : 返回最后 2 期的选股命中信号（1=命中，0=未命中）
uv run tdx-tq formula xg -n UPN -a 3 -s 600519.SH,000001.SZ -c 60 --return-count 2 --return-date --json

# 6.3 运行专家系统公式（计算多空买卖操作信号）
# 参数说明：
#   -n, --name MACD                   : 专家系统公式名称
#   -a, --args 12,26,9                : 快慢均线及平滑参数
#   --return-count 2                  : 返回最近两期多空买卖信号
uv run tdx-tq formula exp -n MACD -a 12,26,9 -s 600519.SH -c 60 --return-count 2 --return-date --json

# 7.1 下载盘后静态数据文件包
# 参数说明：
#   -s, --stocks 688318.SH            : 目标证券代码
#   -t, --type 1                      : 静态包分类（1=十大股东，2=ETF申赎，3=最新龙虎榜，4=重点监控，5=增减持，6=高管持股，7=停复牌）
#   -d, --date 20251231               : 数据基准归档日期（YYYYMMDD）
uv run tdx-tq client download -s 688318.SH -t 1 -d 20251231 --json

# 8.1 创建自定义板块并批量写入成分股
# 参数说明：
#   -c, --code AIPOOL                 : 自定义板块英文字符简称代码（建议 2-8 位字母）
#   -n, --name "AI观察池"             : 板块在通达信中显示的中文名称
#   -s, --stocks 600519.SH,000001.SZ  : 批量写入该板块的证券代码列表
uv run tdx-tq sector create -c AIPOOL -n "AI观察池" --json
uv run tdx-tq sector update-stocks -c AIPOOL -s 600519.SH,000001.SZ --json
~~~

## 5. 执行契约与必要说明

- **连接与会话**：`status` 与 `config show` 只做离线诊断；`init/close` 的连接仅在当前进程内有效，各业务子命令会自动按需初始化并在进程退出时释放连接。`--debug` 向 `stderr` 输出 SDK 方法调用耗时。
- **K 线周期与 1 分钟线 (`1m`) 机制**：
  - 本工具完整支持官方 12 个周期（`1m/5m/10m/15m/30m/1h/1d/1w/1mon/45d/1q/1y`，其中 `60m` 自动映射为 `1h`）。在实测中，`5m` 到 `1y` 的 11 个周期开箱即用，能立即返回实时高频与历史数据。
  - **`1m`（1分钟线）返回空列表的原因与前置条件**：
    1. **数据体积限制**：全市场 5000+ 股票的 1 分钟历史数据体积达数百 GB，通达信客户端默认不会在后台同步全量历史 1 分钟数据。
    2. **获取 1m 的前置条件**：
       - **盘中实时**：开盘期间客户端在后台运行时，会自动接收并缓存当日的 1 分钟线；
       - **盘后历史**：若需查询历史 1 分钟线，需要在通达信客户端界面手动点击：**【系统】 -> 【盘后数据下载】 -> 勾选【1分钟线数据】**，下载落盘后，`market get -p 1m` 即可直接读取出完整的 1 分钟 K 线。
    3. **完整性拦截**：若本地未下载 1m 数据，CLI 会触发严格的数据完整性校验并返回退出码 `1`（`get_market_data 没有返回可用数据`），杜绝用虚假数据误导量化策略。
- **批量完整性门禁 (`DATA_INCOMPLETE`)**：`market get`、`financial get/by-date/one/trading` 默认执行严格完整性校验，遇到部分标的无数据、请求字段全缺失或无效占位行（如宏观指标零日期 `'0'`、交易统计 `'--'`）时返回退出码 `1` (`DATA_INCOMPLETE`)。若业务允许保留可用子集，可显式传入 `--allow-partial` 获取带 `available_stocks / missing_stocks / missing_fields / dropped_records` 的 `schema_version=1` JSON 信封。
- **涨跌停日期窗口 (`market zdt`)**：保留当日涨跌停明细，并返回 `last_limit_up_date` / `last_limit_down_date`（`YYYY-MM-DD`，来自客户端 `ZTDate_Recent` / `DTDate_Recent`）。官方描述为近两年；客户端返回更早日期时保留原值并在 `history.outside_documented_window` 标明。`history` 中 `found` 为查到日期，`no_record` 为 SDK 零值，`unavailable` 为缺失字段；后两者日期为 `null`，不能解释为历史上从未涨跌停。
- **缓存刷新边界**：`market refresh -s ... -p ...` 仅支持客户端缓存周期 `1m/5m/1d`，其余周期在连接前即拒绝（Exit `2`）；`market get` 的 12 种查询周期不受影响。
- **交易安全边界**：本项目不做实盘交易指引；`trade order` 默认仅做离线预演，加 `--confirm` 才提交；`trade cancel` 无预览模式。SDK 返回受理回执不代表已成交或已撤单，遇到状态未知（`TRADE_STATE_UNKNOWN`）时须先通过 `trade orders` 查询核对，切勿盲目重复发单。

## 6. 输出格式、退出码与测试

所有叶子命令支持 `--json`（保证 `stdout` 仅输出合法 JSON，SDK 诊断与告警均重定向至 `stderr`）。退出码：**`0`** 成功、**`2`** 参数校验错误 (`INVALID_PARAMETER`)、**`1`** 操作或 SDK/数据完整性错误、**`130`** 中止。

~~~powershell
uv sync --extra test
uv run --extra test pytest -m "not live" -q
~~~

真实客户端只读回归需显式添加 `--live -m live`；离线回归使用内存替身，不触发任何真实客户端写入或交易。详细架构与协议契约见 [开发文档索引](docs/README.md)。许可为 MIT。
