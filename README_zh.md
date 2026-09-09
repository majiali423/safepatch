# SafePatch

[English](README.md) · [中文](README_zh.md) · [文档导航](docs/README.md)

SafePatch 是面向小型 Python 仓库的代码修复 Agent。它读取代码、提出补丁、请求人工审批，
然后在一次性的 Docker 副本中运行 pytest。每次会话都会保留补丁、测试日志、结构化摘要和执行轨迹。

**推荐入口：**[五分钟项目介绍](docs/RECRUITER_BRIEF.md) ·
[确定性演示](docs/DEMO.md) · [架构说明](docs/ARCHITECTURE.md)

## 工作流程

```text
导入仓库 → 基线测试 → 模型读取与提案
    → 策略校验 → 精确预检 → 绑定 hash 的审批
    → 应用补丁 → Docker 测试 → 摘要、轨迹与 diff
```

- **受控修改：**支持精确 diff 和绑定文件版本的文本替换，默认保护已有测试和配置文件。
- **独立预算：**探索读取、补充证据、重新生成补丁与实际修复轮次分别计数。
- **先审批后执行：**审批同时绑定补丁和工作树 hash，内容变化后不能沿用旧审批。
- **隔离测试：**使用非 root 用户、关闭网络、只读容器根目录和一次性仓库副本。
- **明确验证含义：**基线与修改后测试都通过、但缺少独立需求判据时，返回
  `TESTS_PASSED_UNVERIFIED`，不直接宣称需求已完成。

## 快速开始

需要 Python 3.10+ 和正在运行的 Docker。

```bash
git clone https://github.com/majiali423/safepatch.git
cd safepatch
python -m venv .venv
```

Windows PowerShell 执行 `.venv\Scripts\Activate.ps1`；Linux/macOS 执行
`source .venv/bin/activate`。随后运行：

```bash
python -m pip install -e ".[dev]"
safepatch --build-image
safepatch --docker-check
safepatch examples/buggy_calculator "divide must raise ValueError when b is zero" --dry-run-script examples/dry_run_fix_divide.json --yes
```

演示使用固定模型响应，不需要 API key。去掉 `--yes` 可以交互审批。
原始仓库保持不变；修复后的工作副本和会话产物位于命令输出的目录中。

使用真实模型时，将 `.env.example` 复制为 `.env`、配置服务商并去掉 `--dry-run-script`。
代码片段、报错和 diff 会发送给该服务商。完整操作见[演示指南](docs/DEMO.md)。

## 架构

| 组件 | 职责 |
| --- | --- |
| `TaskController` | 仓库导入、基线测试、审批和会话生命周期 |
| `AnalysisLoop` | 模型调用、读取、补充证据与提案重试 |
| `PatchGate` | 补丁预检与审批绑定 |
| `RepairExecutor` | 应用已审批补丁并运行测试 |
| `SessionFinalizer` | 汇总指标和写出会话产物 |

[架构指南](docs/ARCHITECTURE.md)说明状态流转与技术取舍；
[分析循环回放](docs/ANALYSIS_LOOP.md)提供可复现的组件走读与行为回归检查。

## 评测

已发布的 21 次真实缺陷评测记录了 **17/21** 次隐藏测试成功。无需模型账户即可核验记录：

```bash
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
python examples/llm_benchmark/verify_manifest.py
```

这些是小样本历史实验。另一次预检对照的结果为 11/14 与 9/14，但没有实际触发预检拒绝，
因此不能据此证明预检提升了成功率。后续 9/9 扩展实验有独立报告，不属于上述核验数据包。
详见[评测指南](docs/EVALUATION.md)和[失败案例](docs/FAILURE_CASE_STUDY.md)。

## 支持范围

自动修复面向小型本地 Python + pytest 仓库，配置必须符合[支持子集](docs/PYTEST_SUPPORT.md)。
目前不支持包含可执行代码的 `conftest.py`，普通 fixture 也包括在内；这类仓库执行基线后，
会在模型分析与补丁应用之前停止。

模型没有 shell、浏览器、网络或直接写入工具。Docker 能降低执行风险，但不是完整的安全沙箱；
测试通过也不代表业务语义必然正确。SafePatch 本身不会推送提交或合并 PR。

## 开发

```bash
python -m pytest -q -m "not docker_e2e and not packaging_network"
python -m ruff check code_agent tests devtools
python -m mypy
```

[开发指南](docs/DEVELOPMENT.md)包含 Docker、安装验证、回放数据与仓库维护规则。
包版本为 **0.3.1**；默认分支包含开发中的改动，不等同于已打标签的发行版本。

```text
code_agent/  产品实现
tests/       行为、安全和运行环境回归测试
devtools/    确定性会话采集与比较
examples/    可运行演示、评测任务与已发布证据
docs/        使用、架构和评测说明
```

采用 [MIT 许可证](LICENSE)。Copyright (c) 2026 Jiali Ma.
