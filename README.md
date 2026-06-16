# Review Gate Skill

`review-gate` 是一个 Codex Skill，用来在编码前、合并前、发版前、回复 PR 评论前，或者发布评估/报告结论前，跑一轮结构化的二次审查关口。

它保留了原 `pro` skill 的核心目标：把经过收窄和脱敏的仓库上下文交给外部强模型 reviewer（例如 Extended Pro），再把 reviewer 的建议带回本地代码、diff、PR 状态和测试结果里逐条核验。这个版本进一步加强三件事：

- 自动从 Git 状态、diff、未跟踪文件和证据文件生成 context packet；
- 外发前先跑本地 scrub gate，阻断常见 token、API key、private key 等敏感内容；
- reviewer 的每条建议都必须被本地证据归类为 `FIX`、`DEFER`、`DISMISS` 或 `QUESTION`。

## 安装

从 GitHub 安装：

```text
Use $skill-installer to install https://github.com/tt-a1i/review-gate-skill/tree/main/skills/review-gate
```

安装后重启 Codex。

本地开发时，也可以在仓库根目录用相对路径安装：

```text
Use $skill-installer to install ./skills/review-gate
```

## 使用

```text
Use $review-gate to review this implementation before shipping.
```

支持四类审查：

- `plan-hardening`：编码前检查方案、阶段顺序、缺失决策和范围风险；
- `implementation-review`：发版/合并前检查 diff、测试、边界条件和回归风险；
- `pr-comment-resolution`：检查 PR 评论、CodeRabbit 建议或 reviewer thread 是否真正处理完整；
- `eval-methodology`：检查指标、数据集、对比方法、报告结论和混杂因素。

## 工作流

1. 明确本次要 gate 的决策：审查类型、范围、目标 backend、哪些内容不审。
2. 用 `build_context_packet.py` 生成上下文包。
3. 用 `scrub_context.py` 做外发前扫描。
4. 把 scrub 通过后的上下文交给 Extended Pro 或其他明确批准的 reviewer。
5. 等待完整回复，不因为 Pro 慢就过早放弃。
6. 回到本地逐条 reconciliation，所有结论都要有文件、命令、PR 链接或明确缺失证据。
7. 输出 `PASSED`、`BLOCKED`、`LOCAL-ONLY` 或 `INCOMPLETE`。

## 脚本

生成 context packet：

```bash
python3 skills/review-gate/scripts/build_context_packet.py \
  --repo . \
  --mode implementation-review \
  --decision "Decide whether this diff is ready to ship" \
  --scope "Current implementation diff" \
  --output /tmp/review-gate-context.md
```

扫描敏感内容：

```bash
python3 skills/review-gate/scripts/scrub_context.py \
  /tmp/review-gate-context.md \
  --fail-on block
```

默认生成的 context packet 不包含本机仓库绝对路径，减少外发时泄漏本地用户名或目录结构。确实需要时可传 `--include-repo-path`。

## 目录

```text
skills/review-gate/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── prompt-templates.md
│   └── reconciliation.md
└── scripts/
    ├── build_context_packet.py
    └── scrub_context.py
```

## 隐私边界

`scrub_context.py` 只能发现常见 secret 形态，不是完整 DLP 系统。外发前仍然要人工确认上下文是否包含客户数据、内部链接、日志、截图、账号信息或其他不该发送的内容。

## 关系与授权

这个项目受 [christianaranda/codex-pro-skill](https://github.com/christianaranda/codex-pro-skill) 启发，但实现目标是一个更通用的 `review-gate`：自动 context packet、scrub gate、证据化 reconciliation。

代码以 MIT License 发布。
