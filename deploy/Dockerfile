FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY src/bot/ ./bot/

# Data directory for rule persistence
RUN mkdir -p data

# Run from the bot directory so relative imports work
WORKDIR /app/bot

CMD ["python", "main.py"]
