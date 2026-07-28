"""Fail-closed runtime contract for the local LEAN parity workflow.

The module has no network client dependency and performs no action at import
time.  The operator script supplies all mutation and execution authorization.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.parity.lean import LEAN_OBSERVATION_PREFIX

OCI_INDEX_DIGEST = "sha256:c03e9acab0ef6bd67cd44b968d10c40c13f4079164b8fe02148de45dbd0c0649"
PLATFORM_MANIFEST_DIGEST = "sha256:6cdc4112fa14ed99eca5c313bc84c8008cc07d6143e25b3f6ddeb01df2501f0e"
PINNED_IMAGE = f"quantconnect/lean@{OCI_INDEX_DIGEST}"
MUTABLE_DISCOVERY_IMAGE = "quantconnect/lean:latest"
EXPECTED_PLATFORM = "linux/amd64"
EXPECTED_ARCHITECTURE = "amd64"
EXPECTED_OS = "linux"
EXPECTED_CLI_VERSION = "1.0.227"
EXPECTED_UID = 1001
ROOTLESS_SOCKET = Path(f"/run/user/{EXPECTED_UID}/docker.sock")
ROOTLESS_HOST = f"unix://{ROOTLESS_SOCKET}"

SCENARIO_ID = "weekday_ma_next_open_v1"
FIXTURE_SHA256 = "a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a"
NORMALIZED_BARS_SHA256 = "02394c31af7b982493bcbdadd92735d7a0ee6ae04e3d38b7e3e3a5fde6cbce6d"
SYMBOL = "PARITY"
TIMEFRAME_SECONDS = 86_400
EXECUTION_MODEL = "next_bar_open"
MAX_EXECUTIONS = 5

PULL_AUTHORIZATION = "pull-pinned-lean-parity-image"
PREPARE_AUTHORIZATION = "prepare-exact-parity-fixture"
RUN_AUTHORIZATION = "execute-pinned-parity-v1"
COMPARE_AUTHORIZATION = "compare-exact-parity-v1"

RUNTIME_SCHEMA_VERSION = "1.0.0"
RUNTIME_CONTAINER_NAME = "trading_bot_lab_parity_v1"
RUNTIME_CONTAINER_LABEL = "trading_bot_lab.parity_runtime"
RUNTIME_CONTAINER_LABEL_VALUE = "v1"
RUNTIME_SENTINEL = ".trading-bot-lab-parity-runtime-v1"
RUN_STATE_SCHEMA_VERSION = "1.0.0"

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_FORBIDDEN_CONTAINER_ENV = re.compile(
    r"(?:proxy|token|credential|password|secret|account|organization|broker|exchange|live|cloud|billing|invoice|license|email|owner|project.?id|local.?id)",
    re.IGNORECASE,
)
_PROXY_KEYS = {
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


class LeanRuntimeError(RuntimeError):
    """Raised when the local LEAN runtime contract cannot be proven."""


@dataclass(frozen=True)
class RegistryIdentity:
    """Immutable registry identity selected for this host."""

    repository: str
    index_digest: str
    platform: str
    platform_digest: str


@dataclass(frozen=True)
class RootlessDockerIdentity:
    """Sanitized identity of the explicit rootless daemon."""

    host: str
    docker_root: Path
    architecture: str
    operating_system: str


@dataclass(frozen=True)
class LocalImageIdentity:
    """Sanitized local identity of the pinned image."""

    image_id: str
    repo_digest: str
    architecture: str
    operating_system: str
    size_bytes: int
    created: str


@dataclass(frozen=True)
class RuntimeState:
    """Ignored local state used only to enforce bounded execution."""

    executions: int = 0
    pulls: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "executions": self.executions,
            "pulls": self.pulls,
            "schema_version": RUN_STATE_SCHEMA_VERSION,
        }


def validate_image_reference(image: str) -> str:
    """Accept only the authorized repository and immutable OCI index digest."""

    if image != PINNED_IMAGE:
        raise LeanRuntimeError("image must equal the authorized immutable LEAN reference")
    repository, separator, digest = image.partition("@")
    if separator != "@" or repository != "quantconnect/lean":
        raise LeanRuntimeError("LEAN image repository is not authorized")
    if _DIGEST_PATTERN.fullmatch(digest) is None or digest != OCI_INDEX_DIGEST:
        raise LeanRuntimeError("LEAN image digest is invalid or unauthorized")
    if ":latest" in image or "@" not in image:
        raise LeanRuntimeError("mutable LEAN image tags are forbidden")
    return image


def map_machine_platform(machine: str) -> str:
    """Map one supported kernel architecture to the OCI platform contract."""

    normalized = machine.strip().casefold()
    if normalized in {"amd64", "x86_64"}:
        return "linux/amd64"
    if normalized in {"aarch64", "arm64"}:
        return "linux/arm64"
    raise LeanRuntimeError("unsupported machine architecture for LEAN parity")


def validate_linux_host(*, system: str | None = None, machine: str | None = None) -> None:
    """Reject non-Linux and non-amd64 execution before Docker is contacted."""

    selected_system = system or platform.system()
    selected_machine = machine or platform.machine()
    if selected_system != "Linux":
        raise LeanRuntimeError("local LEAN parity execution is supported only on Linux")
    if map_machine_platform(selected_machine) != EXPECTED_PLATFORM:
        raise LeanRuntimeError("local LEAN parity requires linux/amd64")


def validate_rootless_socket(path: Path = ROOTLESS_SOCKET, *, uid: int | None = None) -> None:
    """Validate the exact user-owned Unix socket without following alternatives."""

    selected_uid = os.getuid() if uid is None else uid
    if selected_uid != EXPECTED_UID:
        raise LeanRuntimeError("unexpected user ID for the rootless Docker contract")
    if path != ROOTLESS_SOCKET or str(path) == "/var/run/docker.sock":
        raise LeanRuntimeError("Docker socket differs from the authorized rootless endpoint")
    try:
        metadata_result = path.stat()
    except OSError as exc:
        raise LeanRuntimeError("authorized rootless Docker socket is unavailable") from exc
    if not stat.S_ISSOCK(metadata_result.st_mode):
        raise LeanRuntimeError("authorized Docker endpoint is not a Unix socket")
    if metadata_result.st_uid != EXPECTED_UID:
        raise LeanRuntimeError("authorized Docker socket is not owned by the expected user")


def validate_docker_info(
    payload: Mapping[str, object], *, user_home: Path
) -> RootlessDockerIdentity:
    """Validate only the security-relevant fields returned by Docker info."""

    options = payload.get("SecurityOptions")
    docker_root_raw = payload.get("DockerRootDir")
    architecture = payload.get("Architecture")
    operating_system = payload.get("OSType")
    if not isinstance(options, list) or "name=rootless" not in options:
        raise LeanRuntimeError("Docker daemon does not explicitly report rootless mode")
    if not isinstance(docker_root_raw, str) or not docker_root_raw:
        raise LeanRuntimeError("DockerRootDir is missing")
    docker_root = Path(docker_root_raw)
    if docker_root == Path("/var/lib/docker"):
        raise LeanRuntimeError("system DockerRootDir is forbidden")
    try:
        docker_root.resolve().relative_to(user_home.resolve())
    except ValueError as exc:
        raise LeanRuntimeError("DockerRootDir is outside the expected user home") from exc
    if (
        not isinstance(architecture, str)
        or map_machine_platform(architecture) != EXPECTED_PLATFORM
        or operating_system != EXPECTED_OS
    ):
        raise LeanRuntimeError("rootless Docker platform differs from linux/amd64")
    return RootlessDockerIdentity(
        host=ROOTLESS_HOST,
        docker_root=docker_root,
        architecture=EXPECTED_ARCHITECTURE,
        operating_system=str(operating_system),
    )


def parse_registry_identity(output: str) -> RegistryIdentity:
    """Parse the bounded buildx summary and reject an altered registry mapping."""

    name_match = re.search(r"(?m)^Name:\s+(?:docker\.io/)?([^\s]+)$", output)
    digest_match = re.search(r"(?m)^Digest:\s+(sha256:[0-9a-f]{64})$", output)
    if name_match is None or digest_match is None:
        raise LeanRuntimeError("registry metadata is incomplete")
    repository_and_ref = name_match.group(1)
    if repository_and_ref != MUTABLE_DISCOVERY_IMAGE:
        raise LeanRuntimeError("registry metadata resolved to an unauthorized repository")
    index_digest = digest_match.group(1)
    platform_entries = re.findall(
        r"(?ms)^\s*Name:\s+[^\s]+@(sha256:[0-9a-f]{64}).*?"
        r"^\s*Platform:\s+([^\s]+)$",
        output,
    )
    selected = [
        digest
        for digest, selected_platform in platform_entries
        if selected_platform == EXPECTED_PLATFORM
    ]
    if index_digest != OCI_INDEX_DIGEST or selected != [PLATFORM_MANIFEST_DIGEST]:
        raise LeanRuntimeError("registry image identity differs from the pinned runtime contract")
    return RegistryIdentity(
        repository="quantconnect/lean",
        index_digest=index_digest,
        platform=EXPECTED_PLATFORM,
        platform_digest=selected[0],
    )


def validate_local_image(payload: object) -> LocalImageIdentity:
    """Validate one Docker image-inspect response against the pinned runtime."""

    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise LeanRuntimeError("Docker image inspection must return exactly one image")
    image = payload[0]
    repo_digests = image.get("RepoDigests")
    if not isinstance(repo_digests, list):
        raise LeanRuntimeError("pinned image has no RepoDigest")
    accepted = {
        PINNED_IMAGE,
        f"docker.io/{PINNED_IMAGE}",
    }
    matches = sorted(
        value for value in repo_digests if isinstance(value, str) and value in accepted
    )
    if len(matches) != 1:
        raise LeanRuntimeError("local RepoDigest does not match the authorized OCI index")
    if image.get("Architecture") != EXPECTED_ARCHITECTURE or image.get("Os") != EXPECTED_OS:
        raise LeanRuntimeError("local pinned image platform is not linux/amd64")
    image_id = image.get("Id")
    created = image.get("Created")
    size = image.get("Size")
    if not isinstance(image_id, str) or _DIGEST_PATTERN.fullmatch(image_id) is None:
        raise LeanRuntimeError("local image ID is invalid")
    if not isinstance(created, str) or not created:
        raise LeanRuntimeError("local image creation timestamp is missing")
    if type(size) is not int or size <= 0:
        raise LeanRuntimeError("local image size is invalid")
    return LocalImageIdentity(
        image_id=image_id,
        repo_digest=matches[0],
        architecture=EXPECTED_ARCHITECTURE,
        operating_system=EXPECTED_OS,
        size_bytes=size,
        created=created,
    )


def validate_cli_version(version: str | None = None) -> str:
    """Require the installed CLI version audited by this compatibility contract."""

    selected = metadata.version("lean") if version is None else version
    if selected != EXPECTED_CLI_VERSION:
        raise LeanRuntimeError("installed LEAN CLI version is outside the audited runtime contract")
    return selected


def require_authorization(observed: str | None, expected: str, *, action: str) -> None:
    """Require an exact public authorization phrase for a mutating phase."""

    if observed != expected:
        raise LeanRuntimeError(f"{action} requires its exact explicit authorization phrase")


def build_cli_general_config() -> dict[str, str]:
    """Return a credential-free temporary CLI configuration."""

    return {
        "database-update-frequency": "-",
        "engine-image": PINNED_IMAGE,
    }


def build_minimal_lean_config(data_directory: Path) -> dict[str, object]:
    """Return the minimum local-only engine configuration for the parity project."""

    selected_data = data_directory.resolve()
    config: dict[str, object] = {
        "api-handler": "QuantConnect.Api.Api",
        "data-cache-provider": "QuantConnect.Lean.Engine.DataFeeds.ZipDataCacheProvider",
        "data-folder": str(selected_data),
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "environments": {
            "backtesting": {
                "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
                "history-provider": [
                    "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
                ],
                "live-mode": False,
                "real-time-handler": (
                    "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler"
                ),
                "result-handler": ("QuantConnect.Lean.Engine.Results.BacktestingResultHandler"),
                "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
                "transaction-handler": (
                    "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler"
                ),
            }
        },
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "job-organization-id": "",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "show-missing-data-logs": False,
    }
    validate_minimal_lean_config(config, data_directory=selected_data)
    return config


def validate_minimal_lean_config(
    config: Mapping[str, object],
    *,
    data_directory: Path,
) -> None:
    """Reject remote providers, identity, credentials, live, and optimization settings."""

    expected_keys = {
        "api-handler",
        "data-cache-provider",
        "data-folder",
        "data-provider",
        "environments",
        "factor-file-provider",
        "job-organization-id",
        "job-queue-handler",
        "log-handler",
        "map-file-provider",
        "messaging-handler",
        "object-store",
        "show-missing-data-logs",
    }
    if set(config) != expected_keys:
        raise LeanRuntimeError("temporary LEAN configuration has unexpected fields")
    if config.get("data-folder") != str(data_directory.resolve()):
        raise LeanRuntimeError("temporary LEAN data folder differs from the approved local root")
    if config.get("job-organization-id") != "":
        raise LeanRuntimeError("temporary LEAN configuration contains organization identity")
    if config.get("data-provider") != "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider":
        raise LeanRuntimeError("temporary LEAN configuration selects a non-local data provider")
    if config.get("object-store") != "QuantConnect.Lean.Engine.Storage.LocalObjectStore":
        raise LeanRuntimeError("temporary LEAN configuration selects non-local Object Store")
    environments = config.get("environments")
    if not isinstance(environments, dict) or set(environments) != {"backtesting"}:
        raise LeanRuntimeError("temporary LEAN configuration must contain only backtesting")
    backtesting = environments["backtesting"]
    if not isinstance(backtesting, dict) or backtesting.get("live-mode") is not False:
        raise LeanRuntimeError("temporary LEAN configuration must disable live mode")
    histories = backtesting.get("history-provider")
    if histories != [
        "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
    ]:
        raise LeanRuntimeError("temporary LEAN history provider must remain local")
    serialized = json.dumps(config, allow_nan=False, sort_keys=True).casefold()
    forbidden = (
        "api-access-token",
        "api-token",
        "brokerage",
        "cloud-id",
        "credential",
        "data-purchase",
        "download-data",
        "live-mode-brokerage",
        "local-id",
        "optimization",
        'organization-id": "' if config.get("job-organization-id") else "never-match",
        "password",
        "remote url",
    )
    if any(token in serialized for token in forbidden):
        raise LeanRuntimeError("temporary LEAN configuration contains a forbidden setting")
    if "://" in serialized or "@" in serialized:
        raise LeanRuntimeError("temporary LEAN configuration contains a remote location")


def sanitize_generated_lean_config(config: Mapping[str, object]) -> dict[str, object]:
    """Remove CLI-injected identity and broker defaults, then validate local-only use."""

    sanitized = dict(config)
    injected_keys = {
        "api-access-token",
        "cloud-id",
        "ib-host",
        "ib-port",
        "ib-tws-dir",
        "iqfeed-host",
        "job-organization-id",
        "job-user-id",
        "organization-id",
        "project-id",
    }
    for key in injected_keys:
        sanitized.pop(key, None)
    validate_generated_engine_config(sanitized)
    return sanitized


def validate_generated_engine_config(config: Mapping[str, object]) -> None:
    """Validate the complete engine config after the audited CLI adds local defaults."""

    if config.get("environment") != "backtesting":
        raise LeanRuntimeError("generated LEAN configuration is not backtesting-only")
    environments = config.get("environments")
    if not isinstance(environments, dict):
        raise LeanRuntimeError("generated LEAN environments are malformed")
    backtesting = environments.get("backtesting")
    if not isinstance(backtesting, dict) or backtesting.get("live-mode") is not False:
        raise LeanRuntimeError("generated LEAN configuration does not disable live mode")
    if config.get("data-provider") != "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider":
        raise LeanRuntimeError("generated LEAN configuration selected a remote data provider")
    if config.get("object-store") != "QuantConnect.Lean.Engine.Storage.LocalObjectStore":
        raise LeanRuntimeError("generated LEAN configuration selected remote Object Store")
    forbidden_keys = {
        "api-access-token",
        "api-token",
        "brokerage",
        "cloud-id",
        "data-download",
        "data-provider-historical",
        "data-purchase-limit",
        "download-data",
        "job-organization-id",
        "job-user-id",
        "live-mode-brokerage",
        "local-id",
        "optimization",
        "organization-id",
        "project-id",
    }
    lowered = {str(key).casefold() for key in config}
    if lowered & forbidden_keys:
        raise LeanRuntimeError("generated LEAN configuration contains a forbidden field")
    serialized = json.dumps(config, allow_nan=False, sort_keys=True).casefold()
    forbidden_values = (
        "quantconnect.lean.engine.datafeeds.apidataprovider",
        "http://",
        "https://",
        "live-mode-brokerage",
    )
    if any(value in serialized for value in forbidden_values):
        raise LeanRuntimeError("generated LEAN configuration contains a remote or live value")


def build_extra_docker_config() -> dict[str, object]:
    """Return the strict container settings carried through the official CLI option."""

    config: dict[str, object] = {
        "cap_drop": ["ALL"],
        "hostname": "parity-fixture-v1",
        "labels": {RUNTIME_CONTAINER_LABEL: RUNTIME_CONTAINER_LABEL_VALUE},
        "mem_limit": "2g",
        "name": RUNTIME_CONTAINER_NAME,
        "nano_cpus": 2_000_000_000,
        "network_mode": "none",
        "pids_limit": 256,
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
    }
    validate_extra_docker_config(config)
    return config


def validate_extra_docker_config(config: Mapping[str, object]) -> None:
    """Reject any relaxation or unsupported container setting."""

    if dict(config) != {
        "cap_drop": ["ALL"],
        "hostname": "parity-fixture-v1",
        "labels": {RUNTIME_CONTAINER_LABEL: RUNTIME_CONTAINER_LABEL_VALUE},
        "mem_limit": "2g",
        "name": RUNTIME_CONTAINER_NAME,
        "nano_cpus": 2_000_000_000,
        "network_mode": "none",
        "pids_limit": 256,
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
    }:
        raise LeanRuntimeError("extra Docker configuration differs from the runtime contract")


def build_isolated_environment(
    base: Mapping[str, str],
    *,
    home: Path,
    temporary_directory: Path,
    shim_directory: Path,
    source_directory: Path,
    audit_path: Path,
    docker_root_parent: Path,
    allowed_mount_roots: Sequence[Path],
) -> dict[str, str]:
    """Build the exact host process environment without copying credentials."""

    environment = {
        key: value
        for key, value in base.items()
        if key not in _PROXY_KEYS
        and key not in {"DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "TMPDIR", "PYTHONPATH"}
        and _FORBIDDEN_CONTAINER_ENV.search(key) is None
    }
    blocked_proxy = "http://127.0.0.1:9"
    environment.update(
        {
            "ALL_PROXY": blocked_proxy,
            "DOCKER_HOST": ROOTLESS_HOST,
            "HOME": str(home),
            "HTTP_PROXY": blocked_proxy,
            "HTTPS_PROXY": blocked_proxy,
            "NO_PROXY": "",
            "PYTHONPATH": os.pathsep.join((str(shim_directory), str(source_directory))),
            "TMPDIR": str(temporary_directory),
            "TRADING_BOT_LAB_PARITY_ALLOWED_ROOTS": json.dumps(
                [str(path.resolve()) for path in allowed_mount_roots],
                separators=(",", ":"),
            ),
            "TRADING_BOT_LAB_PARITY_AUDIT_PATH": str(audit_path),
            "TRADING_BOT_LAB_PARITY_DOCKER_ROOT_PARENT": str(docker_root_parent.resolve()),
            "TRADING_BOT_LAB_PARITY_GUARD": "v1",
            "all_proxy": blocked_proxy,
            "http_proxy": blocked_proxy,
            "https_proxy": blocked_proxy,
            "no_proxy": "",
        }
    )
    return environment


def validate_container_run_kwargs(
    image: str,
    kwargs: Mapping[str, object],
    *,
    allowed_mount_roots: Sequence[Path],
) -> None:
    """Validate the final Docker SDK request before the container is created."""

    validate_image_reference(image)
    required = build_extra_docker_config()
    for key in (
        "cap_drop",
        "hostname",
        "labels",
        "mem_limit",
        "name",
        "nano_cpus",
        "network_mode",
        "pids_limit",
        "privileged",
        "security_opt",
    ):
        if kwargs.get(key) != required[key]:
            raise LeanRuntimeError(f"container setting {key} differs from the runtime contract")
    for key in ("network", "networking_config", "pid_mode", "uts_mode"):
        if kwargs.get(key) not in (None, ""):
            raise LeanRuntimeError(f"container setting {key} is forbidden")
    if kwargs.get("ports") not in (None, {}):
        raise LeanRuntimeError("published container ports are forbidden")
    if kwargs.get("publish_all_ports") not in (None, False):
        raise LeanRuntimeError("automatic container port publication is forbidden")
    if kwargs.get("ipc_mode") not in (None, "", "private"):
        raise LeanRuntimeError("host or shared IPC is forbidden")
    _validate_container_environment(kwargs.get("environment", {}))
    _validate_volume_sources(kwargs.get("volumes", {}), allowed_mount_roots)
    _validate_mount_sources(kwargs.get("mounts", []), allowed_mount_roots)


def _validate_container_environment(environment: object) -> None:
    if isinstance(environment, list):
        keys = [str(item).partition("=")[0] for item in environment]
    elif isinstance(environment, dict):
        keys = [str(key) for key in environment]
    else:
        raise LeanRuntimeError("container environment has an unsupported shape")
    if any(_FORBIDDEN_CONTAINER_ENV.search(key) for key in keys):
        raise LeanRuntimeError("container environment contains a forbidden variable")


def _validate_volume_sources(volumes: object, allowed_roots: Sequence[Path]) -> None:
    if not isinstance(volumes, dict):
        raise LeanRuntimeError("container volumes have an unsupported shape")
    for raw_source, binding in volumes.items():
        _validate_mount_source(str(raw_source), allowed_roots)
        if not isinstance(binding, dict) or binding.get("mode") not in {"ro", "rw"}:
            raise LeanRuntimeError("container volume binding is malformed")


def _validate_mount_sources(mounts: object, allowed_roots: Sequence[Path]) -> None:
    if not isinstance(mounts, list):
        raise LeanRuntimeError("container mounts have an unsupported shape")
    for mount in mounts:
        if not isinstance(mount, Mapping):
            raise LeanRuntimeError("container mount is malformed")
        source = mount.get("Source", mount.get("source"))
        if not isinstance(source, str):
            raise LeanRuntimeError("container mount source is missing")
        _validate_mount_source(source, allowed_roots)


def _validate_mount_source(source: str, allowed_roots: Sequence[Path]) -> None:
    selected = Path(source)
    if not selected.is_absolute() or "docker.sock" in source.casefold():
        raise LeanRuntimeError("container mount source is unsafe")
    resolved = selected.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            return
        except ValueError:
            continue
    raise LeanRuntimeError("container mount escapes the approved runtime roots")


def validate_actual_container(
    attrs: Mapping[str, object],
    *,
    allowed_mount_roots: Sequence[Path],
) -> dict[str, object]:
    """Validate Docker's realized container configuration and return public audit data."""

    host = attrs.get("HostConfig")
    config = attrs.get("Config")
    mounts = attrs.get("Mounts")
    if not isinstance(host, dict) or not isinstance(config, dict) or not isinstance(mounts, list):
        raise LeanRuntimeError("realized container inspection is incomplete")
    if host.get("NetworkMode") != "none" or host.get("Privileged") is not False:
        raise LeanRuntimeError("realized container is not isolated from networking or privilege")
    if host.get("PortBindings") not in (None, {}):
        raise LeanRuntimeError("realized container published host ports")
    if host.get("PidMode") not in (None, "") or host.get("IpcMode") not in (None, "", "private"):
        raise LeanRuntimeError("realized container uses a host namespace")
    if host.get("CapDrop") != ["ALL"]:
        raise LeanRuntimeError("realized container did not drop all capabilities")
    security = host.get("SecurityOpt")
    if security != ["no-new-privileges:true"]:
        raise LeanRuntimeError("realized container lacks no-new-privileges")
    if host.get("Memory") != 2 * 1024**3 or host.get("NanoCpus") != 2_000_000_000:
        raise LeanRuntimeError("realized container resource bounds differ from the contract")
    if host.get("PidsLimit") != 256:
        raise LeanRuntimeError("realized container process bound differs from the contract")
    labels = config.get("Labels")
    if (
        not isinstance(labels, dict)
        or labels.get(RUNTIME_CONTAINER_LABEL) != RUNTIME_CONTAINER_LABEL_VALUE
    ):
        raise LeanRuntimeError("realized container lacks the parity runtime label")
    _validate_container_environment(config.get("Env", []))
    for mount in mounts:
        if not isinstance(mount, dict):
            raise LeanRuntimeError("realized container has a malformed mount")
        source = mount.get("Source")
        if not isinstance(source, str):
            raise LeanRuntimeError("realized container mount source is missing")
        if "docker.sock" in source.casefold():
            raise LeanRuntimeError("realized container has an unsafe mount")
        _validate_mount_source(source, allowed_mount_roots)
    return {
        "capabilities_dropped": True,
        "container_network": "none",
        "credentials_mounted": False,
        "docker_socket_mounted": False,
        "no_new_privileges": True,
        "privileged": False,
        "proxy_environment_present": False,
        "resource_bounds_present": True,
        "schema_version": RUNTIME_SCHEMA_VERSION,
    }


