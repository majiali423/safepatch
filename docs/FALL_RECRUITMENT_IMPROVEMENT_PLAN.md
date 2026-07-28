# SafePatch 秋招项目提升方案

目标：把 SafePatch 从“完成度较高的自建 Agent 原型”提升为“有公开工程门禁、真实任务证据和可复现实验的可靠代码修复系统”。

建议周期为 3 周。时间不足时，严格按 P0 → P1 → P2 顺序推进，不在真实评估完成前继续扩张产品范围。三周版本统一使用 Python 3.11，减少跨版本问题对交付节奏的干扰；多版本兼容留到投递版本稳定之后。

## 1. 投递版本的完成标准

投递前同时满足以下条件：

- 工作区干净，所有受版本控制的测试文件非空且意图明确。
- Windows、Linux CI 在 Python 3.11 下的非 Docker 测试全部通过。
- Docker 安全 E2E 至少在 Linux CI 或独立 release workflow 中通过。
- README 首屏包含 30～60 秒演示、架构图、可复制命令和诚实的评估摘要。
- 至少包含一组真实仓库 bug 结果，以及一组 preflight 消融实验。
- 所有原始 session 结果可追溯，汇总报告可由脚本重新生成。
- 面试时能在 5 分钟内讲清问题、设计、权衡、数据和局限。

## 2. P0：投递前工程可信度（第 1 周）

### 2.1 恢复测试完整性

当前 `tests/test_p0_multifile_apply_rollback.py` 在工作区为 0 字节，`HEAD` 中包含 3 个测试。先确认清空是否有意；若无意则恢复并重新运行完整测试。

同时修正测试环境可移植性：测试替身启动 pytest 时使用 `sys.executable`，不要写死 `python`。涉及：

- `tests/test_end_to_end_local.py`
- `tests/test_hidden_eval.py`
- `tests/test_security_acceptance.py`

验收：干净克隆后只执行安装命令和 `python -m pytest` 即可得到稳定结果。

### 2.2 建立 CI 门禁

建议拆成三个 job：

1. `unit`：Windows + Ubuntu，统一 Python 3.11，运行非 Docker 测试。
2. `quality`：Ruff 只检查 `code_agent/`，构建 wheel，在干净环境安装 wheel 后执行 CLI smoke test。
3. `docker-release`：Ubuntu 单独运行，串行执行 `docker_e2e`；由 tag、release 或手动操作触发，不阻塞每个普通 PR。

真实 LLM benchmark 不进入普通 CI；放入手动 release workflow，避免成本和采样波动阻塞提交。

Ruff 暂不扫描 `examples/` 中故意包含错误的 fixture，也不把 benchmark 任务代码当作产品质量门禁。测试代码可先由 pytest 约束，后续再单独引入测试代码 lint。

验收：PR 必须通过 unit 与 quality；README 显示 CI 状态；release workflow 中 Docker 结果有可下载日志。

### 2.3 建立可复制演示

保留现有 dry-run 演示作为确定性演示，再补一段真实模型演示。视频结构控制在 60 秒：

1. 展示 baseline 测试失败。
2. 展示只读工具定位代码。
3. 展示补丁通过 policy 与 exact preflight。
4. 展示人工审批绑定两个 hash。
5. 展示 Docker 测试通过和最终 artifacts。

README 首屏只放一个主命令：

```powershell
code-agent examples\buggy_calculator `
  "divide should raise ValueError when b is zero" `
  --dry-run-script examples\dry_run_fix_divide.json `
  --yes
