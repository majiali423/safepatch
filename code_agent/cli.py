from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from code_agent.controller import TaskController
from code_agent.envfile import load_dotenv
from code_agent.llm import LLMClient
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.state import ApprovalBinding, SessionStatus

console = Console()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="Repair a small local Python repo with Docker pytest and human approval.",
    )
    parser.add_argument(
        "repo",
        type=Path,
        nargs="?",
        default=None,
        help="Local Python repository path",
    )
    parser.add_argument(
        "description",
        nargs="?",
        default=None,
        help="Bug or feature description",
    )
    parser.add_argument(
        "-d",
        "--description-file",
        type=Path,
        help="Read description from file",
    )
    parser.add_argument(
        "--session-base",
        type=Path,
        default=None,
        help="Directory for temporary sessions (default: ./.code_agent_sessions)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (or CODE_AGENT_MODEL)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve all patches (no prompt)",
    )
    parser.add_argument(
        "--allow-test-changes",
        action="store_true",
        help=(
            "Allow modifying existing test_*.py files (still forbids deleting tests, "
            "editing conftest/pytest config, adding skip/skipif, assert True, "
            "or removing all assertions). Default: off."
        ),
    )
    parser.add_argument(
        "--dry-run-script",
        type=Path,
        help="JSON list of tool calls to replay without an API key",
    )
    parser.add_argument(
        "--build-image",
        action="store_true",
        help="Build local Docker image code-agent-pytest:local and exit",
    )
    parser.add_argument(
        "--docker-check",
        action="store_true",
        help="Run docker info + image preflight and exit",
    )
    args = parser.parse_args(argv)

    if args.build_image:
        return _build_image()

    if args.docker_check:
        return _docker_check()

    if args.repo is None:
        console.print("[red]Please provide a local repository path.[/red]")
        return 2

    description = args.description
    if args.description_file:
        description = args.description_file.read_text(encoding="utf-8").strip()
    if not description:
        console.print("[red]Please provide a bug/feature description.[/red]")
        return 2

    # Preflight: clear error, no stack trace.
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        console.print(f"[red]TEST_ENVIRONMENT_ERROR[/red]: {reason}")
        console.print(
            "Start Docker Desktop, then run: [bold]code-agent --build-image[/bold] "
            "and [bold]code-agent --docker-check[/bold]"
        )
        return 4

    dry_script = None
    if args.dry_run_script:
        dry_script = json.loads(args.dry_run_script.read_text(encoding="utf-8"))

    def approve(binding: ApprovalBinding, attempt: int) -> bool:
        if args.yes:
            console.print(
                f"[yellow]Auto-approving patch attempt {attempt}[/yellow] "
                f"patch_hash={binding.patch_hash[:12]}... "
                f"wt_hash={binding.working_tree_hash[:12]}..."
            )
            return True
        return _prompt_approval(binding, attempt)

    def say(msg: str) -> None:
        console.print(msg)

    llm = LLMClient(model=args.model, dry_run_script=dry_script)
    controller = TaskController(
        llm=llm,
        runner=runner,
        approve=approve,
        say=say,
        session_base=args.session_base,
        allow_test_changes=args.allow_test_changes,
    )

    console.print(
        Panel.fit(
            f"[bold]code-agent[/bold]\nrepo: {args.repo}\n{description}",
            title="session",
        )
    )
    try:
        session = controller.run(args.repo, description)
    except Exception as exc:  # noqa: BLE001 - CLI must not dump stacks for v1 UX
        console.print(f"[red]ERROR[/red]: {exc}")
        return 1

    summary = session.to_summary()
    console.print(Panel(json.dumps(summary, indent=2, ensure_ascii=False), title="summary"))
    console.print(f"Artifacts: {session.artifacts_dir}")

    if session.status == SessionStatus.SUCCEEDED:
        return 0
    if session.status == SessionStatus.TESTS_PASSED_UNVERIFIED:
        console.print(
            "[yellow]Tests passed, but the request has no independent verification oracle.[/yellow]"
        )
        return 9
    if session.status.value == "REJECTED":
        return 3
    if session.status.value in {"TEST_ENVIRONMENT_ERROR", "TEST_TIMEOUT"}:
        return 4
    if session.status.value == "MODEL_OUTPUT_INVALID":
        return 5
    if session.status.value == "PATCH_NOT_APPLICABLE":
        return 6
    if session.status.value == "PATCH_BASE_CHANGED":
        return 7
    if session.status.value == "READ_BUDGET_EXHAUSTED":
        return 8
    return 1


