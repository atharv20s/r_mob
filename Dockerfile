# Use a slim Python 3.13 image
FROM python:3.13-slim

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy and install requirements
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application source code
COPY . .

# Expose gateway port
EXPOSE 8000

# Health check — verify the gateway is responsive
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Start application using uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
