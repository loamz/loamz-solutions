# syntax=docker/dockerfile:1.7
# loamz-solutions as a tool server for agents:  docker run -e SOLUTIONS_TOKEN=… -p 8765:8765 loamz-solutions serve
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim-bookworm
RUN useradd --system --uid 10001 --create-home app && mkdir -p /state && chown app:app /state
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 STATE_DIR=/state
USER app
WORKDIR /state
EXPOSE 8765
VOLUME /state
ENTRYPOINT ["loamz-solutions"]
CMD ["serve"]
