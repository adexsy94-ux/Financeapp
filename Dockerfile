# Dockerfile for VoucherPro Streamlit app

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed by psycopg2 and reportlab
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libfreetype6-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better layer caching)
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . /app

# Streamlit config: disable browser auto-open
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ENABLECORS=false

# Expose Streamlit port
EXPOSE 8501

# Entry point
CMD ["streamlit", "run", "app_main.py", "--server.port=8501", "--server.address=0.0.0.0"]
