from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import run_lean_parity_local as operator
from trading_bot_lab.lean_runtime import (
    AUTHORIZATION_BATCH_ID,
    COMPARE_AUTHORIZATION,
    EXPECTED_CLI_VERSION,
    FIXTURE_SHA256,
    LEAN_CLI_NETWORK,
    MAX_BATCH_EXECUTIONS,
    MAX_EXECUTIONS,
    OCI_INDEX_DIGEST,
    PINNED_IMAGE,
    PINNED_IMAGE_UNAVAILABLE,
    PINNED_PLATFORM_MANIFEST_MISMATCH,
    PLATFORM_MANIFEST_DIGEST,
    PREPARE_AUTHORIZATION,
    PRIOR_CUMULATIVE_EXECUTIONS,
    PULL_AUTHORIZATION,
    ROOTLESS_HOST,
    RUN_AUTHORIZATION,
    RUNTIME_CONTAINER_LABEL,
    RUNTIME_CONTAINER_LABEL_VALUE,
    ExclusiveRunLock,
    LeanRuntimeError,
    RuntimeState,
    assert_ignored_path,
    build_cli_general_config,
    build_extra_docker_config,
    build_isolated_environment,
    build_minimal_lean_config,
    cleanup_runtime_directory,
    increment_execution,
    increment_pull,
    initialize_runtime_directory,
    load_runtime_state,
    parse_mutable_discovery_metadata,
    parse_pinned_registry_identity,
    public_contract_summary,
    require_authorization,
    sanitize_generated_lean_config,
    validate_actual_container,
    validate_cli_version,
    validate_container_auto_remove,
    validate_container_run_kwargs,
    validate_docker_info,
    validate_generated_engine_config,
    validate_image_reference,
    validate_lean_cli_network,
    validate_linux_host,
    validate_local_image,
    validate_minimal_lean_config,
    validate_runtime_audit,
    verify_exact_lf_bytes,
    write_runtime_state,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"

MOVED_LATEST_INDEX = "sha256:" + "1" * 64
MOVED_LATEST_AMD64 = "sha256:" + "2" * 64


def docker_info(home: Path) -> dict[str, object]:
    return {
        "Architecture": "x86_64",
        "DockerRootDir": str(home / ".local" / "share" / "docker"),
        "OSType": "linux",
        "SecurityOptions": ["name=seccomp,profile=builtin", "name=rootless"],
    }


def local_image() -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Created": "2026-07-01T00:00:00Z",
            "Id": "sha256:" + "a" * 64,
            "Os": "linux",
            "RepoDigests": [PINNED_IMAGE],
            "Size": 123456,
        }
    ]


def container_kwargs(root: Path) -> dict[str, object]:
    return {
        **build_extra_docker_config(),
        "environment": {"DOTNET_CLI_TELEMETRY_OPTOUT": "true"},
        "mounts": [{"Source": str(root / "project"), "Target": "/LeanCLI"}],
        "ports": {},
        "volumes": {},
    }


