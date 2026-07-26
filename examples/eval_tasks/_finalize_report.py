import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
rows = []
for tid in [
    "task01_zero_div",
    "task02_boundary",
    "task03_signature",
    "task04_validation",
    "task05_vague",
]:
    m = json.loads((RESULTS / tid / "metrics.json").read_text(encoding="utf-8"))
    rows.append(m)

report = {
    "model": "deepseek-chat",
    "tasks": rows,
    "success_count": sum(1 for r in rows if r.get("success")),
    "total": len(rows),
    "note": (
        "task04 first attempt failed with invalid JSON from model; "
        "metrics reflect successful retry"
    ),
}
(RESULTS / "report.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

lines = [
    "# Real LLM Eval Report (5 tasks)",
    "",
    "- model: `deepseek-chat`",
    f"- success: **{report['success_count']}/{report['total']}** (task04 after 1 retry)",
    "",
    "| 任务 | 成功 | 轮数 | 读操作 | 改文件 | 无关修改 | 建议人工拒绝 |",
    "|---|---|---|---|---|---|---|",
]
for r in rows:
    ok = "Y" if r["success"] else "N"
    unrel = ", ".join(r.get("unrelated_modifications") or []) or "-"
    rej = "Y" if r.get("human_reject_recommended") else "N"
    lines.append(
        f"| {r['task']} | {ok} | {r['attempts_used']} | "
        f"{r['files_read_actions']} | {r['changed_count']} | {unrel} | {rej} |"
    )
lines += [
    "",
    "产物目录: `examples/eval_tasks/results/<task_id>/`",
    "",
    "说明:",
    "- task04 首次因模型返回非法 JSON 失败，重试后成功。",
    "- task05 描述含糊，Agent 未改诱饵文件 `pricing.py`。",
    "",
]
(RESULTS / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
print(json.dumps(report, indent=2, ensure_ascii=False))
