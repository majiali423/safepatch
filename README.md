# code-agent — MVP v0.2

面向**小型本地 Python + pytest 仓库**的可靠性导向、受控代码修复 Agent。

**状态：MVP v0.2 Done**（格式重试 · 测试完整性 · 隐藏评测隔离 · Docker 负向 E2E）

用户输入本地仓库路径 + Bug/需求描述后，系统完成：

仓库导入 → Repo Map / 基线测试 → 工具循环 → 补丁提案 → 策略校验 → 人工审批 → Docker pytest → 最多三轮修复 → 导出产物

- 发布说明：[`docs/V0.2_RELEASE_NOTES.md`](docs/V0.2_RELEASE_NOTES.md)
- 走读：[`docs/FIRST_VERSION_WALKTHROUGH.md`](docs/FIRST_VERSION_WALKTHROUGH.md)
- 验收底稿：[`docs/ACCEPTANCE_REPORT.md`](docs/ACCEPTANCE_REPORT.md)

---

## 架构与状态 / 评测流程

```mermaid
flowchart TD
  A[导入 working_copy] --> B[Repo Map + 基线 Docker pytest]
  B --> C{基线通过?}
  C -->|是| Z[SUCCEEDED]
  C -->|否| D[LLM 工具循环]
  D --> E{输出合法?}
  E -->|否| F[格式重试 ≤2<br/>不计入 repair attempt]
  F -->|耗尽| X[MODEL_OUTPUT_INVALID]
  F --> D
  E -->|tool / finish| D
  E -->|propose_patch| G[PolicyValidator<br/>含测试完整性]
  G -->|策略拒绝| D
  G -->|通过| H[人工审批]
  H -->|拒绝| R[REJECTED]
  H -->|批准| I[应用补丁 + Docker pytest]
  I -->|超时/环境| T[TEST_TIMEOUT / TEST_ENVIRONMENT_ERROR]
  I -->|失败且 attempts&lt;3| D
  I -->|失败且耗尽| M[FAILED_MAX_ATTEMPTS]
  I -->|通过| Z[产品 SUCCEEDED]
  Z -.->|仅评测器| P[eval_temp_copy + hidden pytest]
  P --> Q{EvalStatus}
  Q -->|hidden 通过 / 未配置| S1[eval SUCCEEDED]
  Q -->|hidden 失败| S2[HIDDEN_TESTS_FAILED]
```

要点：

- **产品**只看 `summary.status`（`SessionStatus`）。
- **评测**另有 `metrics.eval_status`（`EvalStatus`）；hidden 失败不反馈 Agent、不触发新修复轮。
- 格式重试与 repair attempt（最多 3）分离。

---

## 能力边界（请按此诚实对外表述）

### 已具备并已验证

- 小型本地 Python 仓导入 `working_copy`（原仓不被修改）
- AST Repo Map + 受限只读工具 + dry-run / LLM 工具循环
- unified diff 提案、策略校验（路径 / 规模 / **测试完整性**）、人工审批后应用
- 结构化输出非法时的 format retry + 脱敏截断 trace；CLI 退出码 5
- 控制器固定参数的 Docker pytest：禁网、512MB、1 CPU、pids=128、非 root、`--rm`、默认 120s
- **真实 Docker 负向 E2E**：禁网生效、非 root、超时独立分类、容器清理
- 隐藏测试评测与产品运行**物理隔离**（`<task>.hidden/` + `eval_temp_copy`）
- 产物：`final.diff`、公开测试日志、`trace.jsonl`；评测另有 `hidden.log` / `eval_trace.jsonl` / `metrics.json`

### 评测说明（勿误读）

- 回归：**74 passed, 1 skipped**
- 跳过：`test_symlink_escape_fails`（当前 Windows 主机不允许创建 symlink）
- `sample02_normalize` 使用**固定 dry-run 硬编码补丁**验证隐藏测试基础设施，**不代表真实 LLM 表现**（见 `examples/eval_tasks/sample02_normalize/NOTE.md`）

### 当前不支持（v0.2 冻结）

- MCP、GitHub Issue、自动 clone、自动 PR / push / commit
- 多 Agent、前端、多语言
- 容器内在线安装仓库依赖、任意 Shell、浏览器
- Hidden 失败回灌产品循环（刻意禁止）

---

## 安装

```bash
pip install -e ".[dev]"
```

## 真实 Docker 演示

```powershell
docker info
code-agent --build-image
code-agent --docker-check

code-agent examples\buggy_calculator `
  "divide 函数在除数为 0 时应该抛出 ValueError，但现在抛出了 ZeroDivisionError，请修复。" `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

配置真实模型（OpenAI 兼容，含 DeepSeek）：

```powershell
copy .env.example .env
# 编辑 .env
```

```env
OPENAI_API_KEY=sk-你的密钥
OPENAI_BASE_URL=https://api.deepseek.com
CODE_AGENT_MODEL=deepseek-chat
```

`.env`、session 目录、`examples/eval_tasks/results/` 已被 gitignore。真实模型运行时去掉 `--dry-run-script`。

可选高风险开关：`--allow-test-changes`（默认关闭；仍禁止删测试 / 改 conftest 与 pytest 配置 / skip 与 `assert True` 等）。

隐藏评测样例（dry-run，需 Docker）：

```powershell
python examples\eval_tasks\run_hidden_samples.py
```

## 架构借鉴

| 开源项目 | 借鉴点 |
|---|---|
| mini-SWE-agent | 线性 Agent 循环 |
| Aider | Repo Map + unified diff |
| SWE-agent | TaskSession / trace |
| OpenHands | Agent 与 Runtime 分离 |
| goose | 写操作人工审批 |
