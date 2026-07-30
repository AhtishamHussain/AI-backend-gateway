FROM python:3.12-slim

# Install uv directly into the container
COPY --from=astral-sh/uv:latest /uv /uvx /bin/

# Set working directory inside the container
WORKDIR /app

# Copy lockfiles and application files
COPY pyproject.toml uv.lock ./
COPY main.py ./

# Sync dependencies using uv for instant container installs
RUN uv sync --frozen --no-cache

# Expose the API gateway port
EXPOSE 8000

# Fire up the production server inside the container environment
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
