# standard library
import sys
import logging
import os
import json
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from truenas_api_conduit.config.user_config import Config
    import tomllib

# third-party
from rich.panel import Panel
import rich_click as click
import psutil

# project
from truenas_api_conduit import APP_NAME, LOCK_FILE, log_setup
import truenas_api_conduit.core as core
from truenas_api_conduit.console import console_stderr

log = logging.getLogger(__name__)

__all__ = [
    "CLIOptions",
    "logging_setup",
    "config_setup",
    "RequestHelper",
    "get_request_helper",
]

@dataclass
class CLIOptions:
    """dataclass\n
    ```
    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    no_color: bool | None = None
    """

    api_key: str | None = None
    truenas_host: str | None = None
    verbose: int = 0
    no_color: bool | None = None



def logging_setup(ctx: click.RichContext) -> None:

    assert isinstance(ctx.obj, CLIOptions)

    nc_env = os.environ.get("NO_COLOR")
    if nc_env is not None or ctx.obj.no_color:
        console_stderr.no_color = True

    if ctx.obj.verbose > 1:
        console_stderr.print(ctx.obj)

    log_setup.init_logging()

    log_mapping = logging.getLevelNamesMapping()
    log_level: int = logging.getLogger().level  # starts at WARNING

    if ctx.obj.verbose > 0:
        if ctx.obj.verbose == 1:
            log_level = log_mapping["INFO"]  # 20
        elif ctx.obj.verbose == 2:
            log_level = log_mapping["DEBUG"]  # 10
        else:
            log_level = log_mapping["TRACE"]  # 5

    log_setup.set_log_level(log_level)


def config_setup(cli_options: CLIOptions) -> Config:

    log_level: int = logging.getLogger().level
    level_name = logging.getLevelName(log_level)
    log_mapping = logging.getLevelNamesMapping()

    if cli_options.api_key:
        log.debug("Prompting for API key")
        api_key = click.prompt("Enter your TrueNAS API key", hide_input=True)
    else:
        api_key = None

    # Creating an args dict because we only want to pass in the args that the user
    # passed in through the CLI. You can't pass None values to the Config class because
    # it would treat "None" as the desired value, instead of treating it as missing.
    to_filter: dict[str, Any] = {
        "log_level": level_name,
        "no_color": cli_options.no_color,
        "truenas_host": cli_options.truenas_host,
        "api_key": api_key,
    }
    args_dict = {k: v for k, v in to_filter.items() if v is not None}

    # NOTE: Remember that the config file/dir must be ensured before trying to
    # import the user_config module:
    core.ensure_config()  # Raises if failure

    # Pydantic will not be loaded until this following import. Its one
    # of the heavier dependencies so this improves startup time.
    from truenas_api_conduit.config import Config
    from pydantic import ValidationError  # .config already imports pydantic
    import tomllib

    try:
        cfg = Config(**args_dict)
    except ValidationError as e:
        errs = e.errors()
        err_string = "[default]The following errors were found in your configuration:"
        for err in errs:
            err_string += f"\n    [yellow]{err['loc'][0]}[/yellow] is {err['type']}:  "
            err_string += f"[bright_red]{err['msg']}"
        console_stderr.print(
            Panel(
                err_string,
                title="Configuration Errors",
                style="red",
                title_align="left",
            )
        )
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        toml_decoding_error_panel(e)
        sys.exit(1)
    except Exception as e:
        if log_level <= log_mapping["TRACE"]:
            raise
        elif log_level <= log_mapping["DEBUG"]:
            log.exception(
                f"Could not initialize config. Raise level to -vvv (trace) "
                "to see the full traceback."
            )
            sys.exit(1)
        else:
            err_string = (
                "[default]Could not initialize config:\n\n"
                f"    {e} ({e.__class__.__qualname__})\n\n"
                "Raise the verbosity to see more information."
            )
            console_stderr.print(Panel(err_string, style="red"))
            sys.exit(1)

    log.info("Config loaded successfully")
    log.info(cfg)
    provenance_str = "Config provenance:\n\n"
    for field, source in cfg.provenance.items():
        provenance_str += f"  {field}: {source}\n"
    log.debug(cfg.provenance)
    return cfg


