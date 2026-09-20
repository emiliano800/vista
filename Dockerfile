FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY synthetic_data ./synthetic_data
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini docker-entrypoint.sh ./
RUN uv sync --frozen --no-dev && useradd --create-home vista && chown -R vista:vista /app \
    && chmod +x docker-entrypoint.sh
USER vista
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
# No CMD: the entrypoint runs the API unless VISTA_ROLE or explicit arguments say otherwise.
