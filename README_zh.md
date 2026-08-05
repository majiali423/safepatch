# SafePatch

[English](README.md) | 中文

SafePatch 是一个面向小型 Python 仓库的受控代码维护 Agent，强调人工审批、受限工具、隔离测试、可审计轨迹和 fail-closed 安全边界。

公开产品名为 **SafePatch**。可安装的 Python 包与 CLI 入口仍为 `code-agent`
（见 `pyproject.toml` / `code-agent` console script）。

## 项目状态

- 包版本：**0.3.1**（`pyproject.toml`）
- [下一版本说明](docs/NEXT_RELEASE_NOTES.md) 中的可靠性改造属于 **Unreleased**，不构成已发布版本承诺
- 本仓库尚未发布开源许可证
- 主要支持：使用 pytest 的小型本地 Python 仓库
- 范围刻意受限；这是工程项目，不是通用软件工程平台

## 为什么需要 SafePatch

LLM 辅助修复经常因工程原因失败，而不只是业务逻辑错误：

- 生成补丁可能修改无关文件
- Agent 可能绕过或弱化人工审批
- 在正式工作副本上跑测试可能污染会话状态
- Docker 超时可能留下容器
- 模型输出与工具调用需要严格解析和类型边界
- 自动化测试通过并不等于请求已被独立验证
- benchmark 与 trace 应可审计，且不应过度宣传能力

SafePatch 收窄产品路径，使这些失败模式保持可见且可执行。

## 安全模型

纵深防御控制——不是“绝对安全”的承诺：

- 模型只能访问会话工作副本，不能使用开放宿主 Shell
- 不向模型提供任意 Shell、浏览器或网络工具
- 应用前必须人工审批（除非操作者显式使用 `--yes`）
- 声明的变更文件集合必须与规范化后的实际 diff 文件集合一致
- 在 Docker 中以 `--network none` 执行 pytest
- 容器内固定非 root UID
- capability drop 与 `no-new-privileges`
- 只读容器根文件系统。disposable 测试副本是唯一可写的仓库挂载；容器同时在 `/tmp` 使用受大小限制的 tmpfs 存放临时运行文件
- 每次 pytest 使用唯一的临时测试副本
- 超时后按精确容器名强制删除
- 清理失败以环境错误返回（fail-closed）
- 持久化日志与结果文本中会脱敏 disposable 宿主路径
- 修复尝试受预算限制，默认最多执行 3 次

配置的 LLM provider 仍需要宿主网络。仓库地图、选取片段、traceback 与 diff 可能发送给该提供商。

## 架构

```text
任务请求
    ↓
仓库检查
    ↓
模型工具调用
    ↓
补丁提案
    ↓
静态校验
    ↓
人工审批
    ↓
应用到工作副本
    ↓
在 disposable 副本上 Docker pytest
    ↓
验证结果
    ↓
Trace / diff / logs
```

`code_agent/` 主要模块：

| 区域 | 职责 |
|---|---|
| `controller.py` | 会话状态机 / agent loop |
| `patching/` | 提案、策略校验、预检、精确应用 |
| `workflow.py` | fail-closed 审批与验证策略 |
| `runtime/docker_pytest.py` | disposable 副本 Docker pytest 运行器 |
| `state.py` | 会话状态、计数器、审批绑定 |
| `llm.py` | provider 边界与工具调用解析 |
| `tools/` | 受限只读检查工具 |
| `eval/` | 产品循环外的隐藏评测 |
| `tracing/` | 只追加且脱敏的轨迹 |

模块细节与失败分类见 [Architecture](docs/ARCHITECTURE.md)。

## 关键行为

已实现能力（非愿景清单）：

- 结构化补丁提案（unified diff 与绑定 revision 的编辑）
- 声明/实际变更文件集合精确一致校验
- 保留真实 `a/` / `b/` 路径分量的路径规范化
- 缺少审批处理器时 fail-closed
- 类型化 schema 与冻结的 legacy tool-call 解析
- 每次 pytest 使用 disposable Docker 测试副本
- 按精确名称清理超时容器
- 持久化表面中的 cleanup / disposable 路径脱敏
- 隐藏 benchmark 在临时评测副本上隔离执行
- 无需调用付费模型即可做 benchmark manifest 漂移检查
- 跨平台 CI（unit、quality/wheel smoke、Docker E2E）

## 安装

```bash
git clone https://github.com/majiali423/safepatch.git
cd safepatch
python -m venv .venv
```

激活虚拟环境：

```powershell
# Windows
.venv\Scripts\activate
```

```bash
# Linux / macOS
source .venv/bin/activate
```

安装包（开发依赖可选，用于测试与 lint）：

```bash
python -m pip install -e .
# 或
python -m pip install -e ".[dev]"
```

pytest 隔离需要 Docker：

```bash
docker info
code-agent --build-image
code-agent --docker-check
```

运行真实模型时，将 `.env.example` 复制为 `.env` 并配置凭证（dry-run 演示不需要）。

## 示例工作流

