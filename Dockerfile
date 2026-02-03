# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies (needed for some python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create an empty banned_users.txt if it doesn't exist
RUN touch banned_users.txt

# Expose the port (Cloud Run defaults to 8080)
EXPOSE 8080

# Command to run the application
# This starts both the Flask server (port 8080) and the Discord Bot
CMD ["python", "wordle_bot.py"]