def toml_decoding_error_panel(e: tomllib.TOMLDecodeError) -> None:

        err_string = (
            "[default]Your config file could not be parsed due to a TOML syntax error "
            f"at line {e.lineno}:\n\n"
        )
        doc_split = e.doc.splitlines()
        relevant_lines = doc_split[e.lineno - 3 : e.lineno + 2]

        for i, line in enumerate(relevant_lines):
            current_line = (e.lineno - 2) + i
            is_bad_line = False

            if current_line == e.lineno:
                is_bad_line = True
                err_string += f">>> "
            else:
                err_string += f"    "
            if current_line <= 9:
                err_string += " "

            err_string += f"{current_line} | "

            if line.strip().startswith("#"):
                err_string += f"[gray50]{line}[/gray50]\n"
            elif is_bad_line:
                err_string += f"[bright_yellow]{line}[/bright_yellow]\n"
            else:
                err_string += f"{line}\n"

        # Error help/suggestions

        bad_line = doc_split[e.lineno - 1]
        for word in ["True", "False"]:
            if word in bad_line:
                err_string += f"\nYou used '{word}' with a capital {word[0]}. "
                err_string += f"This must be lowercase like '{word.lower()}'.\n"
        if bad_line.count('"') == 1:
            err_string += f'\nOnly found one doublequote(") mark in the line. '
            err_string += f"Did you forget to close it?\n"
        if bad_line.count("'") == 1:
            err_string += f"\nOnly found one singlequote(') mark in the line. "
            err_string += f"Did you forget to close it?\n"
        if bad_line.count("'") == 0 and bad_line.count('"') == 0:
            err_string += "\nTip: does it need to be enclosed in quotes?\n"

        console_stderr.print(Panel(err_string, style="red"))


class RequestHelper:

    def __init__(
        self,
        port: int,
    ) -> None:
        self.port = port

    def __call__(
        self, endpoint: core.Endpoints, json_dict: dict[str, Any] | None = None
    ) -> dict[str, Any] | str:
        """no json = GET
        pass in json = POST"""

        if endpoint not in core.Endpoints:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        log.debug("Making request")

        import requests
        import yaspin
        from yaspin.spinners import Spinners

        try:
            with yaspin.yaspin(Spinners.bouncingBall, text="Sending request..."):
                if json_dict is not None:
                    response = requests.post(
                        f"http://127.0.0.1:{self.port}{endpoint}", json=json_dict
                    )
                else:
                    response = requests.get(f"http://127.0.0.1:{self.port}{endpoint}")
        except requests.exceptions.ConnectionError as e:
            log.error("Could not connect to TrueNAS API Conduit service")
            sys.exit(1)
        except Exception as e:
            log.error("Unexpected error making request: %s", e)
            sys.exit(1)

        try:
            return response.json()
        except json.JSONDecodeError as e:
            log.error("Malformed response: %s | Raw response: %s", e, response.text)
            return response.text


def auto_find_service_port() -> int | None:

    log.debug("Auto-finding service port")

    service_proc: psutil.Process | None = None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
            if APP_NAME in " ".join(cmdline):
                service_proc = proc
                break
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue

    if service_proc:
        log.debug("Found service with name: %s", service_proc.name())

        if port := get_process_port(service_proc):
            log.debug("Auto-found the service port: %s", port)
            return port
        else:
            log.critical("The process is running, but does not have a port open")
    else:
        return  # ~ This tells us the process is definitely not running


def get_process_by_pid(pid: int) -> psutil.Process | None:

    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return None


def get_process_port(proc: psutil.Process) -> int | None:

    try:
        connections = proc.net_connections()
        for conn in connections:
            if conn.status == "LISTEN":
                return conn.laddr.port
    except psutil.NoSuchProcess, psutil.AccessDenied:
        log.warning("Could not get process port: %s", proc)
        return None


