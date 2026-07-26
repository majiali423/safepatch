from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_eval_mod():
    path = Path(__file__).resolve().parents[1] / "examples" / "eval_tasks" / "run_llm_eval.py"
    spec = importlib.util.spec_from_file_location("run_llm_eval", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eval_run_dirs_are_unique_and_not_deleted(tmp_path, monkeypatch):
    """Two runs must keep both result/session trees (no rmtree of history)."""
    eval_mod = _load_eval_mod()
    monkeypatch.setattr(eval_mod, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(eval_mod, "TASKS_ROOT", tmp_path / "tasks")

    task_id = "task_demo"
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.txt").write_text("demo", encoding="utf-8")

    created_session_bases: list[Path] = []

    def fake_cli_main(argv):
        session_base = Path(argv[argv.index("--session-base") + 1])
        created_session_bases.append(session_base)
        assert session_base.exists()
        sess = session_base / "sessdeadbeef"
        art = sess / "artifacts"
        art.mkdir(parents=True)
        (art / "summary.json").write_text(
            '{"status":"ERROR","attempts_used":0,"changed_files":[],'
            '"baseline_tests_passed":false,"final_tests_passed":false,'
            '"stop_reason":"model_output_invalid"}',
            encoding="utf-8",
        )
        (art / "trace.jsonl").write_text(
            '{"event":"parse_failed","payload":{}}\n', encoding="utf-8"
        )
        return 5

    monkeypatch.setitem(
        __import__("sys").modules,
        "code_agent.cli",
        type("M", (), {"main": staticmethod(fake_cli_main)})(),
    )
    # run_one imports cli.main inside the function — patch the module attribute.
    import code_agent.cli as cli_mod

    monkeypatch.setattr(cli_mod, "main", fake_cli_main)

    task = {"id": task_id, "expected_files": set(), "allow_test_edits": False}
    row1 = eval_mod.run_one(task)
    row2 = eval_mod.run_one(task)

    assert row1["run_id"] != row2["run_id"]
    assert Path(row1["results_path"]).exists()
    assert Path(row2["results_path"]).exists()
    assert (Path(row1["results_path"]) / "summary.json").exists()
    assert (Path(row2["results_path"]) / "summary.json").exists()
    assert created_session_bases[0].exists()
    assert created_session_bases[1].exists()
    assert created_session_bases[0] != created_session_bases[1]
