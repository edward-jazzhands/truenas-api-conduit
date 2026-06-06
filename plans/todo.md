[ ] Get service install working on Linux
[ ] Get service install working on Mac
[ ] Get service install working on Windows
[ ] Write first version of dockerfile for the containerized service
[ ] Make logging change to stdout when running in the service
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