# Install the package
install:
	uv sync

# Run with defined entry command
run:
  uv run truenas-api-conduit

# Runs ruff, exits with 0 if no issues are found
lint:
  @uv run ruff check src || (echo "Ruff found issues. Please address them." && exit 1)

# Runs mypy and basedpyright on src/, exits with 0 if no issues are found
typecheck:
  @uv run mypy src || (echo "Mypy found issues. Please address them." && exit 1)
  @uv run basedpyright src || (echo "BasedPyright found issues. Please address them." && exit 1)

# Runs typecheckers on tests/, exits with 0 if no issues are found
typecheck-tests:
  @uv run mypy tests || (echo "Mypy found issues in tests. Please address them." && exit 1)
  @uv run basedpyright tests || (echo "BasedPyright found issues in tests. Please address them." && exit 1)

# Runs black on src/
format:
  @uv run black src

# Runs pytest, exits with 0 if no issues are found
test:
  @uv run pytest tests -svvv

# Run the Nox testing suite for comprehensive testing
nox:
  nox

# NOTE: Nox must be installed as a global tool, it can't be installed
# as a dev dependency because it manages virtual environments.
# I install nox using UV (`uv tool install nox`). It looks for the noxfile.py.

# Remove build/dist directories and pyc files
clean:
  rm -rf build dist
  find . -name "*.pyc" -delete

# Remove tool caches
clean-caches:
  rm -rf .mypy_cache
  rm -rf .ruff_cache
  rm -rf .nox

# Remove the virtual environment and lock file
del-env:
  rm -rf .venv
  rm -rf uv.lock

# Delete environment, caches, and build artifacts
nuke: clean clean-caches del-env
  @echo "All build artifacts and caches have been removed."

# Runs nuke then install
reset: nuke install
  @echo "Environment reset."

# Syncs the tags from origin to local
sync-tags:
  git fetch --prune origin "+refs/tags/*:refs/tags/*"

# sync-ci:
#   curl -fsSL https://raw.githubusercontent.com/edward-jazzhands/ci-shared-python/main/sync.sh | bash

# uses the one in ./config as the source of truth
sync-configs:
  cp ./config/config.toml ./src/truenas_api_conduit/config.toml