# 第一版学习文档（Walkthrough）

适合初学者：用一个「除零应抛 ValueError」的小例子，走完 code-agent 第一版闭环。

> 验证层次说明（请勿混淆）：
>
> 1. **单元测试**：策略、路径、补丁解析等 — 已通过  
> 2. **mock / 本地 pytest 集成测试**：用测试替身代替 Docker — 已通过  
> 3. **真实 Docker Runtime E2E** — **尚未验证**（需你本地启动 Docker Desktop）

---

## 1. pytest 在本项目中的作用

pytest 是**唯一的正确性判据**。

- **基线测试**：Agent 改代码之前先跑一遍，证明「现在确实坏了」。
- **修复后测试**：每轮补丁应用后在隔离环境再跑，证明「补丁是否修好」。
- 第一版固定命令：`python -m pytest -q -p no:cacheprovider`。

Agent **不能**自己决定怎么跑测试；由控制器调用 Runtime。

---

## 2. workspace 副本为什么必要

用户给出的是**原始仓库路径**。系统会：

1. 校验目录存在；
2. 创建临时 session 目录；
3. 把仓库复制到 `working_copy`；
4. **之后所有读写、打补丁、跑测试只碰副本**。

这样即使补丁有问题，也不会破坏你的原仓库。`.git`、虚拟环境等会被忽略，避免把无关或巨大目录拷进来。

---

## 3. Repo Map 如何通过 AST 生成

系统用 Python 标准库 `ast` 解析每个 `.py` 文件的**结构**，而不是把全文塞给模型：

- import
- 类名 / 方法名 / 参数
- 顶层函数
- 以 `test_` 开头的 pytest 函数
- 语法错误标记

模型先看地图，再决定要 `read_file` 哪些片段。这是向 Aider 学的「缩小上下文」做法。

---

## 4. Agent 如何选择文件

Agent 只有这些工具：

| 工具 | 用途 |
|---|---|
| `get_repo_map` | 看结构 |
| `list_tree` / `search_text` / `search_symbol` | 定位 |
| `read_file` | 按行阅读（有行数与次数上限） |
| `get_current_diff` | 看已改内容 |
| `propose_patch` | 提出补丁 |
| `finish` | 放弃并结束 |

典型路径：Repo Map → 搜索符号/失败测试名 → 读相关文件 → 提出 unified diff。

---

## 5. unified diff 是什么

unified diff 是一种**文本补丁格式**，描述「旧文件某一段删什么、加什么」。例如：

```diff
--- a/calculator.py
+++ b/calculator.py
@@ -5,6 +5,8 @@
 def divide(a: float, b: float) -> float:
     """Divide a by b.
 
     Should raise ValueError when b is 0.
     """
+    if b == 0:
+        raise ValueError("division by zero")
     return a / b
```

第一版要求模型输出这种 diff，再由 `PolicyValidator` 检查、由 `PatchApplier` 应用。上下文对不上会应用失败并回滚。

---

## 6. 补丁为什么必须先审批

读操作可以自动执行；**写操作有副作用**。

流程：

1. 模型 `propose_patch`
2. 状态进入 `AWAITING_APPROVAL`
3. CLI 展示诊断、文件列表、完整 diff、风险、计划测试
4. 你输入 `approve` 或 `reject`

只有批准后才会应用。每一轮新补丁都要重新批准。拒绝则任务立即变成 `REJECTED` 并结束。

这是向 goose 学的「危险操作要人确认」。

---

## 7. Docker Runtime 如何执行 pytest

模型**没有** Docker 工具。控制器固定调用 `DockerPytestRunner`：

- `--network none`（禁止网络）
- 内存 512MB、1 CPU、最多 128 进程
- 120 秒超时
- 非 root（`1000:1000`）
- `--rm`（结束后删容器）
- 挂载的是 **working_copy**，不是原仓库
- 不向容器传入宿主 API Key

镜像需预先构建：`code-agent --build-image` → `code-agent-pytest:local`。

---

## 8. 测试失败后如何进入下一轮

状态大致是：

```
ANALYZING → PATCH_PROPOSED → AWAITING_APPROVAL → PATCH_APPLIED → TESTING
```

- 测试通过 → `SUCCEEDED`
- 测试失败且未满 3 次 → 回到 `ANALYZING`，把失败信息、traceback、当前 diff、剩余次数喂给模型
- 满 3 次仍失败 → `FAILED_MAX_ATTEMPTS`

注意：

- **基线测试不计入**这三次；
- 补丁在**当前工作副本**上增量应用，不会每轮自动还原（除非应用失败而回滚）。

---

## 9. trace 和 artifacts 有什么区别

| | trace | artifacts |
|---|---|---|
| 是什么 | 事件流水账 `trace.jsonl` | 一次会话的结果包 |
| 用来干什么 | 复盘 Agent 做了什么 | 拿走 diff、摘要、日志 |
| 典型内容 | session_created、tool_call、approval_decision… | `final.diff`、`summary.json`、`baseline.log`… |

trace **不应**记录 API Key 或模型隐藏思维链。

---

## 10. 完整示例：从输入到修复成功

用户输入：

```text
仓库：examples/buggy_calculator
描述：divide 在除数为 0 时应抛 ValueError，现在抛 ZeroDivisionError
```

期望过程：

1. 复制到 working_copy，生成 Repo Map  
2. 基线 pytest：1 passed, 1 failed  
3. Agent 读 `calculator.py` 与测试  
4. 提出 unified diff  
5. 你 `approve`  
6. Docker 内 pytest：2 passed  
7. 导出 `final.diff` / `summary.json` / `trace.jsonl` / 日志  

其中第 6 步属于**真实 Docker Runtime**，需你本机 Docker Desktop 就绪后验证。
