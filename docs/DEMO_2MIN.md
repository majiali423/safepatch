# 2-minute Demo Script（buggy_calculator）

目标：用一个最小任务展示 SafePatch v0.3 的完整闭环（约 90–120 秒口播 + 录屏）。

仓库为 **private**：录屏前请在浏览器登录 GitHub，或只录终端 Demo。

## 录制前检查（~15s，可剪掉）

```powershell
cd C:\Users\lili\project3
code-agent --docker-check
```

期望：`OK: docker ok; image=code-agent-pytest:local`

## 口播提纲（中文，约 2 分钟）

| 时间 | 画面 | 你说什么 |
|---|---|---|
| 0:00–0:20 | README Evaluation 表 / 终端标题 | 这是 SafePatch：面向小型本地 Python+pytest 仓的受控修复 Agent。评测是冻结 12 题 ×3=36 runs；下面用一个最小除零 Bug 演示闭环。 |
| 0:20–0:40 | 打开 `examples/buggy_calculator/calculator.py` | 业务要求除零抛 `ValueError`，当前会抛 `ZeroDivisionError`。 |
| 0:40–1:40 | 跑下面命令 | 走：导入 working_copy → 基线 Docker pytest 失败 → 只读工具 → propose_patch → **policy + exact preflight** → 审批 → apply → pytest 通过。强调：原仓不被改；repair 只在 apply 成功后计数。 |
| 1:40–2:00 | summary / artifacts | 看 `SUCCEEDED`、`attempts_used=1`、`patch_preflight_successes=1`；产物有 `final.diff` 与 `trace.jsonl`。限制一句：小仓 + 单模型，不宣称生产通用。 |

## 推荐命令（dry-run，稳定可复现，适合 Demo）

```powershell
code-agent examples\buggy_calculator `
  "divide 在除数为 0 时应抛出 ValueError，现在抛出 ZeroDivisionError，请修复。" `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

期望关键日志顺序：

1. Baseline failed（`test_divide_by_zero_raises_value_error`）
2. `get_repo_map` → `read_file` ×2 → `propose_patch`
3. Auto-approve（带 `patch_hash` / `wt_hash`）
4. Docker pytest attempt 1 → All tests passed
5. `status: SUCCEEDED`，`attempts_used: 1`

## 可选：真实 DeepSeek（更“真”，但有采样波动）

去掉 `--dry-run-script`，需 `.env` 已配置。Demo 前先私下跑通一次。

## 录屏建议

- 终端字体放大；窗口只留终端 + 可选 VS Code 侧边 `calculator.py`
- 不要展示 `.env` / API Key
- 不要滚动到巨大 `trace.jsonl`；点到 `summary` 与 `final.diff` 即可

## 本机已试跑（本次）

- Docker check：OK
- dry-run Demo：~6s，`SUCCEEDED`，`patch_preflight_successes=1`，`attempts_used=1`
