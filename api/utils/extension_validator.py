"""Chrome extension validator — catches errors Chrome would throw when loading an unpacked extension.

Performs four layers of validation:
1. Manifest schema (required fields, valid keys, valid permissions)
2. File references (all paths declared in manifest.json exist on disk)
3. JavaScript syntax (runs `node --check` on .js files)
4. MV3 compatibility (scans for removed/changed APIs)
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Known valid Manifest V3 top-level keys
# https://developer.chrome.com/docs/extensions/reference/manifest
# ---------------------------------------------------------------------------

VALID_MV3_MANIFEST_KEYS = {
    "manifest_version",
    "name",
    "version",
    "description",
    "icons",
    "action",
    "author",
    "background",
    "chrome_settings_overrides",
    "chrome_url_overrides",
    "commands",
    "content_scripts",
    "content_security_policy",
    "cross_origin_embedder_policy",
    "cross_origin_opener_policy",
    "declarative_net_request",
    "default_locale",
    "devtools_page",
    "differential_fingerprint",
    "event_rules",
    "export",
    "externally_connectable",
    "homepage_url",
    "host_permissions",
    "import",
    "incognito",
    "key",
    "minimum_chrome_version",
    "oauth2",
    "offline_enabled",
    "omnibox",
    "optional_host_permissions",
    "optional_permissions",
    "options_page",
    "options_ui",
    "permissions",
    "requirements",
    "sandbox",
    "short_name",
    "side_panel",
    "storage",
    "tts_engine",
    "update_url",
    "version_name",
    "web_accessible_resources",
}

# ---------------------------------------------------------------------------
# Valid MV3 permissions
# https://developer.chrome.com/docs/extensions/reference/permissions-list
# ---------------------------------------------------------------------------

VALID_MV3_PERMISSIONS = {
    "activeTab",
    "alarms",
    "audio",
    "background",
    "bookmarks",
    "browsingData",
    "certificateProvider",
    "clipboardRead",
    "clipboardWrite",
    "contentSettings",
    "contextMenus",
    "cookies",
    "debugger",
    "declarativeContent",
    "declarativeNetRequest",
    "declarativeNetRequestFeedback",
    "declarativeNetRequestWithHostAccess",
    "dns",
    "documentScan",
    "downloads",
    "downloads.open",
    "downloads.ui",
    "enterprise.deviceAttributes",
    "enterprise.hardwarePlatform",
    "enterprise.networkingAttributes",
    "enterprise.platformKeys",
    "favicon",
    "fileBrowserHandler",
    "fileSystemProvider",
    "fontSettings",
    "gcm",
    "geolocation",
    "history",
    "identity",
    "identity.email",
    "idle",
    "loginState",
    "management",
    "nativeMessaging",
    "notifications",
    "offscreen",
    "pageCapture",
    "platformKeys",
    "power",
    "printerProvider",
    "printing",
    "printingMetrics",
    "privacy",
    "processes",
    "proxy",
    "readingList",
    "runtime",
    "scripting",
    "search",
    "sessions",
    "sidePanel",
    "storage",
    "system.cpu",
    "system.display",
    "system.memory",
    "system.storage",
    "tabCapture",
    "tabGroups",
    "tabs",
    "topSites",
    "tts",
    "ttsEngine",
    "unlimitedStorage",
    "vpnProvider",
    "wallpaper",
    "webAuthenticationProxy",
    "webNavigation",
    "webRequest",
    "webRequestAuthProvider",
}

# ---------------------------------------------------------------------------
# Deprecated / removed APIs to flag in MV3
# ---------------------------------------------------------------------------

MV3_DEPRECATED_PATTERNS: list[tuple[str, str, str]] = [
    # (regex_pattern, short_name, suggestion)
    (
        r"chrome\.browserAction\b",
        "chrome.browserAction",
        "Removed in MV3. Use 'chrome.action' instead.",
    ),
    (
        r"chrome\.pageAction\b",
        "chrome.pageAction",
        "Removed in MV3. Use 'chrome.action' instead.",
    ),
    (
        r"chrome\.extension\.getURL\b",
        "chrome.extension.getURL",
        "Deprecated. Use 'chrome.runtime.getURL' instead.",
    ),
    (
        r"chrome\.extension\.sendMessage\b",
        "chrome.extension.sendMessage",
        "Removed. Use 'chrome.runtime.sendMessage' instead.",
    ),
    (
        r"chrome\.extension\.onMessage\b",
        "chrome.extension.onMessage",
        "Removed. Use 'chrome.runtime.onMessage' instead.",
    ),
    (
        r"chrome\.tabs\.executeScript\b",
        "chrome.tabs.executeScript",
        "Removed in MV3. Use 'chrome.scripting.executeScript' instead.",
    ),
    (
        r"chrome\.tabs\.insertCSS\b",
        "chrome.tabs.insertCSS",
        "Removed in MV3. Use 'chrome.scripting.insertCSS' instead.",
    ),
    (
        r"\bXMLHttpRequest\b",
        "XMLHttpRequest",
        "Not available in MV3 service workers. Use 'fetch' instead.",
    ),
    (
        r"\blocalStorage\b",
        "localStorage",
        "Not available in MV3 service workers. Use 'chrome.storage.local' instead.",
    ),
]


def _issue(level: str, category: str, message: str) -> dict:
    return {"level": level, "category": category, "message": message}


# ---------------------------------------------------------------------------
# Layer 1: Manifest validation
# ---------------------------------------------------------------------------


def _validate_manifest(project_dir: Path) -> tuple[list[dict], dict | None]:
    """Validate manifest.json structure and return (issues, parsed_manifest)."""
    issues: list[dict] = []
    manifest_path = project_dir / "manifest.json"

    if not manifest_path.exists():
        issues.append(_issue("error", "manifest", "manifest.json not found in project root."))
        return issues, None

    raw = manifest_path.read_text(encoding="utf-8", errors="replace")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        issues.append(_issue("error", "manifest", f"manifest.json is not valid JSON: {exc}"))
        return issues, None

    if not isinstance(manifest, dict):
        issues.append(_issue("error", "manifest", "manifest.json root must be a JSON object."))
        return issues, None

    # Required fields
    for field in ("manifest_version", "name", "version"):
        if field not in manifest:
            issues.append(_issue("error", "manifest", f"Missing required field: '{field}'"))

    # manifest_version must be 3
    mv = manifest.get("manifest_version")
    if mv is not None and mv != 3:
        issues.append(
            _issue("error", "manifest", f"manifest_version is {mv}, expected 3 for Manifest V3.")
        )

    # Check for unrecognised top-level keys
    for key in manifest:
        if key not in VALID_MV3_MANIFEST_KEYS:
            issues.append(
                _issue("warning", "manifest", f"Unrecognised manifest key: '{key}' (possible typo?)")
            )

    # Validate permissions
    for perm in manifest.get("permissions", []):
        if perm not in VALID_MV3_PERMISSIONS:
            issues.append(
                _issue("warning", "manifest", f"Unknown permission: '{perm}' — may not be a valid MV3 permission.")
            )

    return issues, manifest


# ---------------------------------------------------------------------------
# Layer 2: File reference checking
# ---------------------------------------------------------------------------


def _collect_referenced_files(manifest: dict) -> list[str]:
    """Extract all file paths referenced in the manifest."""
    files: list[str] = []

    # background.service_worker
    bg = manifest.get("background", {})
    if isinstance(bg, dict) and "service_worker" in bg:
        files.append(bg["service_worker"])

    # content_scripts[].js and content_scripts[].css
    for cs in manifest.get("content_scripts", []):
        files.extend(cs.get("js", []))
        files.extend(cs.get("css", []))

    # action icons and popup
    action = manifest.get("action", {})
    if isinstance(action, dict):
        popup = action.get("default_popup")
        if popup:
            files.append(popup)
        icon = action.get("default_icon")
        if isinstance(icon, str):
            files.append(icon)
        elif isinstance(icon, dict):
            files.extend(icon.values())

    # icons
    icons = manifest.get("icons", {})
    if isinstance(icons, dict):
        files.extend(icons.values())

    # options_page / options_ui
    options_page = manifest.get("options_page")
    if options_page:
        files.append(options_page)
    options_ui = manifest.get("options_ui", {})
    if isinstance(options_ui, dict) and "page" in options_ui:
        files.append(options_ui["page"])

    # devtools_page
    devtools = manifest.get("devtools_page")
    if devtools:
        files.append(devtools)

    # side_panel
    side_panel = manifest.get("side_panel", {})
    if isinstance(side_panel, dict) and "default_path" in side_panel:
        files.append(side_panel["default_path"])

    # web_accessible_resources
    for war in manifest.get("web_accessible_resources", []):
        if isinstance(war, dict):
            files.extend(war.get("resources", []))
        elif isinstance(war, str):
            files.append(war)

    return files


def _validate_file_references(project_dir: Path, manifest: dict) -> list[dict]:
    """Check that every file referenced in the manifest exists on disk."""
    issues: list[dict] = []
    referenced = _collect_referenced_files(manifest)

    for rel_path in referenced:
        full_path = project_dir / rel_path
        if not full_path.exists():
            issues.append(
                _issue(
                    "error",
                    "file_reference",
                    f"Referenced file not found: '{rel_path}'",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Layer 3: JavaScript syntax checking
# ---------------------------------------------------------------------------


def _validate_js_syntax(project_dir: Path, manifest: dict) -> list[dict]:
    """Run `node --check` on every .js file referenced in the manifest."""
    issues: list[dict] = []
    referenced = _collect_referenced_files(manifest)

    js_files = [f for f in referenced if f.endswith(".js")]

    for rel_path in js_files:
        full_path = project_dir / rel_path
        if not full_path.exists():
            continue  # Already caught by file reference check

        try:
            result = subprocess.run(
                ["node", "--check", str(full_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                err = result.stderr.strip()
                # Shorten absolute paths to relative for readability
                err = err.replace(str(project_dir) + "/", "")
                issues.append(
                    _issue("error", "syntax", f"Syntax error in '{rel_path}': {err}")
                )
        except FileNotFoundError:
            issues.append(
                _issue(
                    "warning",
                    "syntax",
                    "Could not run syntax check — Node.js is not installed or not in PATH.",
                )
            )
            break  # No point trying more files
        except subprocess.TimeoutExpired:
            issues.append(
                _issue("warning", "syntax", f"Syntax check timed out for '{rel_path}'.")
            )

    return issues


# ---------------------------------------------------------------------------
# Layer 4: MV3 compatibility scanning
# ---------------------------------------------------------------------------


def _validate_mv3_compat(project_dir: Path, manifest: dict) -> list[dict]:
    """Scan JS files for use of removed/changed Chrome APIs."""
    issues: list[dict] = []
    referenced = _collect_referenced_files(manifest)
    js_files = [f for f in referenced if f.endswith(".js")]

    # Identify which file is the service worker (extra rules apply there)
    bg = manifest.get("background", {})
    service_worker = bg.get("service_worker") if isinstance(bg, dict) else None

    for rel_path in js_files:
        full_path = project_dir / rel_path
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()
        for line_num, line in enumerate(lines, 1):
            # Skip comment lines (basic heuristic)
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for pattern, name, suggestion in MV3_DEPRECATED_PATTERNS:
                # XMLHttpRequest and localStorage warnings only apply in service workers
                if name in ("XMLHttpRequest", "localStorage") and rel_path != service_worker:
                    continue

                if re.search(pattern, line):
                    issues.append(
                        _issue(
                            "warning",
                            "mv3_compat",
                            f"{rel_path}:{line_num} — '{name}': {suggestion}",
                        )
                    )

    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_extension(project_dir: Path) -> list[dict]:
    """Run all validation layers on a Chrome extension project.

    Args:
        project_dir: Path to the extension project root (must contain manifest.json).

    Returns:
        A list of issue dicts, each with keys: level ("error" | "warning"),
        category ("manifest" | "file_reference" | "syntax" | "mv3_compat"),
        and message (human-readable description).
    """
    all_issues: list[dict] = []

    # Layer 1: Manifest validation
    manifest_issues, manifest = _validate_manifest(project_dir)
    all_issues.extend(manifest_issues)

    if manifest is None:
        # Can't continue without a valid manifest
        return all_issues

    # Layer 2: File reference checking
    all_issues.extend(_validate_file_references(project_dir, manifest))

    # Layer 3: JS syntax checking
    all_issues.extend(_validate_js_syntax(project_dir, manifest))

    # Layer 4: MV3 compatibility
    all_issues.extend(_validate_mv3_compat(project_dir, manifest))

    return all_issues
