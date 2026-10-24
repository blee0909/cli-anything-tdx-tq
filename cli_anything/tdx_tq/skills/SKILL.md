---
name: tdx-tq-sdk
description: 通达信 TQ SDK CLI - 通达信量化接口命令行工具
version: 1.1.0
---

# TDX TQ SDK CLI Skill

通达信量化接口命令行工具，基于 TQ 策略模式，提供完整的股票数据获取和分析功能。

## 前置条件

1. **通达信客户端**：必须已启动并登录
2. **通达信安装**：默认路径 `D:\tdx`
3. **Python 环境**：Windows Python 3.12/3.13

## 安装

```bash
uv sync
```

## 命令组

### market - 行情数据

获取股票 K 线数据、实时行情、除权除息数据等。

#### market get - 获取 K 线数据

```bash
cli-anything-tdx-tq market get -s <股票代码> [选项]
```

**参数：**
- `-s, --stocks`: 股票代码列表，逗号分隔（必填）
- `-p, --period`: 周期，可选 1m/5m/15m/30m/1h/1d/1w/1mon（默认：1d）
- `-c, --count`: 尾部数量（无 --start 时默认 60，与 --start 互斥）
- `--start/--end`: 日期区间；八位 K 线结束日期包含整日，区间不受默认 60 条截断
- `--allow-partial`: 显式接受部分数据，返回带缺失说明的版本 1 JSON 信封
- `-d, --dividend`: 复权类型，可选 none/front/back（默认：front）
- `-o, --output`: 输出文件路径
- `--json`: 以 JSON 格式输出

**示例：**
```bash
# 获取亨通光电 60 条日线数据
cli-anything-tdx-tq market get -s 600487.SH -c 60

# 获取前复权数据
cli-anything-tdx-tq market get -s 600487.SH -d front

# 获取多只股票数据
cli-anything-tdx-tq market get -s 600487.SH,000001.SZ,600519.SH

# 输出 JSON 格式
cli-anything-tdx-tq market get -s 600487.SH --json
```

#### market snapshot - 获取实时行情快照

```bash
cli-anything-tdx-tq market snapshot -s <股票代码> [选项]
```

**参数：**
- `-s, --stock`: 股票代码（必填）
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq market snapshot -s 600487.SH
cli-anything-tdx-tq market snapshot -s 600487.SH --json
```

#### market divid - 获取除权除息数据

```bash
cli-anything-tdx-tq market divid -s <股票代码> [选项]
```

**参数：**
- `-s, --stock`: 股票代码（必填）
- `--start`: 开始时间 YYYYMMDD
- `--end`: 结束时间 YYYYMMDD
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq market divid -s 600487.SH
cli-anything-tdx-tq market divid -s 600487.SH --start 20230101 --end 20241231
```

### stock - 股票信息

获取股票基础信息、更多信息等。

#### stock info - 获取股票基础信息

```bash
cli-anything-tdx-tq stock info -s <股票代码> [选项]
```

**参数：**
- `-s, --stock`: 股票代码（必填）
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq stock info -s 600487.SH
cli-anything-tdx-tq stock info -s 600487.SH --json
```

#### stock more - 获取股票更多信息

```bash
cli-anything-tdx-tq stock more -s <股票代码> [选项]
```

**参数：**
- `-s, --stock`: 股票代码（必填）
- `-f, --fields`: 指定字段，逗号分隔
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq stock more -s 600487.SH
cli-anything-tdx-tq stock more -s 600487.SH -f HisHigh,HisLow,ZTPrice
```

### sector - 板块数据

获取板块列表、板块成分股等。

#### sector list - 获取板块列表

```bash
cli-anything-tdx-tq sector list [选项]
```

**参数：**
- `-c, --category`: 板块类别，可选 all（全部板块[10]）、industry（行业[11]）、concept（概念[12]）、style（风格[13]）、region（地区[14]）、industry-concept（行业+概念[15]）、research1/2/3（研究行业一/二/三级[16/17/18]）、ext（扩展市场板块[110]）（默认：all）
- `-t, --type, --list-type`: 返回格式，0=仅代码列表，1=代码与名称（默认：0）
- `--json`: 以 JSON 格式输出

**示例：**
```bash
# 获取全部板块代码列表
cli-anything-tdx-tq sector list

# 获取行业板块（代码与名称）
cli-anything-tdx-tq sector list -c industry -t 1

# 获取概念板块
cli-anything-tdx-tq sector list -c concept --json
```

#### sector stocks - 获取板块成分股

```bash
cli-anything-tdx-tq sector stocks -s <板块名称> [选项]
```

