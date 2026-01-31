# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port that the application listens on
EXPOSE 8080

# Ensure logs are sent to the console immediately
ENV PYTHONUNBUFFERED 1

# Start the application
CMD ["python", "wordle_bot.py"]
