"""Shared browser environment diagnostics for Design preview and export tools."""

from __future__ import annotations

from typing import Any, Literal


BrowserEnvironmentStatus = Literal[
    "package_missing",
    "runtime_unavailable",
    "system_dependencies_missing",
]

PACKAGE_REMEDIATION = (
    "Install the optional Playwright Python package and Chromium browser support with "
    "`pip install playwright` and `python -m playwright install chromium`, then rerun "
    "Design preview or PDF export."
)
BROWSER_REMEDIATION = (
    "Install Playwright Chromium browser support with `python -m playwright install chromium`, "
    "then rerun Design preview or PDF export."
)
SYSTEM_DEPENDENCY_REMEDIATION = (
    "Install the system dependencies required by Playwright Chromium, then run "
    "`python -m playwright install chromium` and rerun Design preview or PDF export."
)


def playwright_package_missing_issue(exc: Exception) -> str:
    """Return a stable issue string for a missing Playwright Python package."""
    return f"Playwright Python package is not available: {type(exc).__name__}"


def browser_runtime_issue(*, context: str, exc: Exception) -> str:
    """Return a stable issue string for browser runtime failures."""
    return f"Browser environment is unavailable for {context}: {_safe_browser_error_detail(exc)}"


def classify_browser_environment_issue(message: str) -> BrowserEnvironmentStatus | None:
    """Classify known browser dependency failures from a stable issue string."""
    lowered = message.lower()
    if (
        "playwright python package is not available" in lowered
        or "playwright is not available" in lowered
        or "no module named 'playwright'" in lowered
    ):
        return "package_missing"
    if (
        "host system is missing dependencies" in lowered
        or "system dependencies are missing" in lowered
        or "missing libraries" in lowered
        or "install-deps" in lowered
    ):
        return "system_dependencies_missing"
    if (
        "browser executable is not installed" in lowered
        or "browser installation is incomplete" in lowered
        or "executable doesn't exist" in lowered
        or "please run the following command to download new browsers" in lowered
        or "playwright install" in lowered
    ):
        return "runtime_unavailable"
    return None


def browser_environment_recommendation(message: str) -> str:
    """Return an actionable remediation hint for a browser dependency failure."""
    status = classify_browser_environment_issue(message)
    if status == "package_missing":
        return PACKAGE_REMEDIATION
    if status == "system_dependencies_missing":
        return SYSTEM_DEPENDENCY_REMEDIATION
    if status == "runtime_unavailable":
        return BROWSER_REMEDIATION
    return ""


def browser_environment_metadata(message: str) -> dict[str, Any]:
    """Return stable diagnostics metadata for a browser dependency failure."""
    status = classify_browser_environment_issue(message)
    if status is None:
        return {}
    return {
        "browser_environment": status,
        "remediation": browser_environment_recommendation(message),
    }


def _safe_browser_error_detail(exc: Exception) -> str:
    raw = str(exc).strip()
    lowered = raw.lower()
    if "executable doesn't exist" in lowered:
        return "Playwright browser executable is not installed."
    if "please run the following command to download new browsers" in lowered:
        return "Playwright browser installation is incomplete."
    if "host system is missing dependencies" in lowered or "missing libraries" in lowered:
        return "Playwright Chromium system dependencies are missing."
    first_line = raw.splitlines()[0].strip() if raw else type(exc).__name__
    return first_line[:240]