最小确定性路径（无需 API Key）：使用小型演示仓库与冻结工具调用脚本。

```powershell
code-agent examples\buggy_calculator `
  "divide raises ZeroDivisionError on b==0; it should raise ValueError." `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

典型真实流程：

1. 将 `code-agent` 指向一个小型 Python + pytest 仓库副本
2. 提供缺陷或变更描述（参数或 `--description-file`）
3. 查看 CLI 展示的补丁提案
4. 在人工审批提示处批准或拒绝（不要使用 `--yes` 时）
5. 等待在 disposable 副本上执行 Docker pytest
6. 在会话 artifacts 目录查看 `final.diff`、pytest 日志、`summary.json` 与 `trace.jsonl`

部分退出码：`0` 成功，`3` 拒绝，`4` 环境/超时，`5` 模型输出无效，`6` 补丁不可应用，`7` 批准后基线变化，`8` 读预算耗尽，`9` 测试为绿但缺少独立请求 oracle（`TESTS_PASSED_UNVERIFIED`）。

更多细节见 [Demo](docs/DEMO.md)。

## Docker 测试隔离

- pytest 不在正式会话 `working_copy` 上执行
- 每次运行创建唯一 disposable 测试副本
- 容器使用 UID 1000 与纵深防御限制
- 测试产生的文件不会写回正式工作副本
- 超时按精确唯一容器名强制删除
- 权限残留可由受限 cleanup 容器处理
- cleanup 只挂载 disposable `working_copy` 目录
- cleanup 失败返回环境错误，而非静默成功
- 持久化日志避免宿主 disposable 绝对路径

## 评测

请区分以下层级：

| 层级 | 用途 |
|---|---|
| 单元 / 集成测试 | 产品行为（无 Docker 或使用 mock） |
| Docker E2E | 真实守护进程上的负向/安全路径 |
| Real-bug benchmark | 冻结的 BugsInPy 衍生历史任务 |
| 隐藏评测 | 产品成功后的可选检查，位于 agent loop 之外 |
| 历史模型报告 | 已记录的小样本结果，并写明边界 |

必须保留的谨慎表述：

- benchmark 样本量有限
- 结果是可审计的工程证据，不是通用能力声明
- 不代表任意仓库上的生产能力
- 历史模型结果未必能仅凭仓库文件完全复算（provider/API、采样与环境边界）
- 不可复现边界已在冻结报告中标注
- 不得把单次通过率包装成稳定产品准确率

无需模型账号即可核验已提交证据：

```bash
python examples/real_bug_benchmark/verify_published_results.py
python examples/real_bug_benchmark/verify_preflight_ablation.py
python examples/llm_benchmark/verify_manifest.py
```

需要时重建 Docker 环境：

```bash
python examples/real_bug_benchmark/verify.py
```

报告与设计说明见 `examples/real_bug_benchmark/` 与 `examples/llm_benchmark/`。

## 开发与验证

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m pytest -m docker_e2e -q
python -m ruff check code_agent tests
python examples/llm_benchmark/verify_manifest.py
git diff --check
```

可选发行包检查（需要 `.[dev]` 中的 `build`）：

```bash
python -m build
```

请勿提交 `.env`、session 目录或 `examples/llm_benchmark/results/`。

## 限制

- 只针对小型 Python + pytest 仓库
- 不向模型提供任意 Shell
- 不自动合并 PR 或推送提交
- 不支持大型仓库索引
- 不支持浏览器操作
- 不支持多 Agent 协作
- 模型质量仍约束补丁质量
- 测试通过不等于业务语义一定正确
- 人工审批仍是关键控制点
- Docker 隔离是纵深防御，不是完整安全沙箱
- benchmark 规模与范围仍然有限

## 仓库结构

```text
code_agent/     产品包（controller、patching、runtime、tools、eval）
tests/          单元、集成与 Docker E2E 测试
examples/       演示仓库、dry-run 脚本、benchmark 与冻结证据
docs/           架构、恢复、可观测性、路线图说明
```

## 文档

| 文档 | 说明 |
|---|---|
| [English README](README.md) | 英文版 |
| [Architecture](docs/ARCHITECTURE.md) | 模块与调用链 |
| [Reliability Roadmap](docs/RELIABILITY_ROADMAP.md) | 工程可靠性路线图 |
| [下一版本说明](docs/NEXT_RELEASE_NOTES.md) | 未发布的可靠性改造 |
| [Session Observability](docs/SESSION_OBSERVABILITY.md) | 会话指标 |
| [Patch Recovery](docs/PATCH_RECOVERY.md) | 安全恢复设计 |
| [Demo](docs/DEMO.md) | 端到端演示 |
| [Failure Case Study](docs/FAILURE_CASE_STUDY.md) | 诚实的隐藏测试失败案例 |
| [Real-bug Benchmark Design](examples/real_bug_benchmark/DESIGN.md) | 冻结任务协议 |

## 许可证

No open-source license has been published for this repository.

在作出许可证决定前，不应假定拥有适用法律之外的授权。
