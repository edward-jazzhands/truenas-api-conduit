# Stage 1: builder
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

# Optimize Python bytecode compilation and file tracking
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (leveraging Docker layer caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Add your actual application source code and build it
ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: runtime
FROM python:3.14-alpine

WORKDIR /app

# Pull the compiled virtual environment from the builder stage
COPY --from=builder /app /app

# Prepend the virtual environment to the system PATH
ENV PATH="/app/.venv/bin:$PATH"

# Expose your local proxy port
EXPOSE 4567

# Run your standalone daemon directly
ENTRYPOINT ["truenas-api-conduitd"]