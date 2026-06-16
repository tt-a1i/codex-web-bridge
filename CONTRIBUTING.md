# 贡献指南

这个仓库的发布物是 `skills/review-gate`。skill 目录内只放 Codex 执行任务需要的内容；仓库级维护说明放在根目录。

## 维护校验

提交或推送前至少运行：

```bash
find . -path ./.git -prune -o -maxdepth 5 -type f -print | sort
git status --short
git diff --stat
python3 -m py_compile \
  skills/review-gate/scripts/build_context_packet.py \
  skills/review-gate/scripts/scrub_context.py
ruby -ryaml -e 'front=File.read("skills/review-gate/SKILL.md").split(/^---\s*$/)[1]; YAML.safe_load(front).fetch("name"); YAML.load_file("skills/review-gate/agents/openai.yaml").fetch("interface"); puts "yaml OK"'
python3 skills/review-gate/scripts/build_context_packet.py \
  --repo . \
  --mode implementation-review \
  --decision "Verify review-gate before release" \
  --scope "Current repository state" \
  --output /tmp/review-gate-context.md
python3 skills/review-gate/scripts/scrub_context.py /tmp/review-gate-context.md --fail-on block
git diff --check
```

如果本机装了 `PyYAML`，也运行官方 skill 校验脚本：

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py skills/review-gate
```

如果本机装了 secret 扫描工具，也运行：

```bash
gitleaks detect --no-git --source . --redact --verbose
trufflehog filesystem . --no-update --fail
```

## 修改原则

- 默认 README 使用中文。
- `skills/review-gate/SKILL.md` 保持短而可执行，把长模板和细则放进 `references/`。
- 只有重复、易错、需要确定性的步骤才放进 `scripts/`。
- 不要在 skill 目录里增加 README、安装指南、发布日志等维护文档。
- 修改外发逻辑时，优先保证“不发送 BLOCK scrub finding”这个安全边界。