def _prompt_approval(binding: ApprovalBinding, attempt: int) -> bool:
    proposal = binding.proposal
    validation = binding.validation
    console.print()
    console.rule(f"Patch proposal (attempt {attempt})")
    console.print(f"[bold]patch_hash[/bold]: {binding.patch_hash}")
    console.print(f"[bold]working_tree_hash[/bold]: {binding.working_tree_hash}")
    if validation.high_risk:
        console.print("[bold red]HIGH RISK[/bold red]")
        for warning in validation.test_integrity_warnings:
            console.print(f"[red]{warning}[/red]")
    if validation.new_test_files:
        console.print(
            "[bold red]HIGH RISK: NEW TEST FILE[/bold red]: "
            + ", ".join(validation.new_test_files)
        )
    if validation.modified_test_files:
        console.print(
            "[bold red]HIGH RISK: EXISTING TESTS MODIFIED[/bold red]: "
            + ", ".join(validation.modified_test_files)
        )
    console.print(f"[bold]Diagnosis[/bold]: {proposal.diagnosis}")
    console.print(f"[bold]Expected[/bold]: {proposal.expected_behavior}")
    console.print(f"[bold]Risk[/bold]: {proposal.risk_notes}")
    console.print(f"[bold]Actual files[/bold]: {', '.join(validation.files)}")
    console.print(f"[bold]Model-declared files[/bold]: {', '.join(proposal.affected_files)}")
    console.print(f"[bold]Tests[/bold]: {', '.join(proposal.tests_to_run)}")
    console.print()
    # Show full diff (including new/modified tests); risk flags come from validation.
    console.print(Syntax(proposal.unified_diff, "diff", theme="monokai", line_numbers=False))
    console.print()
    while True:
        answer = console.input("Approve patch? [approve/reject]: ").strip().lower()
        if answer in {"approve", "a", "yes", "y"}:
            return True
        if answer in {"reject", "r", "no", "n"}:
            return False
        console.print("Please type approve or reject")


def _docker_check() -> int:
    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if ok:
        console.print(f"[green]OK[/green]: {reason}")
        return 0
    console.print(f"[red]TEST_ENVIRONMENT_ERROR[/red]: {reason}")
    return 4


def _build_image() -> int:
    import subprocess

    from code_agent.runtime.docker_pytest import _sanitized_subprocess_env

    dockerfile = Path(__file__).resolve().parent / "runtime" / "Dockerfile.pytest"
    if not dockerfile.exists():
        console.print(f"[red]ERROR[/red]: Dockerfile not found: {dockerfile}")
        return 1

    # Precheck daemon without requiring image.
    if subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
        env=_sanitized_subprocess_env(),
    ).returncode != 0:
        console.print(
            "[red]TEST_ENVIRONMENT_ERROR[/red]: Docker daemon unavailable. "
            "Start Docker Desktop and retry."
        )
        return 4

    cmd = [
        "docker",
        "build",
        "-t",
        "code-agent-pytest:local",
        "-f",
        str(dockerfile),
        str(dockerfile.parent),
    ]
    console.print("Building:", " ".join(cmd))
    proc = subprocess.run(cmd, check=False, env=_sanitized_subprocess_env())
    if proc.returncode != 0:
        console.print("[red]ERROR[/red]: docker build failed")
        return proc.returncode
    console.print("[green]Built[/green] code-agent-pytest:local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
