# TDX TQ SDK CLI

通达信 TQ SDK 的 Windows 命令行工具，支持行情、股票、板块、财务、公式及交易操作。长命令 `cli-anything-tdx-tq` 与短命令 `tdx-tq` 使用同一入口。

## 安装

需要 Windows、Python 3.12/3.13。读取数据前启动并登录支持 TQ 的通达信客户端，确认安装目录中存在 `PYPlugins/sys/tqcenter.py` 及配套 DLL；默认路径为 `D:\tdx`。

~~~powershell
uv sync --locked
uv run --locked tdx-tq --version
uv run --locked tdx-tq status --json
~~~

自定义路径可通过全局 `--tdx-path`、环境变量 `TDX_PATH` 或配置文件指定，优先级依次降低：

~~~powershell
uv run --locked tdx-tq config set --tdx-path D:\tdx --json
uv run --locked tdx-tq --tdx-path D:\tdx market snapshot -s 600519.SH --json
~~~

## 常用命令

证券代码需要市场后缀，如 `600519.SH`。列表参数支持逗号、空白分隔及 `-` 标准输入，标准输入也可使用 JSON 字符串数组。各命令参数以 `--help` 为准。

~~~powershell
# K 线：最近 60 条日线
uv run --locked tdx-tq market get -s 600519.SH,000001.SZ -p 1d -c 60 --json
# 日期区间，与显式 --count 互斥
uv run --locked tdx-tq market get -s 600519.SH --start 20261001 --end 20261020 --json
# 指定股票的涨跌停明细，必须提供 -s
uv run --locked tdx-tq market zdt -s 600519.SH,000001.SZ --json
# 扩展信息与估值
uv run --locked tdx-tq stock more -s 600519.SH -f HqDate,ZTPrice,DTPrice,DynaPE,PB_MRQ --json
# A 股列表；默认 -m 23 为沪深300
uv run --locked tdx-tq stock list -m 5 --json
# 批量计算指标
uv run --locked tdx-tq formula zb -n MA -a 5,10,20 -s 600519.SH,000001.SZ -c 60 --json
~~~

| 命令组 | 命令 |
|---|---|
| 全局 | init、close、status |
| market | get、snapshot、divid、zdt、exday、pv、refresh |
| stock | info、more、list、calendar、search、gb、kzz |
| sector | list、stocks、user、create、delete、rename、clear、update-stocks |
| financial | get、by-date、trading |
| formula | list、info、zb、xg |
| client | link、warn、msg |
| trade | account、asset、positions、orders、order、cancel |
| config | show、set |

## 必要说明

- `status` 只做离线诊断；`init/close` 的连接仅在当前进程有效，后续命令会自行连接。`--debug` 向 stderr 输出 SDK 耗时。
- `market zdt` 不自动生成全市场涨停天梯；`market exday` 是日线成交与委托统计，交易日历使用 `stock calendar`。
- 历史行情、专业财务和部分统计依赖客户端数据包。空结果、零值和缺少参数不能直接证明需要付费；字段、日期、权限和公式类别均需核对。
- K 线和专业财务默认检查请求完整性，不完整时返回 `DATA_INCOMPLETE`。有可用部分时可指定 `--allow-partial`；全部无数据时仍失败。K 线支持 `-o` 导出 `.csv/.json/.xlsx`。

板块代码与显示名称分开指定。以下命令会实际修改客户端板块：

~~~powershell
uv run --locked tdx-tq sector create -c AIPOOL -n "AI观察池" --json
uv run --locked tdx-tq sector update-stocks -c AIPOOL -s 600519.SH,000001.SZ --json
~~~

交易委托默认仅预览，添加 `--confirm` 才提交；`trade cancel` 没有预览模式。SDK 受理不代表成交或最终撤单成功，状态未知时先查询核对，不自动重复提交。

~~~powershell
uv run --locked tdx-tq trade order -s 600519.SH -t buy -v 100 -p 10 --json
~~~

## 输出与测试

叶子命令支持 `--json`，诊断日志进入 stderr；帮助和版本输出仍为文本。退出码：0 成功、2 参数错误、1 操作或 SDK 错误、130 中止。部分接口退出 0 仍可能返回空数据，调用方应检查内容。

~~~powershell
uv sync --locked --extra test
uv run --locked --extra test pytest -m "not live" -q
~~~

真实只读测试需显式添加 `--live -m live`；离线测试使用替身，不提交真实交易。开发说明见 [docs](docs/README.md)。许可为 MIT。