def actual_container(root: Path) -> dict[str, object]:
    return {
        "Config": {
            "Env": ["DOTNET_CLI_TELEMETRY_OPTOUT=true"],
            "Labels": {RUNTIME_CONTAINER_LABEL: RUNTIME_CONTAINER_LABEL_VALUE},
        },
        "HostConfig": {
            "CapDrop": ["ALL"],
            "IpcMode": "private",
            "Memory": 2 * 1024**3,
            "NanoCpus": 2_000_000_000,
            "NetworkMode": "none",
            "PidsLimit": 256,
            "PidMode": "",
            "PortBindings": {},
            "Privileged": False,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [{"Source": str(root / "project"), "Destination": "/LeanCLI"}],
    }


def pinned_registry_output() -> str:
    return (
        f"Name:      docker.io/{PINNED_IMAGE}\n"
        "MediaType: application/vnd.oci.image.index.v1+json\n"
        f"Digest:    {OCI_INDEX_DIGEST}\n"
        "\n"
        "Manifests:\n"
        f"  Name:      docker.io/quantconnect/lean@{PLATFORM_MANIFEST_DIGEST}\n"
        "  Platform:  linux/amd64\n"
    )


def moved_latest_output() -> str:
    return (
        "Name:      docker.io/quantconnect/lean:latest\n"
        "MediaType: application/vnd.oci.image.index.v1+json\n"
        f"Digest:    {MOVED_LATEST_INDEX}\n"
        "\n"
        "Manifests:\n"
        f"  Name:      docker.io/quantconnect/lean:latest@{MOVED_LATEST_AMD64}\n"
        "  Platform:  linux/amd64\n"
    )


def test_exact_image_and_cli_contract_is_immutable() -> None:
    assert validate_image_reference(PINNED_IMAGE) == PINNED_IMAGE
    assert validate_cli_version(EXPECTED_CLI_VERSION) == EXPECTED_CLI_VERSION
    summary = public_contract_summary()
    assert summary["image_oci_index_digest"] == OCI_INDEX_DIGEST
    assert summary["platform_manifest_digest"] == PLATFORM_MANIFEST_DIGEST
    assert summary["fixture_sha256"] == FIXTURE_SHA256
    assert summary == public_contract_summary()


@pytest.mark.parametrize(
    "reference",
    [
        "quantconnect/lean:latest",
        "quantconnect/lean:stable",
        f"other/lean@{OCI_INDEX_DIGEST}",
        "quantconnect/lean@sha256:" + "0" * 64,
        "docker.io/" + PINNED_IMAGE,
    ],
)
def test_image_reference_rejects_tags_repositories_and_digests(reference: str) -> None:
    with pytest.raises(LeanRuntimeError):
        validate_image_reference(reference)


def test_linux_platform_contract_rejects_other_hosts() -> None:
    validate_linux_host(system="Linux", machine="x86_64")
    validate_linux_host(system="Linux", machine="amd64")
    with pytest.raises(LeanRuntimeError):
        validate_linux_host(system="Windows", machine="AMD64")
    with pytest.raises(LeanRuntimeError):
        validate_linux_host(system="Linux", machine="aarch64")


def test_rootless_docker_identity_is_fail_closed(tmp_path: Path) -> None:
    identity = validate_docker_info(docker_info(tmp_path), user_home=tmp_path)
    assert identity.host == ROOTLESS_HOST
    assert identity.architecture == "amd64"

    missing_rootless = docker_info(tmp_path)
    missing_rootless["SecurityOptions"] = ["name=seccomp"]
    with pytest.raises(LeanRuntimeError):
        validate_docker_info(missing_rootless, user_home=tmp_path)

    system_root = docker_info(tmp_path)
    system_root["DockerRootDir"] = "/var/lib/docker"
    with pytest.raises(LeanRuntimeError):
        validate_docker_info(system_root, user_home=tmp_path)

    wrong_platform = docker_info(tmp_path)
    wrong_platform["Architecture"] = "arm64"
    with pytest.raises(LeanRuntimeError):
        validate_docker_info(wrong_platform, user_home=tmp_path)


def test_local_image_identity_requires_exact_repo_digest_and_platform() -> None:
    identity = validate_local_image(local_image())
    assert identity.repo_digest == PINNED_IMAGE
    assert identity.operating_system == "linux"

    wrong_digest = local_image()
    wrong_digest[0]["RepoDigests"] = ["quantconnect/lean@sha256:" + "0" * 64]
    with pytest.raises(LeanRuntimeError):
        validate_local_image(wrong_digest)

    wrong_platform = local_image()
    wrong_platform[0]["Architecture"] = "arm64"
    with pytest.raises(LeanRuntimeError):
        validate_local_image(wrong_platform)


def test_pinned_registry_identity_is_independent_of_moved_latest() -> None:
    identity = parse_pinned_registry_identity(pinned_registry_output())
    discovery = parse_mutable_discovery_metadata(moved_latest_output())

    assert identity.index_digest == OCI_INDEX_DIGEST
    assert identity.platform_digest == PLATFORM_MANIFEST_DIGEST
    assert identity.as_dict()["authoritative"] is True
    assert discovery.index_digest == MOVED_LATEST_INDEX
    assert discovery.platform_digest == MOVED_LATEST_AMD64
    assert discovery.as_dict()["authoritative"] is False


def test_pinned_registry_identity_rejects_changed_platform_manifest() -> None:
    with pytest.raises(LeanRuntimeError, match=PINNED_PLATFORM_MANIFEST_MISMATCH):
        parse_pinned_registry_identity(
            pinned_registry_output().replace(
                PLATFORM_MANIFEST_DIGEST,
                "sha256:" + "0" * 64,
            )
        )


@pytest.mark.parametrize(
    "output",
    [
        "malformed",
        pinned_registry_output().replace(
            "docker.io/quantconnect/lean@",
            "docker.io/other/lean@",
        ),
        pinned_registry_output().replace(OCI_INDEX_DIGEST, "sha256:" + "0" * 64),
        pinned_registry_output().replace(OCI_INDEX_DIGEST, "sha256:not-a-digest"),
    ],
)
def test_pinned_registry_identity_rejects_unavailable_or_ambiguous_reference(
    output: str,
) -> None:
    with pytest.raises(LeanRuntimeError, match=PINNED_IMAGE_UNAVAILABLE):
        parse_pinned_registry_identity(output)


def test_mutable_discovery_rejects_repository_ambiguity() -> None:
    with pytest.raises(LeanRuntimeError):
        parse_mutable_discovery_metadata(
            moved_latest_output().replace(
                "docker.io/quantconnect/lean:latest",
                "docker.io/other/lean:latest",
            )
        )


@pytest.mark.parametrize(
    ("expected", "action"),
    [
        (PULL_AUTHORIZATION, "pull"),
        (PREPARE_AUTHORIZATION, "prepare"),
        (RUN_AUTHORIZATION, "run"),
        (COMPARE_AUTHORIZATION, "compare"),
    ],
)
def test_mutating_authorizations_are_exact(expected: str, action: str) -> None:
    require_authorization(expected, expected, action=action)
    with pytest.raises(LeanRuntimeError):
        require_authorization(None, expected, action=action)
    with pytest.raises(LeanRuntimeError):
        require_authorization(expected + "-changed", expected, action=action)


def test_operator_rejects_pull_and_run_before_preflight_without_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(operator, "perform_preflight", unexpected_preflight)
    with pytest.raises(LeanRuntimeError):
        operator.pull_image(None)
    with pytest.raises(LeanRuntimeError):
        operator.run_lean(None)
    assert called is False


def test_current_batch_rejects_pull_before_preflight_or_registry_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(operator, "STATE_PATH", tmp_path / "missing-state.json")
    monkeypatch.setattr(operator, "perform_preflight", lambda: calls.append("preflight"))
    monkeypatch.setattr(operator, "_run", lambda *args, **kwargs: calls.append("registry"))
    monkeypatch.setattr(
        operator.subprocess,
        "run",
        lambda *args, **kwargs: calls.append("pull"),
    )

    with pytest.raises(LeanRuntimeError, match="no image pull is authorized"):
        operator.pull_image(PULL_AUTHORIZATION)

    assert calls == []


def test_unavailable_pinned_registry_identity_blocks_before_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operator,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "not found"),
    )
    with pytest.raises(LeanRuntimeError, match=PINNED_IMAGE_UNAVAILABLE):
        operator._inspect_pinned_registry({})