def get_process_name(proc: psutil.Process) -> str | None:

    try:
        return proc.name()
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return None


def send_os_signal(pid: int, sig: int = 0) -> bool:

    try:
        os.kill(pid, sig)
        return True
    except PermissionError:
        # process exists, but we can't signal it, which is fine. probably
        # just because its owned by root or some other user. Should still work.
        return True
    except ProcessLookupError:
        log.warning("Could not signal service process with PID: %s", pid)
        return False
    except Exception as e:
        log.error("Unexpected error checking service status: %s", e)
        return False


def read_lockfile() -> dict[str, Any] | None:

    try:
        with open(LOCK_FILE, "r") as f:
            lock_dict = json.loads(f.read())
        assert isinstance(lock_dict, dict)
        assert isinstance(lock_dict["pid"], int)
        assert isinstance(lock_dict["socket_port"], int)
        return lock_dict
    except FileNotFoundError:
        log.info("Did not find a lock file")
        return
    except (json.JSONDecodeError, AssertionError) as e:
        log.error("Malformed lock file: %s", e)
        return
    except Exception as e:
        log.error("Unexpected error reading lock file: %s", e)
        return


def check_service_status() -> int | None:
    "If the service is up, return the port. If not, return None"

    # we check if we can pull everything from the lockfile first, its faster.
    if lock_dict := read_lockfile():
        log.debug("Found lockfile with PID: %s", lock_dict["pid"])

        if proc := get_process_by_pid(lock_dict["pid"]):
            # if we find a process with this PID, we can't immediately trust it,
            # we gotta make sure the PID was not recycled

            log.debug("Found process with PID: %s", lock_dict["pid"])

            # 1st check: does the port match the one in the lockfile
            if proc_port := get_process_port(proc):
                log.debug("Found process port: %s", proc_port)
                if proc_port == lock_dict["socket_port"]:
                    # If we look up the PID from the lockfile and the port on that process
                    # matches the port in the lockfile, it's definitely the right process.
                    log.debug("Process port matches lockfile, must be the right process")
                    return proc_port
                else:
                    log.warning(
                        "Process port (%s) does not match lockfile port (%s)",
                        proc_port,
                        lock_dict["socket_port"],
                    )
                    return auto_find_service_port()
            else:
                log.warning("Could not get process port. Trying next check...")

            # 2nd check: does the process name match the app name
            if proc_name := get_process_name(proc):  #      we got the name
                log.debug("Found process at PID %s with name %s", lock_dict["pid"], proc_name)
                if APP_NAME in proc_name:  #                ...and the name matches
                    log.debug("Name matches, checking signal")
                    if not send_os_signal(lock_dict["pid"]):
                        log.warning(
                            "Found process with PID %s, but could not signal it",
                            lock_dict["pid"],
                        )
                    return lock_dict["socket_port"]
                else:
                    log.warning(
                        "Found process with PID %s, but its name (%s) does not match. "
                        "This means the lock file is stale and the PID was recycled.",
                        lock_dict["pid"],
                        proc_name,
                    )
                    return auto_find_service_port()
            else:
                # process exists, but we can't get its name. Might be fine, might not.
                # this will probably vary depending on user permissions. This is tricky
                # cause it might be ok, so we don't want to trigger the full auto-find yet.
                log.info("Process name is not available for PID: %s", lock_dict["pid"])
                return lock_dict["socket_port"]
        else:
            # no service process found with this PID. Lock file must be stale
            log.warning("Lock file is stale: PID %s not found", lock_dict["pid"])
            return auto_find_service_port()
    else:
        # if the lockfile doesn't exist or is malformed, use the auto-finder
        return auto_find_service_port()


def get_request_helper() -> RequestHelper | None:
    "if service is up, returns a RequestHelper. If not, returns None"

    if service_port := check_service_status():
        return RequestHelper(service_port)
