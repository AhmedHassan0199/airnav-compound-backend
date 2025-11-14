# Use a slim Python image
FROM python:3.12-slim

# Install system dependencies required by WeasyPrint
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libpango-1.0-0 \
    libcairo2 \
    libffi-dev \
    libssl-dev \
    libjpeg62-turbo-dev \
    libpng-dev \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfribidi0 \
    && rm -rf /var/lib/apt/lists/*


# Set workdir
WORKDIR /app

# Copy requirements first (better for caching)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Render sets PORT env var
ENV PORT=8000

# Run gunicorn
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8000"]
