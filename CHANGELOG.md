# 更新日志

本项目遵循语义化版本风格记录用户可见变更。

## [Unreleased]

### Added

- 新增 `skills/review-gate`，提供结构化二次审查 gate。
- 新增 `build_context_packet.py`，从 Git 状态、diff、未跟踪文件和证据文件生成受限上下文包。
- 新增 `scrub_context.py`，在外发前扫描常见 secret 和敏感传输风险。
- 新增 prompt 模板与 reconciliation 规则，覆盖 plan、implementation、PR 评论处理和 eval/reporting 方法审查。

### Changed

- 默认 README 改为中文，并把项目定位收敛为独立的 `review-gate` skill。
