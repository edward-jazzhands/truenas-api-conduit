from truenas_api_conduit.constants import COLORS, ENV, CRYPT_FILE_NAME, CRYPT_KEY_PATH
from truenas_api_conduit.i18n import _  # gettext function

main_help = """TrueNAS API Conduit - A websocket proxy service for the TrueNAS API.\n
This will hold the websocket connection open so that subsequent requests can
re-use the same connection. It can be installed as a service, or run as a
standalone program without installing.\n
Most of the commands have more info in their respective help menus"""

# options

verbose_help = f"""Sets the verbosity level for the CLI. Note this does not
affect the conduit service.
[{COLORS.option}]-v[/{COLORS.option}] for info,
[{COLORS.option}]-vv[/{COLORS.option}] for debug,
[{COLORS.option}]-vvv[/{COLORS.option}] for tracebacks"""

no_color_help = f"""Disables color output. You must set the environment variable
to disable color in the help menu [env: [{COLORS.envvar}]{ENV['no_color']}[default]=]"""

pretty_help = f"""Format the JSON response to be human-readable. Alternatively
you can pipe the response into
[{COLORS.command}]jq[default] (can be faster)"""

# Start

start_help_short = """Start the conduit service, either through your OS service 
manager (if installed) or in standalone mode"""

start_help = f"""Start the conduit service.\n
\n
You can also start the program directly as a standalone program without installing
by using the [{COLORS.option}]--standalone[/{COLORS.option}] option, which
runs in the foreground by default.\n
\n
Tip: to run standalone in the background, use:\n
\n
(Mac + Linux):  [{COLORS.command}]truenas-api start --standalone & disown[default]\n
(Windows):      [{COLORS.command}]Start-Process truenas-api start
--standalone[default]\n
\n
Some of the options for this command can only be used with standalone
mode. To set them for installed (OS service) mode, use one of the other
config methods (config file, env vars, set-key command)"""

standalone_help = """Start the service as a standalone program in the foreground (not
run by your service manager). Does not require installation"""

locked_help = f"""Start the service in locked mode. This can only be used if
you've set your API key using the [{COLORS.command}]set-key[default] command.
It will delay retrieving the API key until the unlock password has been
provided  [{COLORS.envvar}]{ENV['start_locked']}[default]=]"""

api_key_help = f"""(Only with [{COLORS.command}]--standalone[default])
Ask to be prompted for your TrueNAS API key
[env: [{COLORS.envvar}]{ENV['api_key']}[default]=]"""

truenas_address_help = f"""(Only with [{COLORS.command}]--standalone[default])
The address that you use to access the TrueNAS Web UI over
HTTPS [env: [{COLORS.envvar}]{ENV['truenas_address']}[default]=]"""

conduit_host_help = f"""(Only with [{COLORS.command}]--standalone[default])
The address for the TrueNAS API Conduit service. This will be
[{COLORS.envvar}]localhost:4567[default] by default
[env: [{COLORS.envvar}]{ENV['conduit_host']}[default]=]
"""

validate_certs_help = f"""(Only with [{COLORS.command}]--standalone[default])
Whether to require the TrueNAS TLS certificate to be valid
[env: [{COLORS.envvar}]{ENV['validate_certs']}[default]=]"""

log_level_help = f"""Set the logging level for the service.
[env: [{COLORS.envvar}]{ENV['log_level']}[default]=]"""


# Install
install_help_short = """Install the TrueNAS API Conduit service"""

install_help = """Install the TrueNAS API Conduit service.\n
On Linux and MacOS, this will install as a user service and does not
require elevation. On Windows, elevation is required to install

On Linux: registers the program with systemd
On MacOS: registers the program with launchd
On Windows: registers the program with the Windows Service Manager
"""

# Uninstall
uninstall_help = """Uninstall the conduit service"""


# Request

request_help_short = "Make a request using the service. The service must be running"


param_ex = """truenas-api request reporting.get_data --params '[{"name": "cpu"}]'"""

request_help = f"""Make a request using the service. The service must be running.\n
Example: [{COLORS.command}]truenas-api request system.info[default]\n
\n
You can also pipe the response into jq to filter and format the results:\n
[{COLORS.command}]truenas-api request disk.query | jq[default]\n
\n
Example of using the --params option (most TrueNAS API methods
can accept parameters to filter the results):\n
[{COLORS.command}]{param_ex}[default]\n
\n
The --filter option is a shortcut for passing in filter triplet arrays
to the --params option. Each -f flag (stackable) is equivalent to passing in a
single filter triplet. For example:\n
[{COLORS.command}]truenas-api request app.query -f name = 'dockge'[default]\n
\n
Use the [{COLORS.command}]cheatsheet[default] command to see a bigger list
of some common requests and usage examples.\n
Use the [{COLORS.command}]reference[default] command to print the URL to the API
reference on your server for the full list of everything you can request.\n
\n
Note: this program has no knowledge of what methods are available, it just
forwards the request to the TrueNAS API and returns the JSON response verbatim.
This will also return any TrueNAS errors to you if the request worked
but you've requested something invalid."""

filters_help = f"""Add a filter to the request. Filters are in the form of
'filter triplets' as defined by the TrueNAS API. Triplet format is
[{COLORS.envvar}]FIELD OPERATOR VALUE[default]. For example:
[{COLORS.option}]--filter name = sda[/{COLORS.option}]
"""

# Stop

