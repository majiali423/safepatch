from __future__ import annotations

from code_agent.runtime.docker_config import DockerRunConfig
from code_agent.runtime.docker_pytest import (
    CONTROLLED_BOOTSTRAP,
    DockerPytestRunner,
    attach_inventory_plugin,
)


def test_docker_run_config_to_dict_and_cmd_are_same_source():
    cfg = DockerRunConfig(image="safepatch-pytest:local")
    payload = cfg.to_dict()
    assert payload == {
        "image": "safepatch-pytest:local",
        "network_mode": "none",
        "memory": "512m",
        "cpus": 1,
        "pids_limit": 128,
        "user": "1000:1000",
        "timeout_seconds": 120,
        "remove_container": True,
        "container_name": None,
        "cap_drop": "ALL",
        "no_new_privileges": True,
        "read_only": True,
        "tmpfs": "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "pytest_argv": [
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
    }
    cmd = cfg.build_docker_cmd(r"C:\work\copy")
    assert cmd[0:3] == ["docker", "run", "--rm"]
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--memory" in cmd and cmd[cmd.index("--memory") + 1] == "512m"
    assert "--cpus" in cmd and cmd[cmd.index("--cpus") + 1] == "1"
    assert "--pids-limit" in cmd and cmd[cmd.index("--pids-limit") + 1] == "128"
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == "1000:1000"
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in cmd
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in cmd
    assert "--tmpfs" in cmd
    assert cmd[cmd.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,noexec,size=64m"
    assert "--name" not in cmd
    assert cmd[-6:] == list(payload["pytest_argv"])
    assert "safepatch-pytest:local" in cmd
    # Must not embed secrets / env dumps.
    joined = " ".join(cmd)
    assert "OPENAI" not in joined
    assert "API_KEY" not in joined


def test_container_name_appears_in_cmd_and_to_dict():
    cfg = DockerRunConfig(
        image="safepatch-pytest:local",
        container_name="safepatch-test-abc123",
        timeout_seconds=2,
    )
    payload = cfg.to_dict()
    cmd = cfg.build_docker_cmd("/work/copy")
    assert payload["container_name"] == "safepatch-test-abc123"
    assert payload["timeout_seconds"] == 2
    assert "--name" in cmd and cmd[cmd.index("--name") + 1] == "safepatch-test-abc123"


def test_runner_make_run_config_matches_build_cmd():
    runner = DockerPytestRunner()
    cfg = runner.make_run_config("safepatch-pytest:local")
    assert cfg.to_dict()["image"] == "safepatch-pytest:local"
    cmd = cfg.build_docker_cmd("/work/copy")
    assert cfg.image in cmd
    assert cfg.timeout_seconds == 120
    assert cfg.container_name is None


def test_runner_injects_inventory_plugin_without_changing_config_payload():
    cfg = DockerRunConfig(image="safepatch-pytest:local")
    payload = cfg.to_dict()
    cmd = attach_inventory_plugin(cfg.build_docker_cmd("/work/copy"), cfg)
    assert payload["pytest_argv"] == [
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    assert cmd[-2:] == ["-p", "no:cacheprovider"]
    assert CONTROLLED_BOOTSTRAP in cmd
    assert not any(item.startswith("PYTHONPATH=") for item in cmd)
    assert "safepatch_inventory" not in cmd
