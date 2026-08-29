"""Smoke checks for the uv workspace layout (P0-T02).

These guard the property the whole monorepo rests on: every service and the
chassis are installed into one environment from repository sources, and any
service can import the chassis.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SERVICE_PACKAGES = ("identity", "catalog", "booking", "payment", "notification")
WORKSPACE_PACKAGES = ("chassis", *SERVICE_PACKAGES)

EXPECTED_SOURCE_ROOTS = {
    "chassis": REPOSITORY_ROOT / "libs" / "chassis" / "src",
    **{name: REPOSITORY_ROOT / "services" / name / "src" for name in SERVICE_PACKAGES},
}


@pytest.mark.parametrize("package_name", WORKSPACE_PACKAGES)
def test_workspace_package_is_importable(package_name: str) -> None:
    module = importlib.import_module(package_name)

    assert module.__name__ == package_name


@pytest.mark.parametrize("package_name", WORKSPACE_PACKAGES)
def test_workspace_package_resolves_to_repository_source(package_name: str) -> None:
    # An editable install must win over any same-named distribution on PyPI;
    # otherwise a service would silently run third-party code.
    module = importlib.import_module(package_name)
    assert module.__file__ is not None

    resolved_source_root = Path(module.__file__).resolve().parent.parent

    assert resolved_source_root == EXPECTED_SOURCE_ROOTS[package_name]


@pytest.mark.parametrize("package_name", WORKSPACE_PACKAGES)
def test_workspace_package_is_installed_as_distribution(package_name: str) -> None:
    distribution = importlib.metadata.distribution(package_name)

    assert distribution.version == "0.1.0"


@pytest.mark.parametrize("service_package", SERVICE_PACKAGES)
def test_chassis_is_importable_from_every_service(service_package: str) -> None:
    # Run in a subprocess so the check reflects the installed environment
    # rather than modules another test already imported.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {service_package}; import chassis"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr


def test_chassis_pulls_in_no_service_package() -> None:
    # D7: the chassis carries cross-cutting infrastructure and no domain. The
    # check is what that actually forbids — a dependency on a service — rather
    # than the chassis staying empty, which it stopped being the moment a
    # second service needed the same UUID v7 generator.
    #
    # A subprocess, so the answer is what a fresh process sees and not what
    # another test in this run happened to import first.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import chassis, sys;"
            f"services = {SERVICE_PACKAGES!r};"
            "print(sorted(m for m in sys.modules if m.split('.')[0] in services))",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"