def test_bounded_state_only_continues_preserved_history_to_seven(tmp_path: Path) -> None:
    assert AUTHORIZATION_BATCH_ID == "open-phase-risk-correction-1"
    assert PRIOR_CUMULATIVE_EXECUTIONS == 5
    assert MAX_BATCH_EXECUTIONS == 2
    assert MAX_EXECUTIONS == 7
    for reset_state in (
        RuntimeState(),
        RuntimeState(executions=4, pulls=1),
        RuntimeState(executions=5, pulls=0),
        RuntimeState(executions=6, pulls=0),
    ):
        with pytest.raises(LeanRuntimeError, match="preserved runtime history"):
            increment_execution(reset_state)

    state = RuntimeState(executions=5, pulls=1)
    for expected in (6, 7):
        state = increment_execution(state)
        assert state.executions == expected
    with pytest.raises(LeanRuntimeError, match="maximum authorized"):
        increment_execution(state)
    with pytest.raises(LeanRuntimeError, match="no image pull is authorized"):
        increment_pull(RuntimeState(executions=5, pulls=1))

    path = tmp_path / "state.json"
    write_runtime_state(path, state)
    first = path.read_bytes()
    write_runtime_state(path, state)
    assert path.read_bytes() == first
    assert load_runtime_state(path) == state


def test_new_authorization_batch_continues_cumulative_count_and_stops_at_seven() -> None:
    state = RuntimeState(executions=5, pulls=1)
    state = increment_execution(state)
    assert state == RuntimeState(executions=6, pulls=1)
    state = increment_execution(state)
    assert state == RuntimeState(executions=7, pulls=1)
    with pytest.raises(LeanRuntimeError, match="maximum authorized"):
        increment_execution(state)


@pytest.mark.parametrize(
    "payload",
    [
        {"executions": -1, "pulls": 0, "schema_version": "1.0.0"},
        {"executions": 0, "pulls": 2, "schema_version": "1.0.0"},
        {"executions": 0, "pulls": 0, "schema_version": "2.0.0"},
        {"executions": 0.5, "pulls": 0, "schema_version": "1.0.0"},
        {"unexpected": True},
    ],
)
def test_runtime_state_rejects_malformed_evidence(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LeanRuntimeError):
        load_runtime_state(path)