**参数：**
- `-s, --sector`: 板块名称（必填）
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq sector stocks -s 半导体
cli-anything-tdx-tq sector stocks -s 人工智能 --json
```

### financial - 财务数据

获取专业财务数据。

#### financial get - 获取专业财务数据

```bash
cli-anything-tdx-tq financial get -s <股票列表> -f <字段列表> [选项]
```

**参数：**
- `-s, --stocks`: 股票代码列表，逗号分隔（必填）
- `-f, --fields`: 字段列表，逗号分隔（必填），如 FN1(EPS),FN4(BPS),FN6(ROE)
- `--start`: 开始时间 YYYYMMDD
- `--end`: 结束时间 YYYYMMDD
- `--json`: 以 JSON 格式输出

**常用字段：**
- FN1: 每股收益 (EPS)
- FN4: 每股净资产 (BPS)
- FN6: 净资产收益率 (ROE)
- FN230: 营业收入
- FN232: 净利润

**示例：**
```bash
cli-anything-tdx-tq financial get -s 600487.SH -f FN1,FN4,FN6
cli-anything-tdx-tq financial get -s 600487.SH -f FN230,FN232 --start 20230101 --end 20241231
```

#### financial trading - 获取交易统计特征数据

```bash
cli-anything-tdx-tq financial trading -t <stock|sector|market> -f <字段列表> [选项]
```

**参数：**
- `-t, --type`: 统计类型，可选 stock（个股）、sector（板块）、market（全市场）（默认：stock）
- `--target`: 标的代码列表（stock 时为个股代码如 600519.SH，sector 时为板块代码如 880201.SH；stock/sector 时必填）
- `-f, --fields`: 统计指标字段，逗号分隔（个股如 GP01,GP02，板块如 BK05,BK06，全市场如 SC01,SC02）（必填）
- `--start`: 开始时间 YYYYMMDD
- `--end`: 结束时间 YYYYMMDD
- `--allow-partial`: 允许部分可用数据，返回带完整性说明的 JSON 信封
- `--json`: 以 JSON 格式输出

**示例：**
```bash
# 获取个股交易统计
cli-anything-tdx-tq financial trading -t stock --target 600519.SH -f GP01,GP16 --start 20261001 --end 20261023 --json

# 获取全市场交易统计
cli-anything-tdx-tq financial trading -t market -f SC01,SC02 --start 20261001 --end 20261023 --json
```

#### financial one - 获取股票最新单条财务指标

```bash
cli-anything-tdx-tq financial one -s <股票列表> -f <最新字段列表> [选项]
```

**参数：**
- `-s, --stocks`: 股票代码列表，逗号分隔（必填）
- `-f, --fields`: 字段列表，逗号分隔（必填，如 GO1,GO2 一致预期，GO26 解禁，GO29 机构持仓，GO35 业绩预告）
- `--allow-partial`: 允许部分可用数据
- `--json`: 以 JSON 格式输出

### formula - 公式调用

调用通达信指标公式、选股公式。

#### formula zb - 调用指标公式

```bash
cli-anything-tdx-tq formula zb -n <公式名> [选项]
```

**参数：**
- `-n, --name`: 指标公式名称（必填）
- `-a, --args`: 公式参数，逗号分隔
- `-s, --stock`: 股票代码
- `--json`: 以 JSON 格式输出

**示例：**
```bash
# 调用 MA 指标
cli-anything-tdx-tq formula zb -n MA -a 5,10,20

# 调用 KDJ 指标
cli-anything-tdx-tq formula zb -n KDJ -a 9,3,3
```

#### formula xg - 调用选股公式

```bash
cli-anything-tdx-tq formula xg -n <公式名> [选项]
```

**参数：**
- `-n, --name`: 选股公式名称（必填）
- `-a, --args`: 公式参数，逗号分隔
- `-s, --stocks`: 股票列表，逗号分隔
- `--json`: 以 JSON 格式输出

**示例：**
```bash
cli-anything-tdx-tq formula xg -n KDJ -a 9,3,3
```

### client - 桌面客户端联动与预警

联动桌面通达信客户端界面，实现看盘联动、预警气泡与消息通知。

#### client link - 联动看盘

```bash
tdx-tq client link -s 600519.SH
```

#### client warn - 预警弹窗

```bash
tdx-tq client warn -s 600519.SH -m "突破布林上轨" -p 1350.0
```

#### client msg - 客户端通知

```bash
tdx-tq client msg -m "AI策略选股已完成"
```

### trade - 交易与账户管理

实盘与模拟交易执行、资金资产查询、持仓与订单管理。

#### trade account - 查看交易账户

```bash
tdx-tq trade account [--json]
```

#### trade asset - 查询账户资产

```bash
tdx-tq trade asset [--json]
```

#### trade positions - 查询股票持仓

```bash
tdx-tq trade positions [--json]
```

#### trade orders - 查询委托订单

```bash
tdx-tq trade orders [--cancelable-only] [--json]
```

#### trade order - 提交买卖委托

```bash
# 安全预览模式（未实际发送）
tdx-tq trade order -s 600519.SH -t buy -v 100 -p 1300.0