```

验收：新同学在 10 分钟内可以从干净克隆复现；GIF 不依赖讲解也能看懂关键控制链。

### 2.4 准备面试叙事

围绕以下五个问题准备答案，而不是逐模块背 README：

- 模型生成的补丁为什么会在业务逻辑正确时仍然失败？
- 为什么将 format retry、patch regeneration、repair attempt 分开？
- 为什么选择 exact apply 而不是 fuzzy apply？
- 人工审批为什么同时绑定 patch hash 和 working-tree hash？
- hidden tests 为什么必须在产品循环之外？

每个答案都按“真实故障 → 设计 → 代价 → 数据”组织，并准备一个失败案例 trace。

## 3. P1：建立真实有效性证据（第 2 周）

### 3.1 真实 bug benchmark

从公开 Python 项目的历史修复中筛选 **5～8 个**任务，第一版可从 BugsInPy 或真实 GitHub bug-fix commit 中选择。题目少时更要保证覆盖面，至少包含：单文件逻辑错误、边界条件、跨模块修改、红鲱鱼定位和隐藏测试泛化。筛选条件必须提前冻结：

- Python + pytest；无需在线安装依赖。
- 修复范围不超过 5 个文件、300 行变更。
- 修复前公开测试失败，参考修复后公开和隐藏测试通过。
- 不要求数据库、浏览器或外部服务。
- Agent 看不到参考补丁和隐藏测试。

不要只挑系统已经能做的题。记录候选池、被排除任务及排除原因，防止 benchmark selection bias。第一版的目标是形成可信的小样本案例研究，不把 5～8 题包装成通用成功率证明。

目录建议：

```text
benchmarks/real_bugs/
  manifest.json
  tasks/<task_id>/
  hidden/<task_id>/
  references/<task_id>/
  reports/
