---
name: tdx-tq-sdk
description: 通达信 TQ SDK CLI - 通达信量化接口命令行工具
version: 1.1.0
---

# TDX TQ SDK CLI Skill

通达信量化接口命令行工具，围绕本机 `tqcenter.py` 与通达信本地缓存，提供行情获取、实时订阅流、板块管理、本地高速检索、桌面看板投屏与预警联动能力。

## 前置条件

1. **通达信客户端**：读取实时行情/客户端接口前须启动并登录（`status`、`config detect`、`stock list --categories`、`stock search --local`、`stock relation --local`、`client download --parse-only` 与 `trade order` 预览可离线运行）
2. **通达信路径**：支持 `tdx-tq config detect --apply` 自动探测（如 `D:\TDX64`、`D:\tdx`）
3. **Python 环境**：Windows Python 3.12/3.13

## 核心命令速查

### 1. 环境诊断、自动探测与常驻守护进程 (`status` / `config` / `daemon`)
```powershell
tdx-tq status --json
tdx-tq config detect --apply --json
tdx-tq stock list --categories --json

# 启动后台长连接守护进程（后续 CLI 命令自动毫秒级复用同一 run_id 会话）
tdx-tq daemon start --idle-timeout 1800 --json
tdx-tq daemon status --json
tdx-tq daemon stop --json
```

### 2. 行情获取、分块拉取、宽表与实时订阅流 (`market`)
```powershell
# K 线尾部、分块大批量拉取与宽表导出（支持 .csv/.json/.jsonl/.xlsx/.parquet）
tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 --json
tdx-tq --timeout 30 market get -s 600519.SH,000001.SZ -p 1d -c 60 --chunk-size 100 --allow-partial -o kline.jsonl --json
tdx-tq market get -s 600519.SH,000001.SZ --start 20261001 --end 20261020 --pivot close --fill ffill -o close_wide.csv --json

# 实时五档快照（多股批量支持 --allow-partial 与 -o 导出）
tdx-tq market snapshot -s 600519.SH,000001.SZ -f Now,Open,Max,Min,Volume --allow-partial --json

# 实时行情更新流（最多 100 只，支持 NDJSON 流式输出，退出自动退订）
tdx-tq market watch -s 600519.SH,000001.SZ -f Now,Volume --debounce-ms 500 --max-events 10 --jsonl

# 除权除息与缓存刷新（K线刷新仅支持 1m/5m/1d）
tdx-tq market divid -s 600519.SH --start 20240101 --end 20251231 --json
tdx-tq market refresh -s 600519.SH -p 1d --json
```

### 3. 股票、自选/持仓、拼音检索与板块关联 (`stock`)
```powershell
# 读取客户端自选股(-m 0)、客户端持仓股(-m 1)、全部A股(-m 5)、中证A500(-m 28)
tdx-tq stock list -m 0 --json
tdx-tq stock list -m 1 --json
tdx-tq stock list -m 5 --json

# 支持代码、中文简称与拼音首字母（如 GZMT）检索
tdx-tq stock search -k GZMT --local --json

# 解析个股所属行业、研究行业、概念、风格、指数板块
tdx-tq stock relation -s 600519.SH --local --json

# 免费基础股本/财务摘要（单位：万）、可转债条款、交易日历与申购日历
tdx-tq stock info -s 600519.SH -f J_zgb,ActiveCapital --json
tdx-tq stock kzz -s 113050.SH --json
tdx-tq stock calendar -m SH -c 10 --json
tdx-tq stock ipo -t 0 -d 0 --json
```

### 4. 板块查询与临时条件股/自定义板块同步 (`sector`)
```powershell
tdx-tq sector list -c industry --json
tdx-tq sector stocks -s 半导体 --json
tdx-tq sector user --json

# 自定义板块增删改与临时条件股推送
tdx-tq sector create -c AIPOOL -n "AI观察池" --json
tdx-tq sector update-stocks -c AIPOOL -s 600519.SH,000001.SZ --json
tdx-tq sector update-stocks --temp -s 600519.SH,000001.SZ --no-show --json
```

### 5. 客户端桌面看板投屏、预警、回测与文件解析 (`client`)
```powershell
# 将 CSV/JSON 表编译生成通达信定制面板 (.xml + .jsn + .sp)
tdx-tq client panel -i close_wide.csv -n factor_pad --layout vertical --titles "收盘价矩阵" --json

# 发送带买卖标志(B/S)的策略预警、策略日志、结果文件与回测时间序列
tdx-tq client warn -s 600519.SH -m "突破均线" -p 1550.0 --bs-flag B --json
tdx-tq client msg -m "策略运行完毕" --json
tdx-tq client file -f close_wide.csv --stage --json
tdx-tq client bt -s 600519.SH --times 20261020150000,20261021150000 --data "1,100.5;0,102.3" --json

# 下载并结构化解析十大股东(1)或 ETF 申赎清单(2)
tdx-tq client download -s 600519.SH -t 1 -d 20251231 --parse --json
tdx-tq client download -s 600519.SH -t 1 --parse-only --json
```

### 6. 本地向量化通达信公式与全链路选股闭环 (`formula`)
```powershell
# 离线查看内置指标(0)、选股(1)、专家系统(2)公式列表与源码
tdx-tq formula list -t 1 --local --json
tdx-tq formula info -c MACD -t 0 --local --json

# 基于真实 K 线本地计算经典指标
tdx-tq formula zb -n MACD -a 12,26,9 -s 600519.SH,000001.SZ -c 60 --return-count 3 --local --json

# 自定义通达信公式表达式选股 -> 自动同步临时条件股 + 生成看板 + 导出 JSONL
tdx-tq formula eval -e "CROSS(MA(C,5), MA(C,20)) AND V > REF(MA(V,5),1)*1.5" --market-list 23 -c 60 --select --allow-partial --push-temp --no-show --push-panel ma_cross_pad -o hits.jsonl --json
```

## 自动化执行契约

- 所有命令支持 `--json`（`market watch` 支持 `--jsonl` 流式行输出），失败输出包含 `success=false`、`error_code`、`error_type` 与 `retryable`，退出码 `2` 为参数错误、`1` 为 SDK/数据/不支持错误。
- `status --json` 中的 `sdk.sdk_profile` 与 `sdk.capabilities` 反映本机真实 SDK 能力；缺失接口返回 `UNSUPPORTED_SDK_CAPABILITY`，绝不输出伪造结果。
- `market get` 默认严格校验完整性，不完整返回 `DATA_INCOMPLETE`；传 `--allow-partial` 返回 `schema_version=1` 信封。
- `trade order` 默认仅离线预览，加 `--confirm` 才调用底层接口；`TRADE_STATE_UNKNOWN` 严禁自动重复提交。
