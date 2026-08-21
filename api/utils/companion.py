"""Companion module for OS-level automation of Chrome extension loading.

On macOS, uses AppleScript via `osascript` to drive Chrome's "Load unpacked"
flow through the native file dialog.  Requires two user-granted permissions:

  1. Chrome > View > Developer > Allow JavaScript from Apple Events
  2. System Settings > Privacy & Security > Accessibility (for the terminal
     app running the backend)

Falls back gracefully on unsupported platforms.
"""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step 1: Check prerequisites
# ---------------------------------------------------------------------------

_CHECK_ACCESSIBILITY_SCRIPT = """\
tell application "System Events"
    tell process "Google Chrome"
        try
            set winCount to count of windows
            return winCount as text
        on error
            return "0"
        end try
    end tell
end tell
"""

_CHECK_JS_ENABLED_SCRIPT = """\
tell application "Google Chrome"
    try
        set result to execute front window's active tab javascript "1+1"
        return "ok"
    on error errMsg
        return errMsg
    end try
end tell
"""

# ---------------------------------------------------------------------------
# Step 2: Click "Load unpacked" via JavaScript through the Shadow DOM
# ---------------------------------------------------------------------------

# Passed as argv item 2 to avoid AppleScript double-quote issues.
_JS_CLICK_LOAD_UNPACKED = r"""
(function() {
    try {
        var mgr = document.querySelector('extensions-manager');
        if (!mgr || !mgr.shadowRoot) return 'error:no_manager';
        var toolbar = mgr.shadowRoot.querySelector('extensions-toolbar');
        if (!toolbar || !toolbar.shadowRoot) return 'error:no_toolbar';

        var toggle = toolbar.shadowRoot.querySelector('#devMode');
        if (toggle && !toggle.hasAttribute('checked')) {
            toggle.click();
            return 'toggled_dev_mode';
        }

        var btn = toolbar.shadowRoot.querySelector('#loadUnpacked');
        if (!btn) return 'error:no_button';
        btn.click();
        return 'clicked';
    } catch(e) { return 'error:' + e.message; }
})()
""".strip()

# ---------------------------------------------------------------------------
# Step 3: Main automation script
# ---------------------------------------------------------------------------

_MAIN_SCRIPT = """\
on run argv
    set extPath to item 1 of argv
    set jsCode to item 2 of argv

    -- Remember the currently active app so we can restore focus afterwards
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
    end tell

    -- 1. Open chrome://extensions in a NEW tab (so we don't disrupt user's tab)
    tell application "Google Chrome"
        activate
        if (count of windows) is 0 then make new window
        set origIdx to active tab index of front window
        tell front window to make new tab with properties {URL:"chrome://extensions"}
    end tell

    delay 0.5

    -- 2. Click "Load unpacked" via JavaScript (navigates Shadow DOM)
    set dialogOpened to false
    tell application "Google Chrome"
        try
            set jsResult to execute front window's active tab javascript jsCode

            if jsResult is "toggled_dev_mode" then
                delay 0.4
                set jsResult to execute front window's active tab javascript jsCode
            end if

            if jsResult is "clicked" then
                set dialogOpened to true
            end if
        end try
    end tell

    if not dialogOpened then
        error "JavaScript could not click the Load unpacked button."
    end if

    -- 3. Navigate the native file dialog
    delay 0.5
    tell application "System Events"
        tell process "Google Chrome"
            keystroke "g" using {command down, shift down}
            delay 0.35
            keystroke extPath
            delay 0.15
            keystroke return
            delay 0.5
            keystroke return
        end tell
    end tell

    -- 4. Wait briefly for the extension to finish loading, then clean up
    delay 0.5
    try
        tell application "Google Chrome"
            tell front window
                delete active tab
                try
                    set active tab index to origIdx
                end try
            end tell
        end tell
    end try

    -- 5. Restore focus to the app the user was in before
    try
        tell application frontApp to activate
    end try
end run
"""


async def _run_osascript(*args: str, timeout: float = 15) -> tuple[int, str, str]:
    """Run osascript and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "osascript", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Timed out"
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
    )


async def load_extension_via_os(extension_path: str) -> dict:
    """Automate loading an unpacked extension into Chrome.

    Uses Chrome's AppleScript ``execute javascript`` to click through the
    Shadow DOM on ``chrome://extensions``, then System Events keystrokes to
    navigate the native file dialog.

    Returns ``{"success": True}`` or ``{"success": False, "error": "..."}``.
    """
    if sys.platform != "darwin":
        return {
            "success": False,
            "error": (
                "Automated extension loading is currently only supported on macOS. "
                "Please load the extension manually via chrome://extensions > Load unpacked."
            ),
        }

    # ---- Pre-flight check 1: Accessibility permission ----
    rc, out, err = await _run_osascript("-e", _CHECK_ACCESSIBILITY_SCRIPT)
    if rc != 0 or out == "0":
        # Check if it's an explicit access denial vs simply 0 windows
        if "not allowed assistive access" in err.lower() or "1002" in err:
            return {
                "success": False,
                "error": (
                    "Accessibility permission required. Go to System Settings > "
                    "Privacy & Security > Accessibility and enable the app "
                    "running the backend (e.g. Terminal, iTerm, or Cursor)."
                ),
            }
        return {
            "success": False,
            "error": (
                "Cannot access Chrome windows via System Events. "
                "Grant Accessibility permission to the app running the "
                "backend server: System Settings > Privacy & Security > "
                "Accessibility, then add and enable your terminal app "
                "(Terminal, iTerm, Cursor, etc.)."
            ),
        }

    # ---- Pre-flight check 2: JavaScript from Apple Events ----
    rc, out, err = await _run_osascript("-e", _CHECK_JS_ENABLED_SCRIPT)
    if rc != 0 or out != "ok":
        if "turned off" in (out + err).lower() or "apple events" in (out + err).lower():
            return {
                "success": False,
                "error": (
                    "Chrome's JavaScript from Apple Events is disabled. "
                    "Enable it in Chrome: View menu > Developer > "
                    "Allow JavaScript from Apple Events."
                ),
            }
        # JS is enabled but something else went wrong — continue anyway
        logger.warning("JS check returned unexpected result: %s / %s", out, err)

    # ---- Run the main automation ----
    try:
        rc, out, err = await _run_osascript(
            "-e", _MAIN_SCRIPT,
            extension_path,
            _JS_CLICK_LOAD_UNPACKED,
            timeout=30,
        )
    except Exception as exc:
        logger.exception("Unexpected error in load_extension_via_os")
        return {"success": False, "error": f"Unexpected error: {exc}"}

    if rc != 0:
        logger.warning("osascript failed (exit %d): %s", rc, err)
        return {
            "success": False,
            "error": f"Automation failed: {err}" if err else "Automation failed with an unknown error.",
        }

    logger.info("Extension loaded via OS automation: %s", extension_path)
    return {"success": True}
