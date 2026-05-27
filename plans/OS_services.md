# Service Architecture Plan

## Platform Strategy

| Platform | Mechanism      | Elevation Required |
|----------|-------------------------|-----------|
| Linux    | systemd user unit       | No        |
| macOS    | launchd LaunchAgent     | No        |
| Windows  | pywin32 Windows Service | Yes (UAC) |

All three platforms get native, first-class service infrastructure with no shimming or approximations.

## Priorities

1. First we complete the Linux service entirely
2. MacOS will be started once Linux is complete
3. Windows will be done last / lowest priority.

---

## Project Structure

```
your_app/
├── core/                 # Pure business logic, zero service awareness
├── config/               # Pydantic-settings, user config module
├── service/              # This plan's concern
│   ├── __init__.py       
│   ├── base.py           # Abstract base class defining the service interface
│   ├── windows.py        # pywin32 implementation
│   ├── linux.py          # systemd user unit generator/installer
│   └── macos.py          # launchd plist generator/installer
├── __init__.py           
├── cli.py                # Click/Rich-Click commands including service subcommands
├── console.py            # Shared rich console(stderr) object
```

---

## Entry Points

Three distinct entry points in `pyproject.toml`:

- `truenas-api-conduit` — The main entry point, has the same name as the package so it works properly with pipx/uv/etc. Interactive CLI, Click/Rich-Click, user-facing
- `truenas-api` — An alias for `truenas-api-conduit`, literally the exact same thing but with a different name (for convenience)
- `truenas-api-conduitd` — headless entry point, what the service infrastructure actually calls

The separation exists because services (especially pywin32) need a clean entry point with no Rich/Click interactive machinery attached to it. It also makes PyInstaller tractable later since it needs a clear `__main__` target, and probably help with various other things.

---

## CLI Surface

```
truenas-api-conduit install
truenas-api-conduit uninstall
truenas-api-conduit start
truenas-api-conduit stop
truenas-api-conduit restart
truenas-api-conduit status
```

All commands route through `get_service_manager()` and call the abstract interface. The CLI layer has no platform-specific logic in it.

---

## Abstract Service Interface

`base.py` defines the contract every platform implements:

- `install()`
- `uninstall()`
- `start()`
- `stop()`
- `restart()`
- `status()`

A single `get_service_manager()` factory function resolves the correct implementation at runtime based on a `Platform` enum. `sys.platform` is checked once, this is handled by the core module when the program starts, which is used in the get_service_manager() factory function.

---

## Platform Implementation Details

### Linux — systemd user unit
- No sudo at any point
- Unit file written to `~/.config/systemd/user/`
- `StandardOutput=journal`, `StandardError=journal` — stdout/stderr captured by journald automatically
- `Restart=always` with a sane restart delay (will be config setting)
- Activated with `systemctl --user enable` and `systemctl --user start`
- Also need `loginctl enable-linger <username>` to ensure the service is started on boot without needing the user to log in

### macOS — launchd LaunchAgent
- No sudo at any point
- Plist written to `~/Library/LaunchAgents/`
- `StandardOutPath` and `StandardErrorPath` set explicitly in the plist — this is required, launchd does not capture stdout automatically the way journald does
- plist explicitly sets StandardErrorPath to a file in ~/Library/Logs/truenas-api-conduit/ or similar
- `KeepAlive` set to true for restart-on-crash behavior
- Activated with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yourapp.plist`(note: NOT `launchctl load`, that is deprecated)
-the corresponding teardown verb is bootout instead of unload, with the same signature: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.yourapp.plist`

### Windows — pywin32 Windows Service
- Windows requires admin/elevation to install
- Full Windows Service registered via pywin32
- UAC prompt triggered by re-launching the process with `ShellExecute` + `runas` verb before service registration — pywin32 does not handle elevation itself
- Proper `SvcDoRun` / `SvcStop` lifecycle
- on Windows, the pywin32 service setup configures stderr redirection to a file in %APPDATA%\truenas-api-conduit\.
- pywin32 is a Windows-only optional dependency, not a hard requirement for the package overall

**Windows specific problem**: Windows UAC + ShellExecute re-launch is trickier than it sounds. You need to re-launch and wait for the elevated process to finish, then check its exit code. The naive implementation just fires off the elevated process and returns, leaving the user with no feedback about whether the install actually succeeded. You'll want to pass `fWait=True` equivalent — via `subprocess` + `ctypes` or `win32api.WaitForSingleObject` on the process handle.

The cleanest approach is:
`win32api.ShellExecute` won't give you a waitable handle, so you'll want `ShellExecuteEx` instead — it returns a `PROCESS_INFORMATION`-like structure with a handle you can pass to `win32event.WaitForSingleObject`. That's the correct pattern for "launch elevated, block, check exit code."
---

## Configuration & Paths

The app has a user-facing config file. As such the config folder must be placed in a location where it will not be hidden to the average user.
On Linux we can follow the XDG Base Directory specification and place it in `~/.config/truenas-api-conduit/`.
But on Windows and MacOS, we will place it directly in ~/.truenas-api-conduit/.

- **Linux** — `~/.config/truenas-api-conduit/`
- **macOS** — `~/.truenas-api-conduit/`
- **Windows** — `~\.truenas-api-conduit\`

This is determined once by the core module on initialization, and then used by all other modules.

---

## Future Packaging Considerations

**Homebrew** — formula post-install calls `truenas-api-conduit service install`. Plist path must not be hardcoded since Homebrew controls whether it lands in LaunchAgents or LaunchDaemons. Accept the target directory as a parameter.

**Scoop** — manifest `post_install` script calls `truenas-api-conduit service install`. Works cleanly as long as service install logic lives entirely in the CLI rather than in the Scoop manifest itself.

**apt/dnf** — `install` should accept two extra options: `--system` and `--package`. `--system` installs to `/etc/systemd/system/` (the correct location for user-administered system services). `--package` is for Debian/RPM postinst scripts and installs to `/lib/systemd/system/`. Both modes require `systemctl daemon-reload` afterward, and both require sudo.

**PyInstaller** — the headless `truenas-api-conduit-service` entry point is the build target. pywin32 has known PyInstaller compatibility requirements; dedicated hooks will be needed at that stage.

---

## Invariants to Enforce

- `service install` on Linux and macOS must not require sudo unless the user selected `--system`.
- Windows install will always require elevation. This is acceptable given the target audience.
- Core business logic in `core/` has zero imports from `service/`. The boundary is strict.
- pywin32 is never imported on non-Windows platforms.