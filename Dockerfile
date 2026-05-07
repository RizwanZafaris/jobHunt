FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (for portal scanning)
RUN playwright install chromium --with-deps || true

# Copy application code
COPY . .

# Create output directories
RUN mkdir -p output/resumes output/reports output/interview_prep data

# Expose port (Railway sets PORT env var)
EXPOSE 8000

# Default: start API server
# Override with START_MODE=scheduler for the cron daemon
CMD ["python", "main.py", "--api"]
