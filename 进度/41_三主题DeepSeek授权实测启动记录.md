# 三主题 DeepSeek 授权实测启动记录

日期：2026-09-14。

## 授权与边界

用户已确认第 40 号文档所列调用范围：Yemen、MJ、David Bowie 三个冻结主题，使用 `https://api.deepseek.com` 的 `deepseek-v4-flash`；每主题累计最多 400 次 HTTP 请求，三主题最多 1,200 次，重试计入额度。正文每片段最多 3,200 字符、前文最多 400 字符，另含标题、证据引用和派生状态。Gold 不发送给模型，仅本地评测。

现有密钥文件已定位为 `D:\paper\chronos_repro\.env`。仅运行程序读取密钥，未显示、复制或重新写入密钥。

## 启动状态

已在 `D:\miniforge\envs\tls\python.exe` 下启动顺序执行批次：

1. Crisis / yemen。
2. T17 / mj。
3. Entities / David_Bowie。

启动后第一次检查，Yemen 账本显示 `requests_started=1`、`request_limit=400`、`balance_stop=false`。这是已开始请求的计数，不代表请求成功，也不是完成比例。

每主题完成后才能启动下一个。任何非零退出状态都会暂停批次供排查；任一账本记载余额不足，禁止继续启动其他主题。旧 Egypt 的余额停止记录和历史轨迹未被重置或覆盖。

## 配置与输出

使用 `chronos_repro/configs/tisa_multitopic_v7_diagnostic.json` 中绑定的配置。

输出目录：

- `chronos_repro/artifacts/tisa_v7_diagnostic/crisis_yemen`
- `chronos_repro/artifacts/tisa_v7_diagnostic/t17_mj`
- `chronos_repro/artifacts/tisa_v7_diagnostic/entities_David_Bowie`

三个主题全部成功结束后，批次才调用离线多主题评测脚本，输出 `chronos_repro/artifacts/tisa_v7_diagnostic_report_01.json`。如果中途停止，则不会伪造完整汇总。

## 结果解释

此文件记录启动与授权，不是完成报告。目前不能声称新版达到 70% 严格 Gold 日期召回。以之后生成的 trajectory、evaluation、manifest 和累计账本为准。

这些主题已被历史诊断观察，尤其 Yemen 与旧 seed test 划分存在历史使用冲突。因此仅作诊断，不用于训练、checkpoint 选择或声称干净测试泛化。