```

### 3.2 Preflight 消融实验

固定任务、模型、提示词、temperature 和最大调用次数，对比：

- A：完整 SafePatch。
- B：关闭 preflight，补丁直接进入审批和 apply。

三周版本不加入 fuzzy apply 对照。它会同时改变匹配策略和安全语义，难以与“是否有 preflight”形成单变量比较；后续应作为独立实验研究精确率、误应用风险和恢复能力。

主要指标：

- 最终公开/隐藏测试通过率。
- first-patch applicability。
- apply failure after approval。
- 无效 repair attempt 数。
- 总模型调用、token、墙钟时间和成本。

消融结论应回答：preflight 是否减少了无效修复尝试和审批噪声，而不是只比较最终成功率。

### 3.3 受控实验规模

采用“主实验充分重复、跨模型小规模核验”的方式控制成本：

- 主模型 + 完整 SafePatch：6 个左右真实任务 × 3 次，约 18 次运行。
- 主模型 + 关闭 preflight：同一任务集 × 3 次，约 18 次运行。
- 第二模型交叉验证：按单文件、跨模块、隐藏边界分层抽 3 题 × 2 次，约 6 次运行。

总规模约 42 次。若最终选择 5 或 8 个任务，可保持相同原则调整，不强求两个模型对所有题都跑三次。

报告每个模型的：

- 成功率及按任务难度分层结果。
- 首次补丁可应用率和 regeneration recovery。
- 平均/中位 token、调用次数、耗时、估算成本。
- 失败类型分布：格式、策略、preflight、公开测试、隐藏测试、环境。

报告必须保留原始 session ID 和配置快照，汇总表由脚本生成，不能手工填写。

### 3.4 真实任务中的多文件覆盖

不为了展示复杂度而强制制造大型任务。先按真实任务候选池决定多文件覆盖；若 5～8 个任务中完全没有跨模块修复，再补 1～2 个来源清楚、确实需要跨文件修改的任务。优先考虑：

1. 跨模块配置传播：解析、默认值、校验三个文件协同修改。
2. 数据处理流水线：schema、清洗和聚合存在关联缺陷。
3. API/服务边界：业务层和适配层需要同时修复，但测试文件不可修改。

跨模块任务应包含红鲱鱼文件与隐藏边界测试，但不设置人为代码行数门槛。参考修复必须确实需要跨文件变更，并保留上游 issue/commit 作为来源证据。

## 4. P2：工程质量与可维护性（第 3 周）

### 4.1 质量工具

开发依赖建议优先加入：

- `ruff`
- `pytest-cov`
- `build`

Ruff 仅对 `code_agent/` 设强门禁。覆盖率用于发现盲区和生成报告，暂不设置激进的全局阈值，更不为了追求 100% 写低价值测试。重点观察 controller、patching、workspace 和 Docker error classification 的分支覆盖率。

`mypy`/`pyright` 暂不作为全仓强门禁。有余力时只检查边界清晰的核心模块，例如 `state.py`、`patching/` 和 `repository/`，先生成报告，不阻塞投递版本。

### 4.2 发布与依赖复现

- 添加 LICENSE。
- 补充项目 URL、作者、Python classifiers。
- 区分三类依赖：产品运行依赖保留在 `project.dependencies`；测试、Ruff、构建工具进入 dev extra；benchmark runner 和数据处理依赖单独维护。
- 选择 `uv.lock` 或分层 constraints 文件锁定开发与 benchmark 环境，不让 benchmark 专用包膨胀产品安装。
- CI 中构建 wheel，并在新环境安装 wheel 后运行 CLI smoke test。
- benchmark 记录 Python、Docker image digest、模型名和参数。

### 4.3 Controller 重构（投递后）

三周内不做 Controller 大重构。当前优先级是建立 CI、真实评估和消融证据；大范围重构会扩大回归面，却不会直接提高招聘方对项目真实性的判断。

投递后如继续维护，再按状态转换责任拆成：

- `AnalysisLoop`：模型请求、工具调用、格式重试。
- `PatchGate`：policy、preflight、审批 binding。
- `RepairExecutor`：apply、rollback、pytest、失败分类。
- `SessionFinalizer`：summary、diff、trace、observability。

重构前冻结行为测试；重构提交不得同时引入新功能。验收重点是状态转换和 counter 语义不变。

### 4.4 可观测性展示

静态 HTML 报告属于有余力再做，不进入三周版本的完成标准。若 P0、真实 benchmark 和消融均已完成，可做一个读取 session artifacts 的纯静态生成器，输出：

- 时间线。
- 模型/工具/测试调用统计。
- 补丁与失败分类。
- token、耗时和成本卡片。

这样既能用于演示，也不会把项目范围扩张成前端系统。只有确实需要多 session 检索时，再增加 SQLite 索引或轻量 API。

## 5. 三周执行顺序

### 第 1 周：可以放心投递

- Day 1：恢复测试、修复 `sys.executable`、建立干净测试基线。
- Day 2～3：Python 3.11 Windows/Linux CI、仅产品代码 Ruff、wheel + CLI smoke test。
- Day 4：Docker release workflow、日志上传。
- Day 5：录制 GIF、精简 README 首屏。
- Day 6～7：准备架构讲解和两个失败案例。

### 第 2 周：从玩具评估升级为真实证据

- Day 1～2：冻结真实任务选择协议、候选池和 manifest。
- Day 3～4：接入 5～8 个真实任务。
- Day 5：实现 preflight 开关，仅用于 benchmark 消融。
- Day 6～7：运行约 42 次受控实验并自动生成报告。

### 第 3 周：工程收尾

- 完成分层依赖锁定、LICENSE、包元数据和覆盖率报告。
- 有余力时对核心模块运行类型检查，但不设全仓强门禁。
- 只有核心证据全部完成后，才考虑静态 session 报告页。
- 更新简历数据，只引用可复现的冻结报告。

## 6. 简历最终应引用的数字

优先引用能解释设计价值的指标：

- 真实任务公开和隐藏测试成功率。
- preflight 将审批后 apply failure 降低了多少。
- 避免了多少无效 repair attempt。
- regeneration 的恢复率。
- 不同模型的成本/成功率权衡。
- CI 平台数、确定性测试数和关键安全 E2E 数。

不要把自建微任务的 100% 单独写成“修复准确率 100%”。应明确任务规模、运行次数、模型和限制。