def test_exclusive_lock_prevents_parallel_execution(tmp_path: Path) -> None:
    lock = tmp_path / "parity.lock"
    with ExclusiveRunLock(lock), pytest.raises(LeanRuntimeError), ExclusiveRunLock(lock):
        pass
    assert not lock.exists()


def test_fixture_hash_and_lf_bytes_are_exact(tmp_path: Path) -> None:
    assert verify_exact_lf_bytes(SOURCE_FIXTURE) == FIXTURE_SHA256
    fixture = SOURCE_FIXTURE.read_bytes()

    crlf = tmp_path / "crlf.csv"
    crlf.write_bytes(fixture.replace(b"\n", b"\r\n"))
    with pytest.raises(LeanRuntimeError):
        verify_exact_lf_bytes(crlf)

    mutated = tmp_path / "mutated.csv"
    mutated.write_bytes(fixture[:-1])
    with pytest.raises(LeanRuntimeError):
        verify_exact_lf_bytes(mutated)

    symlink = tmp_path / "symlink.csv"
    symlink.symlink_to(SOURCE_FIXTURE)
    with pytest.raises(LeanRuntimeError):
        verify_exact_lf_bytes(symlink)


def test_container_request_is_offline_unprivileged_and_path_bounded(tmp_path: Path) -> None:
    kwargs = container_kwargs(tmp_path)
    validate_container_run_kwargs(PINNED_IMAGE, kwargs, allowed_mount_roots=(tmp_path,))
    assert kwargs["network_mode"] == "none"
    assert kwargs["privileged"] is False
    assert kwargs["cap_drop"] == ["ALL"]

    published = dict(kwargs)
    published["ports"] = {"80/tcp": 8080}
    with pytest.raises(LeanRuntimeError):
        validate_container_run_kwargs(PINNED_IMAGE, published, allowed_mount_roots=(tmp_path,))

    socket_mount = container_kwargs(tmp_path)
    socket_mount["mounts"] = [{"Source": "/run/user/1001/docker.sock", "Target": "/socket"}]
    with pytest.raises(LeanRuntimeError):
        validate_container_run_kwargs(PINNED_IMAGE, socket_mount, allowed_mount_roots=(tmp_path,))

    escaped = container_kwargs(tmp_path)
    escaped["mounts"] = [{"Source": str(tmp_path.parent), "Target": "/LeanCLI"}]
    with pytest.raises(LeanRuntimeError):
        validate_container_run_kwargs(PINNED_IMAGE, escaped, allowed_mount_roots=(tmp_path,))


def test_cli_bridge_label_is_audited_but_never_used_as_container_network() -> None:
    assert LEAN_CLI_NETWORK == "lean_cli"
    validate_lean_cli_network(LEAN_CLI_NETWORK)
    for unexpected in (None, "", "lean_cli_default", "bridge", "host"):
        with pytest.raises(LeanRuntimeError):
            validate_lean_cli_network(unexpected)

    assert build_extra_docker_config()["network_mode"] == "none"


def test_container_auto_remove_is_disabled_for_prestart_audit() -> None:
    validate_container_auto_remove(False)
    for unexpected in (None, True, 0, "false"):
        with pytest.raises(LeanRuntimeError):
            validate_container_auto_remove(unexpected)


@pytest.mark.parametrize(
    "name",
    ["API_TOKEN", "BROKER_PASSWORD", "ORGANIZATION_ID", "HTTPS_PROXY"],
)
def test_container_request_rejects_sensitive_environment(
    tmp_path: Path,
    name: str,
) -> None:
    kwargs = container_kwargs(tmp_path)
    kwargs["environment"] = {name: "forbidden"}
    with pytest.raises(LeanRuntimeError):
        validate_container_run_kwargs(PINNED_IMAGE, kwargs, allowed_mount_roots=(tmp_path,))


def test_realized_container_is_validated_before_public_audit(tmp_path: Path) -> None:
    audit = validate_actual_container(
        actual_container(tmp_path),
        allowed_mount_roots=(tmp_path,),
    )
    validate_runtime_audit(audit)
    assert audit["container_network"] == "none"
    assert audit["docker_socket_mounted"] is False

    unsafe = actual_container(tmp_path)
    unsafe["Mounts"] = [{"Source": "/run/user/1001/docker.sock"}]
    with pytest.raises(LeanRuntimeError):
        validate_actual_container(unsafe, allowed_mount_roots=(tmp_path,))

    weakened = actual_container(tmp_path)
    weakened["HostConfig"]["SecurityOpt"] = ["no-new-privileges:false"]
    with pytest.raises(LeanRuntimeError):
        validate_actual_container(weakened, allowed_mount_roots=(tmp_path,))


