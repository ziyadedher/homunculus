FROM python:3.14-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml .
RUN uv pip install --system .

# Copy application code
COPY src/ src/
COPY config/ config/

# Install the package
RUN uv pip install --system --no-deps .

EXPOSE 8080

CMD ["python", "-m", "homunculus"]
