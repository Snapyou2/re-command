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
    librsvg2-bin \
    imagemagick \
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

# Generate PWA icons from SVG
RUN rsvg-convert -w 192 -h 192 /app/web_ui/assets/logo.svg -o /app/web_ui/assets/icon-192.png && \
    rsvg-convert -w 512 -h 512 /app/web_ui/assets/logo.svg -o /app/web_ui/assets/icon-512.png && \
    # Maskable icon: render logo at 80% size centered on background color for safe zone
    rsvg-convert -w 410 -h 410 /app/web_ui/assets/logo.svg -o /tmp/logo-410.png && \
    convert -size 512x512 xc:'#1a1a2e' /tmp/logo-410.png -gravity center -composite /app/web_ui/assets/icon-512-maskable.png && \
    # Generate multi-size favicon.ico
    rsvg-convert -w 16 -h 16 /app/web_ui/assets/logo.svg -o /tmp/icon-16.png && \
    rsvg-convert -w 32 -h 32 /app/web_ui/assets/logo.svg -o /tmp/icon-32.png && \
    rsvg-convert -w 48 -h 48 /app/web_ui/assets/logo.svg -o /tmp/icon-48.png && \
    convert /tmp/icon-16.png /tmp/icon-32.png /tmp/icon-48.png /app/web_ui/assets/favicon.ico && \
    rm -f /tmp/logo-410.png /tmp/icon-16.png /tmp/icon-32.png /tmp/icon-48.png

# Expose the port for the Flask app
EXPOSE 5000

# Copy and make the entrypoint script executable
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command to run
CMD ["tail", "-f", "/dev/null"]
