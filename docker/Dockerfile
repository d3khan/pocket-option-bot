FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libsqlite3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and config
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/

# Create data directory and set permissions
RUN mkdir -p /app/data && chown -R 1000:1000 /app/data

# Expose port
EXPOSE 8000

# Run as non-root user
USER 1000

# Run the application
CMD ["uvicorn", "pocket_option_bot.main:app", "--host", "0.0.0.0", "--port", "8000"]