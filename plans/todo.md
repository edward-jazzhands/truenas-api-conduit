[X] CLI reads from lock file where appropriate
[X] Status check should robustly test if service is running - use os.kill(pid, 0) and get pid from the lock file
[X] Deal with problem where lock file is not found
[X] Build ways to restart and stop the service
[ ] Get service install working on Linux
[ ] Get service install working on Mac
[ ] Get service install working on Windows
[X] Get params working properly in request command
[X] Build nicer CLI options for filter params on request command
[ ] Write first version of dockerfile for the containerized service