def test_isolated_environment_drops_host_credentials_and_context(tmp_path: Path) -> None:
    environment = build_isolated_environment(
        {
            "API_TOKEN": "private",
            "DOCKER_CONTEXT": "desktop-linux",
            "HOME": "private-home",
            "PATH": "safe-path",
            "HTTPS_PROXY": "private-proxy",
        },
        home=tmp_path / "home",
        temporary_directory=tmp_path / "tmp",
        shim_directory=tmp_path / "shim",
        source_directory=tmp_path / "src",
        audit_path=tmp_path / "audit.json",
        docker_root_parent=tmp_path,
        allowed_mount_roots=(tmp_path,),
    )
    assert environment["DOCKER_HOST"] == ROOTLESS_HOST
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["PATH"] == "safe-path"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert environment["NO_PROXY"] == ""
    assert "API_TOKEN" not in environment
    assert "DOCKER_CONTEXT" not in environment


def test_cli_and_engine_configs_disable_remote_and_live_behavior(tmp_path: Path) -> None:
    assert build_cli_general_config() == {
        "database-update-frequency": "-",
        "engine-image": PINNED_IMAGE,
    }
    config = build_minimal_lean_config(tmp_path / "data")
    validate_minimal_lean_config(config, data_directory=tmp_path / "data")
    serialized = json.dumps(config, sort_keys=True).casefold()
    assert "apihistoryprovider" not in serialized
    assert "download-data" not in serialized
    assert "live-mode-brokerage" not in serialized
    assert "optimization" not in serialized
    assert config["object-store"] == "QuantConnect.Lean.Engine.Storage.LocalObjectStore"
    assert config["environments"]["backtesting"]["live-mode"] is False


def test_generated_cli_identity_and_broker_defaults_are_removed(tmp_path: Path) -> None:
    generated = build_minimal_lean_config(tmp_path / "data")
    generated.update(
        {
            "api-access-token": "",
            "environment": "backtesting",
            "ib-host": "127.0.0.1",
            "job-user-id": "0",
            "project-id": 123,
        }
    )
    sanitized = sanitize_generated_lean_config(generated)
    assert "api-access-token" not in sanitized
    assert "ib-host" not in sanitized
    assert "job-user-id" not in sanitized
    assert "project-id" not in sanitized
    validate_generated_engine_config(sanitized)

    remote = dict(sanitized)
    remote["data-provider"] = "QuantConnect.Lean.Engine.DataFeeds.ApiDataProvider"
    with pytest.raises(LeanRuntimeError):
        validate_generated_engine_config(remote)


def test_generated_paths_are_lexically_bounded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    allowed = root / "reports" / "parity" / "comparison-v1.json"
    assert_ignored_path(root, allowed)
    with pytest.raises(LeanRuntimeError):
        assert_ignored_path(root, root / "src" / "result.json")
    with pytest.raises(LeanRuntimeError):
        assert_ignored_path(root, root.parent / "escape.json")


def test_runtime_cleanup_requires_current_sentinel(tmp_path: Path) -> None:
    runtime = tmp_path / "owned-runtime"
    initialize_runtime_directory(runtime)
    (runtime / "temporary.txt").write_text("created by current run\n", encoding="utf-8")
    cleanup_runtime_directory(runtime)
    assert not runtime.exists()

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    marker = unrelated / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    with pytest.raises((LeanRuntimeError, FileNotFoundError)):
        cleanup_runtime_directory(unrelated)
    assert marker.is_file()


def test_default_operator_phase_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(operator, "perform_preflight", lambda: calls.append("preflight"))
    args = operator.build_parser().parse_args([])
    assert args.phase == "preflight"
    assert operator.phase_is_read_only(args.phase) is True
    assert operator.execute(args) == 0
    assert calls == ["preflight"]
    for phase in ("pull", "prepare", "run", "compare", "all"):
        assert operator.phase_is_read_only(phase) is False


def test_windows_preflight_stops_before_repository_or_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(operator.platform, "system", lambda: "Windows")
    monkeypatch.setattr(operator.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        operator,
        "_validate_repository_state",
        lambda: calls.append("repository"),
    )
    with pytest.raises(LeanRuntimeError):
        operator.perform_preflight()
    assert calls == []


def test_operator_source_never_selects_live_download_or_mutable_execution() -> None:
    source = (ROOT / "scripts" / "run_lean_parity_local.py").read_text(encoding="utf-8")
    assert '"--no-update"' in source
    assert '"--image",\n            PINNED_IMAGE' in source
    assert "--download-data" not in source
    assert "lean live" not in source.casefold()
    assert "optimize" not in source.casefold()
    assert "docker system" not in source.casefold()
