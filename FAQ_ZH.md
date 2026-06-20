# 常见问题

## 只安装 Skill 就能当 MCP 用吗？

不能。Skill 只提供 Bridge Mode 的操作说明和脚本。MCP Connector Mode 需要完整项目分发里的 Rust 服务：

```bash
./bin/codex-connector init
./bin/codex-connector serve
```

## ChatGPT 网页端应该填哪个 URL？

填公开 HTTPS tunnel 的 `/mcp` endpoint，例如：

```text
https://your-tunnel.example.com/mcp
```

`public_base_url` 配置里只填 origin，不带 `/mcp`：

```bash
./bin/codex-connector init --public-base-url https://your-tunnel.example.com --force
```

## 第一次用应该选什么权限？

建议先用只读：

```bash
./bin/codex-connector init --root /path/to/project --trust-level readonly --force
```

如果希望 ChatGPT 能写计划但不改源码，用：

```bash
./bin/codex-connector init \
  --root /path/to/project \
  --trust-level execute \
  --tool-mode minimal \
  --write-mode handoff \
  --shell-mode off \
  --force
```

如果确定要让它改源码，但不想给任意 shell，用：

```bash
./bin/codex-connector init \
  --root /path/to/project \
  --trust-level execute \
  --write-mode workspace \
  --shell-mode safe \
  --force
```

## `trust_level` 和 `write_mode` 有什么区别？

`trust_level` 是权限上限，`write_mode` 是源码写入开关。即使
`trust_level=execute`，只要 `write_mode=handoff` 或 `write_mode=off`，源码写入、
worktree、publish、PR 创建等工具也不会开放。

## `shell_mode=safe` 能跑什么？

它只允许常见检查命令，例如：

- `git status` / `git diff` / `git log`
- `cargo test` / `cargo check` / `cargo clippy`
- `npm test`
- `npm run test|lint|typecheck|check|build`
- `pytest`
- `go test`

它会拒绝多行命令、shell metacharacter、绝对路径、`..`、敏感路径和不在允许列表里的命令。

## 可以不用 OAuth 吗？

只建议用于短时间 readonly smoke test：

```bash
./bin/codex-connector init --root /path/to/project --no-owner-token --force
```

公网持久使用应该走 OAuth owner approval。不要把 no-auth connector 长期挂在公网 tunnel 上。

## 如何验证已经连通？

1. 本地运行 `./bin/codex-connector doctor`。
2. 启动 `./bin/codex-connector serve`。
3. 在 ChatGPT 创建 connector，URL 填 `https://.../mcp`。
4. 新开 ChatGPT 对话，让它调用 `open_workspace`，再 `read README.md`。

如果能返回 README 标题和工具结果，链路就跑通了。
