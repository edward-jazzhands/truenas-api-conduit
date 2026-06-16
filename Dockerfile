# * Multi-stage builder
FROM ghcr.io/astral-sh/uv:python3.14-bookworm AS builder

# UV_COMPILE_BYTECODE will cause UV to pre-compile all the bytecode files. This
# is not entirely required but it speeds up the program.
# UV_LINK_MODE will cause UV to copy in all dependencies into the image, instead
# of the default behavior which is to use symlinks. This i'm pretty sure is
# 100% required for the image to work.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# NOTE on the philosophy here: The first sync we do --no-install-project,
# and this downloads all the dependencies into the builder stage. Then we
# copy the project source into the builder, and run the full install. The
# dependencies are already downloaded, so that step is very fast.
# The point of doing this is by putting all the dependencies in their own
# earlier layer, we avoid making UV resolve the entire dependency graph
# every time we build the image. This *combines* with the --mount=cache
# system, so everything is pre-downloaded AND resolved/installed.
# This whole system makes image rebuilds MUCH faster, assuming the
# dependency graph hasn't changed.

# NOTE: the --mount flags are a BuildKit optimization

# --mount=type=cache,target=/root/.cache/uv:
# Reuses uv's package cache across Docker builds.

# sync --frozen --no-install-project
# --frozen: Require uv.lock to exactly match the project, do not update it.
# --no-install-project: Install only dependencies, not the current project itself.

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# this of course assumes there's a .dockerignore file in the project root:
COPY . .

# Dependencies are already installed from the previous layer, so this step
# is typically very fast.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# * Stage 2: runtime
FROM python:3.14-slim

# forces Python logs to stream directly to stdout/stderr
ENV PYTHONUNBUFFERED=1

# 1000:1000 is the standard first-user ID on Ubuntu/Debian/Arch. 
# it provides a fallback if a user doesn't pass UID/GID in compose.
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g 1000 -m appuser

WORKDIR /app

# Copy files and ensure our base 1000:1000 user owns them
COPY --from=builder --chown=1000:1000 /app /app

# This is REQUIRED for arbitrary UIDs:
# Force Python to resolve '~' to this path:
ENV HOME=/home/appuser

# add the virtual environment to the system PATH
ENV PATH="/app/.venv/bin:$PATH"

# Default to 1000:1000. The compose file will override this if needed.
USER 1000:1000

EXPOSE 4567

ENTRYPOINT ["truenas-api-conduitd"]