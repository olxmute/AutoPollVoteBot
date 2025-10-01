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

# Set default port
ENV PORT=8080

# Expose health check port
EXPOSE ${PORT}

# Run the application
CMD ["python", "app.py"]
