FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY config.yaml.j2 .
COPY my_user_session.session .
COPY src/ ./src/

# Expose health check port
EXPOSE 8080

# Run the application
CMD ["python", "app.py"]
