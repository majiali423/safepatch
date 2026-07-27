# 第一版验收报告

日期：2026-07-27

## 0. 验证层次（务必区分）

| 层次 | 含义 | 状态 |
|---|---|---|
| 1. 单元测试 | validator / workspace / repo_map / patch 等 | **已通过** |
| 2. mock Docker 的集成测试 | 测试替身代替 `DockerPytestRunner` | **已通过** |
| 3. 本地 pytest 替代路径 | `LocalPytestRunner` 在宿主跑 pytest | **已通过**（仅测试用，不是产品路径） |
| 4. 真实 Docker Runtime E2E | Desktop + `code-agent-pytest:local` | **P3 负向 E2E**：见 `tests/test_docker_e2e_negative.py`（`@pytest.mark.docker_e2e`） |

**不要把第 2、3 层描述成「真实 Docker E2E 已通过」。**

**评测说明：** `sample02_normalize` 使用固定 dry-run 硬编码补丁验证隐藏测试基础设施，**不代表真实 LLM 能力表现**（见 `examples/eval_tasks/sample02_normalize/NOTE.md`）。

---

## 1. 实现审计

### 1.1 项目目录树

```text
code_agent/
  __init__.py
  __main__.py
  cli.py
  controller.py          # TaskController（状态机）
  state.py
  llm.py
  repository/
    workspace.py
    repo_map.py
    search.py
    git_diff.py
  tools/
    list_tree.py
    read_file.py
    search_text.py
    search_symbol.py
    registry.py
  patching/
    proposal.py
    validator.py         # PolicyValidator
    applier.py           # PatchApplier
  runtime/
    docker_pytest.py     # DockerPytestRunner
    Dockerfile.pytest
  tracing/
    recorder.py
examples/
  buggy_calculator/
  dry_run_fix_divide.json
tests/
  test_*.py
docs/
  WALKTHROUGH.md
  ACCEPTANCE_REPORT.md
pyproject.toml
README.md
```

### 1.2 核心文件职责

| 文件 | 职责 |
|---|---|
| `cli.py` | CLI 入口、审批交互、`--build-image` / `--docker-check` |
| `controller.py` | `TaskController` 状态机编排 |
| `state.py` | `TaskSession` / `SessionStatus` / `PatchProposal` / `TestResult` |
| `llm.py` | OpenAI 兼容客户端 + dry-run 脚本适配器 |
| `repository/workspace.py` | 导入副本、`safe_resolve` 防逃逸 |
| `repository/repo_map.py` | AST Repo Map |
| `repository/search.py` | 文本/符号搜索 |
| `repository/git_diff.py` | 快照对比 diff |
| `tools/*` | Agent 只读工具 |
| `patching/proposal.py` | 结构化 PatchProposal 解析 |
| `patching/validator.py` | 策略校验（禁止路径/规模） |
| `patching/applier.py` | unified diff 应用与回滚 |
| `runtime/docker_pytest.py` | 控制器独占的 Docker pytest |
| `tracing/recorder.py` | `trace.jsonl`（含脱敏） |

### 1.3 完整调用链

```text
CLI (cli.main)
  → docker preflight (DockerPytestRunner.preflight)
  → TaskController.run
      → Workspace.import_repository  → working_copy
      → RepoMap.build_repo_map
      → baseline: DockerPytestRunner.run_pytest
      → analyze loop:
          LLMClient / DryRun script
            → tools (list/read/search/map/diff)
            → propose_patch → PatchProposal
            → PolicyValidator.validate
          → AWAITING_APPROVAL (CLI approve/reject)
          → PatchApplier.apply
          → DockerPytestRunner.run_pytest
      → TraceRecorder + artifacts (final.diff, summary.json, logs)
```

命名对照：规格口中的 TaskController / PolicyValidator / PatchApplier  
在代码中分别为 `TaskController`、`PolicyValidator`、`PatchApplier`。

### 1.4 状态机

