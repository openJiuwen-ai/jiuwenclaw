import os
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_PROTOBUF5_COMPATIBLE_OTEL = Version("1.28.0")
OTEL_REQUIREMENTS = {
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-exporter-otlp-proto-http",
}


def test_opentelemetry_dependencies_exclude_protobuf4_only_proto_versions():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = {
        Requirement(raw).name: Requirement(raw)
        for raw in pyproject["project"]["dependencies"]
    }

    for name in OTEL_REQUIREMENTS:
        specifier = requirements[name].specifier
        assert specifier.contains(MIN_PROTOBUF5_COMPATIBLE_OTEL), (
            f"{name} must allow OpenTelemetry {MIN_PROTOBUF5_COMPATIBLE_OTEL}"
        )
        assert not specifier.contains(Version("1.27.0")), (
            f"{name} must exclude OpenTelemetry 1.27.0 and older because "
            "opentelemetry-proto<1.28 requires protobuf<5"
        )


# ---------------------------------------------------------------------------
# agent-os deploy requirements compatibility
#
# agent-os deploys jiuwenswarm from a frozen requirements.txt
# (agent-os/deploy/requirements.txt). These tests fail whenever the freeze
# drifts outside the ranges declared in this project's pyproject.toml, so
# incompatible bounds get caught in CI instead of breaking the deployment.
# ---------------------------------------------------------------------------

DEPLOY_REQUIREMENTS_ENV = "AGENTOS_DEPLOY_REQUIREMENTS"
DEFAULT_DEPLOY_REQUIREMENTS = PROJECT_ROOT.parent / "agent-os" / "deploy" / "requirements.txt"


def _load_deploy_pins(requirements_path: Path) -> dict[str, Version]:
    """Parse a pip-freeze style requirements file into name -> pinned version."""
    pins: dict[str, Version] = {}
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        req = Requirement(line)
        exact_pins = [spec for spec in req.specifier if spec.operator in ("==", "===")]
        if req.url is not None or len(exact_pins) != 1:
            continue  # not a freeze-style pin (e.g. a direct URL reference)
        pins[canonicalize_name(req.name)] = Version(exact_pins[0].version)
    return pins


@pytest.fixture(scope="module")
def deploy_pins() -> dict[str, Version]:
    env_value = os.environ.get(DEPLOY_REQUIREMENTS_ENV)
    requirements_path = Path(env_value) if env_value else DEFAULT_DEPLOY_REQUIREMENTS
    if not requirements_path.is_file():
        if env_value:
            pytest.fail(f"{DEPLOY_REQUIREMENTS_ENV}={env_value} does not exist")
        pytest.skip(
            "agent-os deploy requirements not found next to jiuwenswarm; "
            f"set {DEPLOY_REQUIREMENTS_ENV} to enable this test"
        )
    return _load_deploy_pins(requirements_path)


def _iter_dependency_specs(pyproject: dict, sources: tuple[str, ...]):
    """Yield (source, Requirement) for the requested pyproject spec sections.

    `sources` selects which sections to walk: "project" for
    [project.dependencies], "extras" for [project.optional-dependencies] and
    "groups" for [dependency-groups]. `include-group` tables inside
    [dependency-groups] are ignored.
    """
    if "project" in sources:
        for raw in pyproject["project"]["dependencies"]:
            yield "[project.dependencies]", Requirement(raw)
    if "extras" in sources:
        for extra, raws in pyproject["project"].get("optional-dependencies", {}).items():
            for raw in raws:
                yield f"[project.optional-dependencies.{extra}]", Requirement(raw)
    if "groups" in sources:
        for group, raws in pyproject.get("dependency-groups", {}).items():
            for raw in raws:
                if isinstance(raw, str):
                    yield f"[dependency-groups.{group}]", Requirement(raw)


def _conflict_report(pyproject_path: Path, requirements_path: Path, problems: list[str]) -> str:
    """Format an actionable interception report for dependency conflicts.

    Rendered when a jiuwenswarm constraint change (or an agent-os freeze drift)
    makes the two files incompatible, so the failure tells the developer
    exactly how to sync the change to the agent-os repository.
    """
    bar = "=" * 74
    return "\n".join(
        [
            "",
            bar,
            "BLOCKED: dependency conflict between jiuwenswarm and agent-os deploy",
            bar,
            f"  jiuwenswarm constraints : {pyproject_path}",
            f"  agent-os freeze        : {requirements_path}",
            "",
            "Conflicts:",
            *(f"  - {problem}" for problem in problems),
            "",
            "This change must NOT merge as-is. Resolve by ONE of:",
            "  1. UPDATE THE AGENT-OS REPO (required when jiuwenswarm constraints",
            "     changed on purpose): adjust the pins listed above in",
            "     agent-os/deploy/requirements.txt so they satisfy the new",
            "     constraints, verify the deployment, and submit the sync change",
            "     to the agent-os repository together with this one.",
            "  2. Or widen the jiuwenswarm constraint so the deployed pin fits,",
            "     if the new bound was not intentional.",
            "",
            "CI will keep failing until both repos are consistent.",
            bar,
        ]
    )


def test_agentos_deploy_requirements_satisfy_core_dependencies(deploy_pins):
    """Every core dependency is installed by agent-os deploy within range."""
    requirements_path = Path(
        os.environ.get(DEPLOY_REQUIREMENTS_ENV, DEFAULT_DEPLOY_REQUIREMENTS)
    )
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    problems = []
    for source, req in _iter_dependency_specs(pyproject, ("project",)):
        if req.marker is not None:
            continue
        name = canonicalize_name(req.name)
        pinned = deploy_pins.get(name)
        if pinned is None:
            problems.append(
                f"{source}: {req.name} ({req.specifier}) is MISSING from agent-os "
                f"deploy requirements -> add it to agent-os/deploy/requirements.txt "
                f"and sync the agent-os repo"
            )
            continue
        if req.url is not None:
            continue  # direct URL reference; presence is all we can verify
        if not req.specifier.contains(pinned, prereleases=True):
            problems.append(
                f"{source}: {req.name} requires {req.specifier}, but agent-os deploy "
                f"requirements pin {pinned} -> update the pin in "
                f"agent-os/deploy/requirements.txt and sync the agent-os repo"
            )
    assert not problems, _conflict_report(
        pyproject_path, requirements_path, problems
    )


def test_agentos_deploy_requirements_satisfy_optional_extras_and_groups(deploy_pins):
    """Optional extras / dependency groups installed by agent-os deploy stay in range."""
    requirements_path = Path(
        os.environ.get(DEPLOY_REQUIREMENTS_ENV, DEFAULT_DEPLOY_REQUIREMENTS)
    )
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    problems = []
    for source, req in _iter_dependency_specs(pyproject, ("extras", "groups")):
        if req.marker is not None or req.url is not None:
            continue
        pinned = deploy_pins.get(canonicalize_name(req.name))
        if pinned is None:
            continue  # extra / group not part of the deployment image
        if not req.specifier.contains(pinned, prereleases=True):
            problems.append(
                f"{source}: {req.name} requires {req.specifier}, but agent-os deploy "
                f"requirements pin {pinned} -> update the pin in "
                f"agent-os/deploy/requirements.txt and sync the agent-os repo"
            )
    assert not problems, _conflict_report(
        pyproject_path, requirements_path, problems
    )
