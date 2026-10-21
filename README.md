# TDX TQ SDK CLI

通达信量化接口命令行工具 - 基于 TQ 策略模式

## 安装

### 前置条件

1. **通达信客户端**：已启动并登录
2. **通达信安装路径**：默认 `D:\tdx`，可通过环境变量 `TDX_PATH` 修改
3. **Python 3.8+**

### 安装步骤

```bash
# 进入 cli_agent 目录
cd cli_agent

# 安装依赖
pip install -e .
```

### 验证安装

```bash
# 检查 CLI 是否可用
cli-anything-tdx-tq --version

# 查看帮助
cli-anything-tdx-tq --help
```

## 快速开始

### 1. 查看状态

```bash
cli-anything-tdx-tq status
```

### 2. 获取行情数据

```bash
# 获取日线数据
cli-anything-tdx-tq market get -s 600487.SH,000001.SZ -p 1d -c 60

# 获取实时行情
cli-anything-tdx-tq market snapshot -s 600487.SH

# 获取除权除息数据
cli-anything-tdx-tq market divid -s 600487.SH
```

### 3. 获取股票信息

```bash
# 获取基础信息
cli-anything-tdx-tq stock info -s 600487.SH

# 获取更多信息
cli-anything-tdx-tq stock more -s 600487.SH
```

### 4. 获取板块数据

```bash
# 获取行业板块列表
cli-anything-tdx-tq sector list -t 0

# 获取板块成分股
cli-anything-tdx-tq sector stocks -s 半导体
```

### 5. 获取财务数据

```bash
cli-anything-tdx-tq financial get -s 600487.SH -f FN1,FN4,FN6 --start 20240101 --end 20241231
```

### 6. 调用公式

```bash
# 调用指标公式
cli-anything-tdx-tq formula zb -n MA -a 5,10,20

# 调用选股公式
cli-anything-tdx-tq formula xg -n KDJ -a 9,3,3
```

### 7. 导出 JSON 格式

```bash
# 所有命令都支持 --json 输出
cli-anything-tdx-tq stock info -s 600487.SH --json
```

## 命令参考

### 全局命令

| 命令 | 说明 |
|------|------|
| `init` | 初始化连接 |
| `close` | 关闭连接 |
| `status` | 查看状态 |
| `config` | 配置管理 (支持 ~/.tdx_tq/config.json 持久化) |

> 快捷提示：可以使用短别名 `tdx-tq` 替代长命令 `cli-anything-tdx-tq`。支持标准输入管道（例如 `echo 600519.SH | tdx-tq market snapshot -s - --json`）。

### market - 行情数据

| 命令 | 说明 |
|------|------|
| `market get` | 获取 K 线数据 (完整保留日期与 OHLCV) |
| `market snapshot` | 获取实时行情快照 (支持多代码与管道) |
| `market divid` | 获取除权除息数据 |
| `market zdt` | 获取股票涨跌停价格与停牌状态 |
| `market pv` | 获取量价聚合统计 |

### stock - 股票信息

| 命令 | 说明 |
|------|------|
| `stock info` | 获取股票基础财务与属性信息 |
| `stock more` | 获取股票进阶信息 |
| `stock list` | 获取全市场股票代码列表 |
| `stock search` | 股票代码/名称模糊匹配搜索 |
| `stock gb` | 获取股本变动信息 (自动匹配有效交易日) |
| `stock kzz` | 获取可转债信息 |
| `stock calendar`| 查询交易日历 |

### sector - 板块数据

| 命令 | 说明 |
|------|------|
| `sector list` | 获取板块列表 |
| `sector stocks` | 获取板块成分股 |
| `sector user` | 获取用户自选/自定义板块 |
| `sector create` | 创建新的自定义板块 |
| `sector delete` | 删除指定的自定义板块 |
| `sector rename` | 重命名自定义板块 |
| `sector clear` | 清空自定义板块成分股 |
| `sector update-stocks` | 更新自定义板块成分股 |

### client - 桌面客户端联动与预警

| 命令 | 说明 |
|------|------|
| `client link` | 联动通达信桌面客户端切换并展示指定股票 K 线 |
| `client warn` | 向通达信桌面端发送交易预警气泡弹窗 |
| `client msg` | 向通达信客户端发送文字通知 |

### trade - 交易与账户管理

| 命令 | 说明 |
|------|------|
| `trade account` | 查询当前交易账户状态与 ID |
| `trade asset` | 查询资金账户总资产与可用余额 |
| `trade positions` | 查询股票持仓与盈亏明细 |
| `trade orders` | 查询当日委托订单列表 |
| `trade order` | 提交买卖委托 (默认安全预览，需 `--confirm` 确认) |
| `trade cancel` | 撤销已提交的委托订单 |

### financial - 财务数据

| 命令 | 说明 |
|------|------|
| `financial get` | 获取专业财务数据 (注: 需客户端下载离线财务数据包) |
| `financial by-date` | 按指定报告期获取财务数据 |
| `financial trading` | 获取股票/板块/市场交易统计数据 |

### formula - 公式调用

| 命令 | 说明 |
|------|------|
| `formula list` | 获取所有指标或选股公式列表 |
| `formula info` | 获取指定公式的参数与定义 |
| `formula zb` | 调用指标公式并计算 |
| `formula xg` | 调用选股公式筛选股票 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TDX_PATH` | 通达信安装路径 | `D:\tdx` |

## 输出格式

### JSON 模式

所有命令都支持 `--json` 选项，输出 JSON 格式数据，便于 Agent 消费：

```bash
cli-anything-tdx-tq stock info -s 600487.SH --json
```

### 表格模式（默认）

默认输出为人类可读的表格格式。

## 故障排除

### 错误：初始化失败

确保：
1. 通达信客户端已启动并登录
2. 通达信安装路径正确
3. `D:\tdx\PYPlugins\sys\tqcenter.py` 存在

### 错误：模块未找到

运行 `pip install -e .` 重新安装。

## 开发

```bash
# 开发模式安装
pip install -e .

# 运行测试
pytest tests/
```

## License

MIT