| 状态 | 进入条件 |
|---|---|
| CREATED | session 创建 |
| IMPORTED | 副本导入成功 |
| BASELINE_TESTED | 基线 pytest 完成（非环境错误） |
| ANALYZING | 基线后 / 测试失败后继续分析 |
| PATCH_PROPOSED | 模型提出合法补丁 |
| AWAITING_APPROVAL | 等待用户 |
| PATCH_APPLIED | 补丁应用成功 |
| TESTING | 正在跑 pytest |
| SUCCEEDED | 测试通过 |
| FAILED_MAX_ATTEMPTS | 3 次补丁测试仍失败 |
| REJECTED | 用户拒绝 |
| TEST_ENVIRONMENT_ERROR | Docker/镜像/pytest 不可用 |
| TEST_TIMEOUT | pytest 超过 120s（独立于断言失败） |
| ERROR | 内部/LLM 错误 |

转换要点：

- 测试失败且 `attempts_used < 3` → `ANALYZING`
- 用户拒绝 → 立即 `REJECTED` 终止
- 基线不计入 `attempts_used`

### 1.5 真实实现 vs mock / dry-run / 测试替代

| 组件 | 性质 |
|---|---|
| Workspace / RepoMap / Validator / Applier / Trace | **真实实现** |
| DockerPytestRunner | **真实实现**（本机 Docker 未验证） |
| LLMClient 调 API | **真实实现**（需 API Key） |
| `--dry-run-script` | **Dry-run 适配器**（无模型，重放 JSON 动作） |
| `tests/test_end_to_end_local.py` 的 `LocalPytestRunner` | **测试替代**，不是产品路径 |
| 安全测试中的 monkeypatch preflight/timeout | **mock** |

### 1.6 尚未验证与已知限制

- 真实 Docker Desktop E2E 未跑通
- Windows 符号链接测试在无权限主机上会 skip
- 第一版不装仓库依赖（镜像内仅 pytest）
- 仅 UTF-8 `.py`；无 MCP / Issue / PR / 多 Agent

---

## 2. Spec 符合性（PASS / PARTIAL / FAIL）

| # | 条目 | 结果 | 说明 |
|---|---|---|---|
| 1 | 原始仓库不会被修改 | **PASS** | 只写 working_copy；有测试锁定 |
| 2 | 只能访问 working_copy | **PASS** | 工具均经 `safe_resolve` |
| 3 | 路径穿越/绝对路径被阻止 | **PASS** | 有测试 |
| 4 | Agent 无任意 Shell | **PASS** | 工具白名单无 shell |
| 5 | Agent 无法直接调用 Docker | **PASS** | 仅控制器调用 runner |
| 6 | 只读工具最多 12 次 | **PASS** | `TaskSession.max_read_actions` |
| 7 | 基线不计入三次修复 | **PASS** | `attempts_used` 仅在批准后递增 |
| 8 | 每轮补丁重新审批 | **PASS** | 循环内每次 `approve()` |
| 9 | 用户拒绝立即终止 | **PASS** | → `REJECTED` |
| 10 | unified diff 严格解析 | **PASS** | 上下文不匹配则失败回滚 |
| 11 | 禁止 .git/.env/Dockerfile/CI/依赖 | **PASS** | PolicyValidator |
| 12 | 单轮最多 5 文件 | **PASS** | |
| 13 | 单轮行数硬限制 | **PASS** | 300 行 |
| 14 | assertion failure vs 环境错误 | **PASS** | `error_kind` + 独立状态 |
| 15 | 第三次失败强制终止 | **PASS** | `FAILED_MAX_ATTEMPTS` |
| 16 | trace 不记录 API Key / 思维链 | **PASS** | 键与字符串脱敏；不记录 hidden CoT |
| 17 | 生成 final.diff/summary/trace/日志 | **PASS** | artifacts 导出 |

验收过程中已修复的 PARTIAL：

- 符号链接逃逸：导入跳过 symlink + `safe_resolve` 显式检测
- 超时独立状态：新增 `TEST_TIMEOUT` / `error_kind="timeout"`
- Docker 预检：`docker info` + `--docker-check`；不可用时 CLI 清晰报错、不抛栈
- 控制器命名对齐：`TaskController` / `PolicyValidator` / `PatchApplier`

---

## 3. 真实 Docker 尚待本地执行的步骤

见本文件第 4 节与 README。在 Docker Desktop 启动前，**不得**声称 Docker E2E 通过。
