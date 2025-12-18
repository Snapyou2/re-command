FROM python:3.11-slim-bookworm

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    rsyslog \
    iputils-ping \
    curl \
    ca-certificates \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Directories for music library, temporary downloads, and streamrip/deemix config
RUN mkdir -p /app/music /app/temp_downloads /root/.config/streamrip /root/.config/deemix
# Create non-root user
RUN useradd -u 1000 -m appuser
# Copy the streamrip config file to the location streamrip expects for root user
COPY docker/streamrip_config.toml /root/.config/streamrip/config.toml

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    # Ensure certifi's certificates are part of the system's trusted CAs
    python -c "import certifi; import shutil; shutil.copyfile(certifi.where(), '/usr/local/share/ca-certificates/certifi.crt')" && \
    update-ca-certificates

# Copy the rest of the application code
COPY . .

# Copy the web UI
COPY web_ui /app/web_ui

# Expose the port for the Flask app
EXPOSE 5000

# Copy and make the entrypoint script executable
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command to run
CMD ["tail", "-f", "/dev/null"]