def validate_runtime_audit(payload: Mapping[str, object]) -> None:
    """Reject malformed, private, or weakened runtime audit output."""

    expected = {
        "capabilities_dropped": True,
        "container_network": "none",
        "credentials_mounted": False,
        "docker_socket_mounted": False,
        "no_new_privileges": True,
        "privileged": False,
        "proxy_environment_present": False,
        "resource_bounds_present": True,
        "schema_version": RUNTIME_SCHEMA_VERSION,
    }
    if dict(payload) != expected:
        raise LeanRuntimeError("runtime audit is malformed or indicates weakened isolation")
    serialized = json.dumps(payload, allow_nan=False, sort_keys=True)
    if "/" in serialized or "\\" in serialized or "@" in serialized or "://" in serialized:
        raise LeanRuntimeError("runtime audit contains private path or remote metadata")


def load_runtime_state(path: Path) -> RuntimeState:
    """Load the bounded ignored state without repairing malformed data."""

    if not path.exists():
        return RuntimeState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeanRuntimeError("runtime state is unreadable or malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"executions", "pulls", "schema_version"}:
        raise LeanRuntimeError("runtime state has unexpected fields")
    if payload["schema_version"] != RUN_STATE_SCHEMA_VERSION:
        raise LeanRuntimeError("runtime state schema version is unsupported")
    executions = payload["executions"]
    pulls = payload["pulls"]
    if type(executions) is not int or not 0 <= executions <= MAX_EXECUTIONS:
        raise LeanRuntimeError("runtime execution count is invalid")
    if type(pulls) is not int or not 0 <= pulls <= 1:
        raise LeanRuntimeError("runtime pull count is invalid")
    return RuntimeState(executions=executions, pulls=pulls)


def write_runtime_state(path: Path, state: RuntimeState) -> None:
    """Atomically write the ignored bounded state with private permissions."""

    if state.executions > MAX_EXECUTIONS or state.pulls > 1:
        raise LeanRuntimeError("runtime state exceeds its authorization bounds")
    atomic_write_text(path, json.dumps(state.as_dict(), indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def increment_execution(state: RuntimeState) -> RuntimeState:
    """Count one launched LEAN execution and reject attempts beyond five."""

    if state.executions >= MAX_EXECUTIONS:
        raise LeanRuntimeError("maximum authorized LEAN parity execution count reached")
    return RuntimeState(executions=state.executions + 1, pulls=state.pulls)


def increment_pull(state: RuntimeState) -> RuntimeState:
    """Count the one authorized image pull."""

    if state.pulls >= 1:
        raise LeanRuntimeError("authorized image pull has already been consumed")
    return RuntimeState(executions=state.executions, pulls=state.pulls + 1)


class ExclusiveRunLock:
    """Narrow same-workflow lock that never removes a lock it did not create."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def __enter__(self) -> ExclusiveRunLock:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(self._descriptor, b"parity-v1\n")
        except FileExistsError as exc:
            raise LeanRuntimeError("another parity workflow may already be active") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._descriptor is None:
            return
        os.close(self._descriptor)
        self._descriptor = None
        self.path.unlink(missing_ok=True)


def initialize_runtime_directory(path: Path) -> None:
    """Create one empty private runtime directory with an ownership sentinel."""

    if path.exists():
        raise LeanRuntimeError("temporary parity runtime directory already exists")
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)
    sentinel = path / RUNTIME_SENTINEL
    sentinel.write_text("v1\n", encoding="utf-8")
    sentinel.chmod(0o600)


def cleanup_runtime_directory(path: Path) -> None:
    """Remove only a runtime directory carrying the exact current-run sentinel."""

    sentinel = path / RUNTIME_SENTINEL
    if not path.is_dir() or sentinel.read_text(encoding="utf-8") != "v1\n":
        raise LeanRuntimeError("refusing to clean an unowned runtime directory")
    shutil.rmtree(path)


def write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write deterministic JSON under an already-approved ignored runtime root."""

    atomic_write_text(
        path,
        json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
    )
    path.chmod(0o600)


def verify_exact_lf_bytes(path: Path, expected_hash: str = FIXTURE_SHA256) -> str:
    """Reject CRLF, missing final LF, or an exact-byte fixture mismatch."""

    if path.is_symlink() or not path.is_file():
        raise LeanRuntimeError("parity fixture must be a regular non-symlink file")
    payload = path.read_bytes()
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise LeanRuntimeError("parity fixture must contain exact LF-only bytes")
    digest = sha256(payload).hexdigest()
    if digest != expected_hash:
        raise LeanRuntimeError("parity fixture hash differs from the runtime contract")
    return digest


def assert_ignored_path(root: Path, path: Path) -> None:
    """Require generated state to remain beneath one documented ignored root."""

    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise LeanRuntimeError("generated parity path escapes the repository") from exc
    normalized = relative.as_posix()
    if not normalized.startswith(
        (
            "lean-workspace/data/",
            "lean-workspace/Strategies/ParityFixtureV1/backtests/",
            "logs/parity/",
            "reports/parity/",
        )
    ):
        raise LeanRuntimeError("generated parity path is outside approved ignored roots")


def _allowed_mount_roots_from_environment() -> tuple[Path, ...]:
    raw = os.environ.get("TRADING_BOT_LAB_PARITY_ALLOWED_ROOTS", "")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LeanRuntimeError("runtime guard mount roots are malformed") from exc
    if (
        not isinstance(payload, list)
        or not payload
        or any(not isinstance(item, str) for item in payload)
    ):
        raise LeanRuntimeError("runtime guard mount roots are malformed")
    roots = tuple(Path(item) for item in payload)
    if any(not root.is_absolute() for root in roots):
        raise LeanRuntimeError("runtime guard mount roots must be absolute")
    return roots


def install_lean_cli_runtime_guards() -> None:
    """Install process-local guards for the audited LEAN CLI 1.0.227 behavior."""

    if os.environ.get("TRADING_BOT_LAB_PARITY_GUARD") != "v1":
        raise LeanRuntimeError("LEAN parity runtime guard was not explicitly enabled")
    validate_cli_version()
    validate_image_reference(PINNED_IMAGE)
    allowed_roots = _allowed_mount_roots_from_environment()
    audit_path = Path(os.environ.get("TRADING_BOT_LAB_PARITY_AUDIT_PATH", ""))
    if not audit_path.is_absolute():
        raise LeanRuntimeError("runtime audit path must be absolute")

    from docker.models.containers import ContainerCollection
    from lean.components.config.lean_config_manager import LeanConfigManager
    from lean.components.docker.docker_manager import DockerManager
    from lean.components.docker.lean_runner import LeanRunner

    if getattr(ContainerCollection.run, "_trading_bot_lab_parity_guard", False):
        return

    original_parse = LeanRunner.parse_extra_docker_config
    original_complete = LeanConfigManager.get_complete_lean_config
    original_get_client = DockerManager._get_docker_client
    original_get_container = DockerManager.get_container_by_name

    def guarded_parse(run_options: dict[str, Any], extra: dict[str, Any] | None) -> None:
        if extra is None:
            raise LeanRuntimeError("extra Docker configuration is required")
        validate_extra_docker_config(extra)
        original_parse(run_options, {"name": extra["name"]})
        for key in (
            "cap_drop",
            "hostname",
            "labels",
            "mem_limit",
            "nano_cpus",
            "network_mode",
            "pids_limit",
            "privileged",
            "security_opt",
        ):
            run_options[key] = extra[key]

    def guarded_complete(
        self: Any,
        environment: str,
        algorithm_file: Path,
        debugging_method: object,
    ) -> dict[str, object]:
        generated = original_complete(self, environment, algorithm_file, debugging_method)
        return sanitize_generated_lean_config(generated)

    def guarded_create_network(self: Any, name: str) -> None:
        if name != "lean_cli_default":
            raise LeanRuntimeError("LEAN attempted to create an unexpected Docker network")
        return None

    def guarded_get_client(self: Any) -> Any:
        client = original_get_client(self)
        docker_root_parent = Path(os.environ.get("TRADING_BOT_LAB_PARITY_DOCKER_ROOT_PARENT", ""))
        if not docker_root_parent.is_absolute():
            raise LeanRuntimeError("rootless Docker parent must be absolute")
        identity = validate_docker_info(client.info(), user_home=docker_root_parent)
        if identity.host != ROOTLESS_HOST:
            raise LeanRuntimeError("Docker SDK resolved to an unexpected daemon")
        return client

    def guarded_get_container(self: Any, name: str) -> Any:
        if name != RUNTIME_CONTAINER_NAME:
            raise LeanRuntimeError("LEAN requested an unexpected container name")
        existing = original_get_container(self, name)
        if existing is not None:
            raise LeanRuntimeError("authorized parity container name is already in use")
        return None

    def guarded_image_installed(self: Any, image: object) -> bool:
        selected = str(image)
        validate_image_reference(selected)
        client = guarded_get_client(self)
        try:
            client.images.get(selected)
        except Exception as exc:
            from docker.errors import ImageNotFound

            if isinstance(exc, ImageNotFound):
                return False
            raise
        return True

    def guarded_run(
        self: Any,
        image: object,
        command: object = None,
        stdout: bool = True,
        stderr: bool = False,
        remove: bool = False,
        **kwargs: Any,
    ) -> Any:
        del stdout, stderr
        selected = str(image)
        validate_image_reference(selected)
        if kwargs.pop("network", None) not in (None, "lean_cli_default"):
            raise LeanRuntimeError("LEAN selected an unexpected Docker network")
        kwargs.pop("extra_hosts", None)
        kwargs.pop("networking_config", None)
        validate_container_run_kwargs(selected, kwargs, allowed_mount_roots=allowed_roots)
        try:
            self.client.images.get(selected)
        except Exception as exc:
            raise LeanRuntimeError("pinned image disappeared before container creation") from exc
        if kwargs.pop("detach", None) is not True:
            raise LeanRuntimeError("LEAN container must be created detached")
        kwargs["remove"] = remove
        container = self.create(selected, command, **kwargs)
        try:
            container.reload()
            audit = validate_actual_container(
                container.attrs,
                allowed_mount_roots=allowed_roots,
            )
            validate_runtime_audit(audit)
            write_private_json(audit_path, audit)
            container.start()
            return container
        except Exception:
            with suppress(Exception):
                container.remove(force=False)
            raise

    guarded_run._trading_bot_lab_parity_guard = True  # type: ignore[attr-defined]
    LeanConfigManager.get_complete_lean_config = guarded_complete
    LeanRunner.parse_extra_docker_config = staticmethod(guarded_parse)
    DockerManager.create_network = guarded_create_network
    DockerManager._get_docker_client = guarded_get_client
    DockerManager.get_container_by_name = guarded_get_container
    DockerManager.image_installed = guarded_image_installed
    ContainerCollection.run = guarded_run


def public_contract_summary() -> dict[str, object]:
    """Return deterministic public identifiers suitable for sanitized evidence."""

    return {
        "execution_model": EXECUTION_MODEL,
        "fixture_sha256": FIXTURE_SHA256,
        "image_oci_index_digest": OCI_INDEX_DIGEST,
        "lean_cli_version": EXPECTED_CLI_VERSION,
        "normalized_bars_sha256": NORMALIZED_BARS_SHA256,
        "observation_prefix": LEAN_OBSERVATION_PREFIX,
        "platform": EXPECTED_PLATFORM,
        "platform_manifest_digest": PLATFORM_MANIFEST_DIGEST,
        "scenario_id": SCENARIO_ID,
        "symbol": SYMBOL,
        "timeframe_seconds": TIMEFRAME_SECONDS,
    }


__all__ = [
    "COMPARE_AUTHORIZATION",
    "EXECUTION_MODEL",
    "ExclusiveRunLock",
    "EXPECTED_CLI_VERSION",
    "EXPECTED_PLATFORM",
    "FIXTURE_SHA256",
    "LeanRuntimeError",
    "LocalImageIdentity",
    "MAX_EXECUTIONS",
    "MUTABLE_DISCOVERY_IMAGE",
    "NORMALIZED_BARS_SHA256",
    "OCI_INDEX_DIGEST",
    "PINNED_IMAGE",
    "PLATFORM_MANIFEST_DIGEST",
    "PREPARE_AUTHORIZATION",
    "PULL_AUTHORIZATION",
    "ROOTLESS_HOST",
    "ROOTLESS_SOCKET",
    "RUN_AUTHORIZATION",
    "RUNTIME_CONTAINER_LABEL",
    "RUNTIME_CONTAINER_LABEL_VALUE",
    "RUNTIME_CONTAINER_NAME",
    "RuntimeState",
    "SYMBOL",
    "TIMEFRAME_SECONDS",
    "assert_ignored_path",
    "build_cli_general_config",
    "build_extra_docker_config",
    "build_isolated_environment",
    "build_minimal_lean_config",
    "cleanup_runtime_directory",
    "increment_execution",
    "increment_pull",
    "initialize_runtime_directory",
    "install_lean_cli_runtime_guards",
    "load_runtime_state",
    "map_machine_platform",
    "parse_registry_identity",
    "public_contract_summary",
    "require_authorization",
    "validate_actual_container",
    "validate_container_run_kwargs",
    "validate_cli_version",
    "validate_docker_info",
    "validate_extra_docker_config",
    "validate_generated_engine_config",
    "validate_image_reference",
    "validate_linux_host",
    "validate_local_image",
    "validate_minimal_lean_config",
    "sanitize_generated_lean_config",
    "validate_rootless_socket",
    "validate_runtime_audit",
    "verify_exact_lf_bytes",
    "write_private_json",
    "write_runtime_state",
]