# 确认下单提交
tdx-tq trade order -s 600519.SH -t buy -v 100 -p 1300.0 --confirm
```

#### trade cancel - 撤销委托

```bash
tdx-tq trade cancel -o <订单号> -s 600519.SH
```

### config - 配置管理

#### config show - 显示当前配置

```bash
cli-anything-tdx-tq config show
```

#### config set - 设置配置

```bash
cli-anything-tdx-tq config set --tdx-path D:\new_tdx64
```

### status - 查看状态

```bash
cli-anything-tdx-tq status [--json]
```

## 全局选项

| 选项 | 说明 |
|------|------|
| `--help` | 显示帮助信息 |
| `--version` | 显示版本号 |
| `--tdx-path` | 通达信安装路径（可通过 TDX_PATH 环境变量设置） |

## 输出格式

### JSON 模式

所有命令都支持 `--json` 选项，输出 JSON 格式数据：

```bash
cli-anything-tdx-tq stock info -s 600487.SH --json
```

输出示例：
```json
{
  "Code": "600487.SH",
  "Name": "亨通光电",
  "Industry": "通信设备",
  "Area": "江苏",
  ...
}
```

## 使用场景

### 场景 1：获取单只股票数据

```bash
# 获取日线数据
cli-anything-tdx-tq market get -s 600487.SH -c 100 --json > data.json
```

### 场景 2：获取板块成分股

```bash
# 获取半导体板块所有股票
cli-anything-tdx-tq sector stocks -s 半导体 --json > semiconductor.json
```

### 场景 3：批量获取财务数据

```bash
# 获取多只股票的 EPS 和 ROE
cli-anything-tdx-tq financial get -s 600487.SH,000001.SZ,600519.SH -f FN1,FN6 --json
```

## 注意事项

1. **前置条件**：必须先启动并登录通达信客户端
2. **股票代码格式**：6 位数字 + 市场后缀，如 `600487.SH`
3. **时间格式**：YYYYMMDD，如 `20240101`
4. **数据周期**：1m, 5m, 15m, 30m, 1h, 1d, 1w, 1mon 等
5. **复权类型**：none(不复权), front(前复权), back(后复权)

## 故障排除

### 初始化失败

确保通达信客户端已启动并登录，检查路径配置：
```bash
cli-anything-tdx-tq config show
```

### 模块未找到

重新安装：
```bash
uv sync
```


## 自动化执行契约

帮助、状态和订单预览不需要 SDK。--json 失败输出包含 success=false、error_code、error_type 和 retryable，需同时检查非零退出码。嵌套数据保持结构，NaN/Inf 转为 null；分钟 K 线保留完整时间。60m 映射为 1h，当前不支持 tick。

订单预览不查询账户；account_id 可为 null。--confirm 才提交，限价须大于零。status=accepted 仅代表 SDK 受理；TRADE_STATE_UNKNOWN 需要先查询委托核对，不自动重复提交。init/close 只作用于当前进程。

账户查询 state=unavailable、logged_in=false 需要明确的原生无可用账户回执；ACCOUNT_STATE_UNKNOWN 不表示已退出登录。客户端、板块、缓存操作缺少确认回执时返回 OPERATION_STATE_UNKNOWN，不应直接重复执行。交易的空响应、传输异常或畸形回执按 TRADE_STATE_UNKNOWN 先核对委托。除权除息 JSON 保留 SDK 的 Date 索引。

market get、financial get、financial by-date 默认对不完整数据返回 DATA_INCOMPLETE、非零退出码；details 列出缺失证券/字段/值与逐项错误，data 保留可用部分。--allow-partial 的成功结果始终为 schema_version=1、success、partial、data 及完整性说明；全部无可用数据仍失败。请保存信封中的缺失说明，不要只按证券键存在判断数据完整。严格导出在验证前不会替换文件；部分导出的数据文件仅保存有效记录，控制台信封保存缺失说明。K 线关闭自动前值填充，财务默认 RangeIndex 不输出人为 index 字段，有意义索引仍保留。
