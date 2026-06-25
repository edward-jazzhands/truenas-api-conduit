[ ] Add support for having more than one TrueNAS API key (Big project, probably a 2.0 thing)
[X] Get service install working on Linux
[ ] Get service install working on Mac
[ ] Get service install working on Windows
[X] Write first version of dockerfile for the containerized service
[ ] Upload/release to PyPI and Github
[ ] Upload Docker image to Docker Hub
[ ] Upload to Homebrew
[ ] Create a .deb package (Debian)
[ ] Create a .rpm package (Fedora)
[ ] Create a .pkg.tar.zst package (Arch)
[ ] Create submission for TrueNAS App Marketplace
[ ] Fix all mypy & pyright errors
[X] Replace requests lib with aiohttp client
[?] Integrate systemd-python for better service integration in the service
[ ] Write unit tests for everything
[?] Add support for docker Secrets  *need to test*
[X] Separate out all the main menu help text into a separate file
[X] Finish process of moving the settings.toml file to exterior assets
[ ] Locking feature should only work if the password was set using the set-key command (through keyring)
[ ] Add option to force the set-key command to use the file encrypter backend
[ ] Add a log level setter command
[X] Move CLI error handling to a top level error handler with custom exceptions
[X] Convert CLI to use console_stderr directly instead of logging and remove all logging calls from CLI
[X] Change the -v/--verbose option to only affect the CLI and not the service, likewise make the log level not affect the CLI
[ ] Add the i18n function to all print and logging statements, update all to use lazy/deferred keyword string formatting
[?] Add a theme setter command
[ ] Get all commands working with docker version
[X] Implement the "start locked" feature
[X] Integrate start locked option into the CLI start command
[ ] Implement start locked detection based on presence of crypt key
[X] Add an env command to the CLI to see all env vars and which are currently set
[ ] Combine service address and service port into a single field in the config
[ ] Add tracking of which versions of the TrueNAS API the current version is validated against (this should very rarely change)
[X] Improve the completions command to be more robust and show the correct shell
[X] write status options to separate/toggle OS status message passthrough
[X] Create logs command to print the system logs on the base class and services
[X] Improve log streaming and piping to less/lnav
[X] Roll config-path and print-config commands into the config command
[X] Ensure all relevant commands are compatible with jq and add jq usage examples to the CLI helps
[X] Add a 'features' section to the main Readme
[X] Add force override options to send stop/restart commands straight to the service instead of through the OS service manager
[X] Make logging change to stdout when running in the service
[X] Make prompt for encryption key retry on wrong key
[X] Add connection diagnostic to the TrueNAS websocket client
[X] Add optional request header requirement for the service
[X] Have the config command check if the config file exists before opening
[X] Combine salt file into corresponding vault file
[X] print-config command only asks for keyring pass if unmasking
[X] Build system to change the address the service listens on
[X] Separate concerns between the Keyring Source and the FileEncrypter backend, also make it more generally reusable
[X] Build system to store crypt key in a file and use it automatically
[X] Write keyring FileEncrypter backend
[X] Get keyring working with FileEncrypter set as fallback
[X] Set up tab completion for commands and options in the CLI
[X] CLI reads from lock file where appropriate
[X] Status check should robustly test if service is running - use os.kill(pid, 0) and get pid from the lock file
[X] Deal with problem where lock file is not found
[X] Build ways to restart and stop the service
[X] Get params working properly in request command
[X] Build nicer CLI options for filter params on request command
[X] Write a README.md
[X] Create a cheatsheet command in the CLI
[X] Create an API reference command in the CLI
[X] Add ping to status check
[X] Add --pretty option to request commands
[X] Add jq usage examples to cheatsheet