# T17 与 Entities 解压、验证和冻结记录

> 日期：2026-09-04  
> 执行环境：Conda `tls`，Python 3.10.14  
> 数据代码版本：news-tls `b79c0d1c32263d685570b326f36e35d02b292c21`

## 一、输入压缩包

| 数据集 | ZIP | ZIP 字节数 | SHA-256 |
|---|---|---:|---|
| T17 | `C:\Users\hwzhao7\Downloads\t17-20260904T050704Z-1-001.zip` | 51,159,815 | `4855ff29f5da561f150ec724e030af9bacf79703d97701b5e64f310276848410` |
| Entities | `C:\Users\hwzhao7\Downloads\entities-20260904T045721Z-1-001.zip` | 508,178,816 | `de9a21c30edccf33b14b4086a30c92931ae5cbd76c2df06bdaea5c61f9d5b8a4` |

首先使用 Python `zipfile.testzip()` 检查 ZIP CRC，两个压缩包均返回 `None`，即没有发现损坏成员。T17 包含 27 个文件，Entities 包含 141 个文件，恰好分别对应 9×3 和 47×3 的主题必要文件结构。

## 二、安全解压与目录处理

没有直接覆盖旧目录，而是先解压到：

- `D:\paper\closed_corpora_staging\t17_20260904\t17`
- `D:\paper\closed_corpora_staging\entities_20260904\entities`

全量校验通过后，旧残缺目录被重命名保留：

- `D:\paper\closed_corpora_download\t17_partial_20260904`
- `D:\paper\closed_corpora_download\entities_partial_20260904`

验证后的完整目录再移动到正式位置：

- `D:\paper\closed_corpora_download\t17`
- `D:\paper\closed_corpora_download\entities`

本过程没有删除旧数据，后续确认不再需要时可人工清理两个 partial 备份和空的 staging 外层目录。

## 三、全量语料校验结果

校验不是只数文件，而是逐条读取所有 `.jsonl.gz`，检查 gzip CRC、JSON 解析、文章必要字段、关键词列表和全部金时间线。

| 数据集 | 主题 | 文章 | 参考时间线 | 错误 |
|---|---:|---:|---:|---:|
| T17 | 9/9 | 4,203 | 19 | 0 |
| Entities | 47/47 | 51,183 | 47 | 0 |

报告文件：

- `chronos_repro/artifacts/t17_zip_full_validation.json`
- `chronos_repro/artifacts/entities_zip_full_validation.json`

## 四、冻结快照

| 数据集 | 快照 ID | 文件数 | 总字节数 |
|---|---|---:|---:|
| T17 | `2704b6b058774e15` | 27 | 51,309,614 |
| Entities | `25ac73e52bc93b3b` | 141 | 508,157,475 |

快照路径：

- `D:\paper\chronos_repro\snapshots\t17\2704b6b058774e15`
- `D:\paper\chronos_repro\snapshots\entities\25ac73e52bc93b3b`

冻结完成后又独立执行 `verify`，逐文件 SHA-256 与 manifest 均一致。验证报告为：

- `chronos_repro/artifacts/t17_snapshot_verify.json`
- `chronos_repro/artifacts/entities_snapshot_verify.json`

## 五、当前结论

此前“不完整”的结论仅适用于旧的中断下载目录，现在已经失效。Crisis、T17、Entities 三个闭域数据集都有完整且可验证的固定快照，可以进入统一闭域索引和基线实验。当前自动测试仍为 `9 passed`。
