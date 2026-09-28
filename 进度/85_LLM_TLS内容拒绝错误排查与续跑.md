# LLM-TLS 内容拒绝错误排查与续跑

2026-09-20，用户要求“检查错误，继续跑”。

## 原因确认

原批次在 00:51 左右停止调度，00:55 完成保存与导出。停止时：

- 20,095 个独立请求已返回并保存。
- 52,022 个待处理请求。
- 1 个失败请求，Entities / Hu_Jintao，job_id：
  `11f44db4be14f42b62bff363d357a608a9fc5e0a5da472611ca1565efdc49677`。
- 3 个模型输出格式异常，原响应已保留；与本次批次退出不是同一问题。

对失败请求保持输入、模型和参数完全不变，进行一次诊断重放，复现：

```json
{"http_status":400,"error":{"message":"Content Exists Risk","type":"invalid_request_error","code":"invalid_request_error"}}
```

这是服务商对该请求的内容拒绝，不是网络故障或已确认的余额不足。
旧网络包装器把所有不可重试错误统一取消整个工作队列，导致一篇文章的拒绝停止全批。
收尾状态又被其他等待线程的取消覆盖，留下 `network_wait_cancelled`，掩盖了根因。

诊断原始记录：
`chronos_repro/artifacts/llm_tls_extraction_v1/resume_audit_20260920/failed_request_diagnostic.json`。
续跑前的状态/导出/传输策略文件也已备份到同一目录。

## 修复

- 仅当明确匹配 HTTP 400、`Content Exists Risk`、`invalid_request_error` 时，
  把该请求保留为 `ContentRejectedError`，不自动重试、不改写请求，不停止其他文章。
- 余额、鉴权以及其他不可重试错误仍停止全批；普通 HTTP 限流保留原有重试。
- 断网暂停、每 30 秒连通性检查和恢复续跑保留。
- 新增 `api_errors.jsonl`，记录 HTTP 状态、服务商原因、请求哈希和处理动作。
- 保留第一次全局停止的根因，后续等待线程取消不再覆盖它。
- 已失败的 Hu_Jintao 请求依照已复现证据重分类为 `ContentRejectedError`，仍是失败记录。
- 新增 `--skip-prepare`：复用已完成的输入准备，直接接续待处理请求。

主提取脚本和原 API 客户端 SHA-256 与 run identity 均一致；没有改变提示词、模型、文章输入或缓存键。
没有改动原文检索和 VERIFY；没有通过重写敏感请求绕过服务商拒绝。

## 验证和续跑

`test_llm_tls_network_pause.py` 与 `test_llm_tls_extraction.py` 共 **15 项通过**。
新增测试确认：内容拒绝不重试该文档且下一篇仍能执行；401、其他400、403等错误仍停止；根因不被取消覆盖。

已使用隐藏后台进程启动：

```powershell
D:/miniforge/envs/tls/python.exe -u scripts/run_llm_tls_extraction_suite.py --skip-prepare
```

新 supervisor 启动 PID：30008。实时状态以 `suite_status.json` 和 `progress.json` 为准。
继续原 32 并发配置。已有 20,095 个结果不会重新请求；52,022 个待处理请求继续推进。
那 1 个服务商拒绝项单独保留，不把失败计作成功。
