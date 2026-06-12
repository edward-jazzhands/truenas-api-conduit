[ ] Get service install working on Linux
[ ] Get service install working on Mac
[ ] Get service install working on Windows
[ ] Write first version of dockerfile for the containerized service
[ ] Add tracking of which versions of the TrueNAS API the current version is validated against (this should very rarely change)
[ ] Pass through args to the help command
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