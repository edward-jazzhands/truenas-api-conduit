[X] CLI reads from lock file where appropriate
[X] Status check should robustly test if service is running - use os.kill(pid, 0) and get pid from the lock file
[ ] Build poller module for writing specific query results to an output file.
[ ] Deal with problem where lock file is not found
[X] Build ways to restart and stop the service