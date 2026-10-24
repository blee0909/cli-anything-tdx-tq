# TDX TQ CLI 架构与执行契约

当前目标平台为 Windows，Python 3.12/3.13。源码版本由 cli_anything.tdx_tq.__version__ 提供，构建元数据与 CLI 共用该来源。

| 层 | 位置 | 职责 |
|---|---|---|
| 命令 | tdx_tq_cli.py | Click 参数、调用编排、文本/JSON 呈现 |
| 交易服务 | core/trading.py | OrderRequest 校验、提交/撤单映射及状态未知处理 |
| 行情服务 | core/market.py | MarketRequest 数量/区间模式、代码/周期/日期校验及时间轴归一化 |
| 数据完整性 | core/quality.py | 证券实际可用性、财务请求字段、逐项错误、严格/部分结果契约 |
| SDK 适配 | core/sdk.py、core/protocol.py | 指定来源加载、原生回执证据、兼容补丁、返回契约与耗时日志 |
| 会话 | core/session.py | 进程内连接、来源一致性、真实交易日历 |
| 配置 | core/config.py | 优先级、损坏诊断、原子持久化 |
| 输出/导出 | utils/output.py、utils/cli.py、core/export.py | 递归严格 JSON、退出码和原子文件输出 |

SDK 导入不发生在模块级。加载器检查已解析配置对应的 tqcenter.py 文件，捕获 DLL/模块加载故障，拒绝在同一进程中混用不同 SDK 目录。中文兼容补丁只处理已知 GBK 返回；可转债空列表补丁只捕获 IndexError，其他故障继续传播。

原生响应及 SDK 分页重组后的回执通过 ContextVar 关联到单次调用，先于供应商错误折叠记录协议证据。错误信息只附带方法、原生函数、请求类型、失败类别和 ErrorId，不保留响应正文。stock_account 的 -1 不能独立证明未登录；本机账户查询明确 ErrorId=9（未登录或不存在）或成功回执返回 -1时才视为无可用账户，其他不确定情形为 ACCOUNT_STATE_UNKNOWN。交易需要有效 ErrorId=0；明确负回执为 TdxDataError，发出后无法确认则为 TdxExecutionUnknownError。客户端、板块、缓存操作与文件下载（download_file）同样校验字典或 JSON 字符串回执，不确认则为 OPERATION_STATE_UNKNOWN。没有自动交易重试。受理、成交和最终撤单状态仍需区分，后两者需要查询确认。完整表见 [SDK_RESULT_CONTRACTS.md](SDK_RESULT_CONTRACTS.md)。

会话只在当前进程中有效。每个进程使用独立连接标识，在命令上下文退出时关闭。此版本没有常驻守护进程或多线程共享 SDK 的保证。

SDK 自身仍可能阻塞到内部超时。--debug 提供方法耗时；CLI 尚未实现所有原生调用的可抢占式端到端截止时间。后续性能和批量优化需要基于实际负载测量，不应通过自动重试交易来掩盖未知状态。

开发测试分为默认离线测试和显式 --live 真实读取。使用 uv.lock 固定开发/CI 依赖；发布包包含 skill 和 LICENSE，wheel 排除测试，sdist 保留回归源码。发布前同时校验安装后的资源、版本、两个入口及离线诊断。

批量行情/财务读取使用 read_with_metadata 显式返回本次调用的 Errors，与结果一起交给 core/quality.py；不共享全局的最后一次错误。适配器在供应商方法处理前把整数 ErrorId 规范为数字字符串。CLI 先验证请求，再连接；K 线区间查询 count=-1，日期越界直接失败。严格完整性失败为 DATA_INCOMPLETE，有数据的部分结果需显式 --allow-partial 并返回版本 1 信封，导出同样先校验。完整性只描述可观察数据，不保证交易日历覆盖。