stop_help = """Stop the conduit service. This will detect if its running
as an OS service or in standalone mode and send the stop request accordingly"""

stop_direct_help = """Force the stop request to go directly to the service,
bypassing the OS service manager (only relevant if installed, standalone
mode does this automatically)"""

# Restart

restart_help_short = """Restart the conduit service. This will detect if its running
as an OS service or in standalone mode and send the restart request accordingly"""

restart_help = """Restart the conduit service. This will detect if its running
as an OS service or in standalone mode and send the restart request accordingly."""

direct_help = """Send the restart request directly to the service, bypassing the
OS service manager (only relevant if installed, standalone mode does this
automatically)"""

hot_restart_help = f"""Perform a hot restart (implies --direct). This will NOT
reload the configuration from any sources, and instead will restart the service with
the current config. This is useful if you passed in some options using the
[{COLORS.command}]start[default] command, and you want them to persist, or if
you don't want to enter your unlock password again"""

# Lock

lock_help_short = """Lock the service"""

lock_help = f"""Lock the service. Once locked, you'll be required to use your
API key password to unlock the service.\n
\n
This is only possible if you've set your API key using the
[{COLORS.command}]set-key[default] command, which will store your TrueNAS API
key in your OS secret manager or fall back to using the built-in file encryption
if that is not available.\n
\n
If you've set your API key using the [{COLORS.command}]set-key[default] command,
and the service cannot get the password automatically (ie. you don't have it
stored on the server for security reasons), the service will start in locked mode
automatically.\n

See the [{COLORS.command}]set-key[default] command for more info on how to set
your API key password.\n
"""

# Unlock

unlock_help_short = """Unlock the service"""

unlock_help = """Unlock the service. See the [{COLORS.command}]lock[default]
command for more info on how to lock the service."""

# Status

status_help_short = """Check the status/ping of the conduit service"""

status_help = """Check the status/ping of the conduit service.
This can query the service directly, or ask your operating system (if installed).\n
This returns the response in JSON (if not using the --system option)."""

system_status_help = "View the OS service manager's status output, if installed"

# Logs


logs_helps_short = """Read the system logs for the service (must be installed)"""

# HACK: These help menus might be OS specific. I'll probably need to adjust
# the wording to make it applicable to Mac and Windows.

logs_help = f"""Read the system logs for the service (must be installed).\n
You can pipe this into a log viewer (such as 'lnav' or 'moor') to view the logs
in real time (ie.: [{COLORS.command}]truenas-api logs -f | lnav[default]).\n
Note that -f opens the system logger directly and will not have any color or
search capabilities. Recommended to install a proper log viewer TUI such as
`lnav` or `moor`"""

follow_help = """Follow/tail the log output (Note: This just runs the system logger
directly, which is why it can be piped, but it has no color by itself)"""

limit_help = "The number of logs to print. Exclusive with --follow"


# Set Key

set_key_help_short = """Set the API key using whatever compatible keyring/secrets manager
is available on your system"""

set_key_help = f"""Set the API key using whatever compatible keyring/secrets manager
is available on your system.\n
\n
If there is no keyring backend available (ie. you're running in some minimal or
headless environment), the program will fall back to writing the API key to an
encrypted file in your storage directory, and you'll be prompted to set the
encryption password (a.k.a the "crypt key").\n
\n
You can also force the program to use the file encrypter backend by using the
[{COLORS.option}]--encrypted[/] option.\n
\n
If you've created this encrypted file, the program will then look for the crypt key
in the [{COLORS.envvar}]{ENV['crypt_key']}[default] environment variable,
or in a file named [{COLORS.envvar}]{CRYPT_FILE_NAME}[default] in your config
directory. (On your system, this is: {CRYPT_KEY_PATH})
[env: [{COLORS.envvar}]{ENV['crypt_key']}[default]=]\n
\n
If available, it will use that as the encryption key to avoid prompting you (thus
making it possible to start the service automatically/at boot).\n
\n
If the encrypted file exists and this crypt key can't be found by the program
(which you might want to avoid for security reasons), the program will
automatically launch in locked mode
"""

delete_help = "Delete the API key from the current keyring backend"

show_help = """Show the API key in the current keyring backend"""

del_crypt_help = "Delete the stored encryption key file, if it exists"

encrypted_help = """Force the program to use the file encrypter backend and
not try to use any OS keyring/secrets manager you might have available"""

# Config

config_help = f"""Attempts to open the config file in your editor, if
[env: [{COLORS.envvar}]{ENV['editor']}[default]=] is set. Contains options to print
the path to the config file, or print the config to stdout, etc."""

print_path_help = "Print the path to the config file (you can pipe this)"

print_config_help = f"""Validate and output your current configuration as JSON to
stdout. If you combine the [{COLORS.command}]--unmask[default] option, then the
generated JSON may be piped to truenas-api-conduitd via stdin.
"""

unmask_help = f"""(Used with [{COLORS.command}]--print-config[default]) reveals the
API key in the JSON output. May trigger a password prompt"""

# Cheatsheet
cheatsheet_help = (
    """Print a cheatsheet showing how to do a bunch of commmon API requests"""
)

# Reference
reference_help = """Print the URL to the TrueNAS API reference on your server
(requires your config to be set up)"""

# Version
version_help = """Print the version of the TrueNAS API Conduit service"""

# Completions
completions_help = """Print the commands to enable tab completions in your shell
(you can eval this)"""


# Env
env_help = """Print out a list of all environment variables which can be used by
the service, and their current values"""
