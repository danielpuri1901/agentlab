# ARM64 (Graviton) worker image for AgentLab's cloud fabric.
#
# uv-in-Docker pattern verified against
# https://docs.astral.sh/uv/guides/integration/docker/ on 2026-08-16: copy the
# pinned uv binary from Astral's distroless image, sync dependencies before
# copying source (intermediate layer caching), then sync the full project.
# uv version pinned to 0.10.9 to match the version installed locally
# (`uv --version`), per the docs' "best practice to pin to a specific uv
# version."
#
# Stage layout:
#   base    - python:3.12-slim with the uv binary installed.
#   deps    - production dependencies only, cached independently of source.
#   builder - full project synced with --no-dev; this is what ships.
#   test    - builder plus dev deps, runs the test suite; nothing here ships.
#   final   - fresh python:3.12-slim with only the builder's --no-dev tree.
#
# `final` forces BuildKit to build and run the `test` stage (see the COPY
# --from=test line below) so `docker build` only succeeds if the test suite
# passes, without pulling the test stage's dev-dependency venv into the
# runtime image.

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM deps AS builder
COPY . .
RUN uv sync --frozen --no-dev

FROM builder AS test
RUN uv sync --frozen
RUN uv run pytest -q

FROM python:3.12-slim AS final
RUN groupadd --system --gid 999 agentlab \
    && useradd --system --gid 999 --uid 999 --create-home agentlab
WORKDIR /app
COPY --from=builder --chown=agentlab:agentlab /app /app
# Dependency edge only: forces the `test` stage (and its `uv run pytest -q`)
# to run as part of this build. The copied file is never read at runtime.
COPY --from=test /app/pyproject.toml /tmp/.pytest-gate
ENV PATH="/app/.venv/bin:$PATH"
USER agentlab
ENTRYPOINT ["agentlab"]
