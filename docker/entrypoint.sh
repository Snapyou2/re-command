#!/bin/bash

export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Generate config.py from environment variables
echo "# Generated config.py from Docker environment variables" > config.py
echo "import os" >> config.py
echo "" >> config.py

# Navidrome Configuration
echo "ROOT_ND = os.getenv(\"ROOT_ND\", \"${RECOMMAND_ROOT_ND:-}\")" >> config.py
echo "USER_ND = os.getenv(\"USER_ND\", \"${RECOMMAND_USER_ND:-}\")" >> config.py
echo "PASSWORD_ND = os.getenv(\"PASSWORD_ND\", \"${RECOMMAND_PASSWORD_ND:-}\")" >> config.py
echo "MUSIC_LIBRARY_PATH = os.getenv(\"MUSIC_LIBRARY_PATH\", \"/app/music\")" >> config.py
echo "TEMP_DOWNLOAD_FOLDER = os.getenv(\"TEMP_DOWNLOAD_FOLDER\", \"/app/temp_downloads\")" >> config.py
echo "" >> config.py

# ListenBrainz API Configuration (Optional)
echo "LISTENBRAINZ_ENABLED = os.getenv(\"LISTENBRAINZ_ENABLED\", \"${RECOMMAND_LISTENBRAINZ_ENABLED:-False}\").lower() == \"true\"" >> config.py
echo "ROOT_LB = os.getenv(\"ROOT_LB\", \"${RECOMMAND_ROOT_LB:-https://api.listenbrainz.org}\")" >> config.py
echo "TOKEN_LB = os.getenv(\"TOKEN_LB\", \"${RECOMMAND_TOKEN_LB:-}\")" >> config.py
echo "USER_LB = os.getenv(\"USER_LB\", \"${RECOMMAND_USER_LB:-}\")" >> config.py
echo "" >> config.py

# Last.fm API Configuration (Optional)
echo "LASTFM_ENABLED = os.getenv(\"LASTFM_ENABLED\", \"${RECOMMAND_LASTFM_ENABLED:-False}\").lower() == \"true\"" >> config.py
echo "LASTFM_API_KEY = os.getenv(\"LASTFM_API_KEY\", \"${RECOMMAND_LASTFM_API_KEY:-}\")" >> config.py
echo "LASTFM_API_SECRET = os.getenv(\"LASTFM_API_SECRET\", \"${RECOMMAND_LASTFM_API_SECRET:-}\")" >> config.py
echo "LASTFM_USERNAME = os.getenv(\"LASTFM_USERNAME\", \"${RECOMMAND_LASTFM_USERNAME:-}\")" >> config.py
echo "LASTFM_PASSWORD_HASH = os.getenv(\"LASTFM_PASSWORD_HASH\", \"${RECOMMAND_LASTFM_PASSWORD_HASH:-}\")" >> config.py
echo "LASTFM_SESSION_KEY = os.getenv(\"LASTFM_SESSION_KEY\", \"${RECOMMAND_LASTFM_SESSION_KEY:-}\")" >> config.py
echo "" >> config.py

# LLM Suggestions Settings
echo "LLM_ENABLED = os.getenv(\"LLM_ENABLED\", \"${RECOMMAND_LLM_ENABLED:-false}\").lower() == \"true\"" >> config.py
echo "LLM_PROVIDER = os.getenv(\"LLM_PROVIDER\", \"${RECOMMAND_LLM_PROVIDER:-gemini}\")" >> config.py
echo "LLM_API_KEY = os.getenv(\"LLM_API_KEY\", \"${RECOMMAND_LLM_API_KEY:-}\")" >> config.py
echo "LLM_MODEL_NAME = os.getenv(\"LLM_MODEL_NAME\", \"${RECOMMAND_LLM_MODEL_NAME:-}\")" >> config.py
echo "LLM_TARGET_COMMENT = os.getenv(\"LLM_TARGET_COMMENT\", \"${RECOMMAND_LLM_TARGET_COMMENT:-llm_recommendation}\")" >> config.py
echo "" >> config.py

# Deezer Configuration (Optional - can be configured via web UI)
echo "DEEZER_ARL = os.getenv(\"DEEZER_ARL\", \"${RECOMMAND_DEEZER_ARL:-}\")" >> config.py
echo "" >> config.py

# Download Method (choose one)
echo "DOWNLOAD_METHOD = os.getenv(\"DOWNLOAD_METHOD\", \"${RECOMMAND_DOWNLOAD_METHOD:-streamrip}\")" >> config.py
echo "" >> config.py

# Album Recommendation Settings
echo "ALBUM_RECOMMENDATION_ENABLED = os.getenv(\"ALBUM_RECOMMENDATION_ENABLED\", \"${RECOMMAND_ALBUM_RECOMMENDATION_ENABLED:-false}\").lower() == \"true\"" >> config.py
echo "" >> config.py

# UI Visibility Settings
echo "HIDE_DOWNLOAD_FROM_LINK = os.getenv(\"HIDE_DOWNLOAD_FROM_LINK\", \"${RECOMMAND_HIDE_DOWNLOAD_FROM_LINK:-false}\").lower() == \"true\"" >> config.py
echo "HIDE_FRESH_RELEASES = os.getenv(\"HIDE_FRESH_RELEASES\", \"${RECOMMAND_HIDE_FRESH_RELEASES:-false}\").lower() == \"true\"" >> config.py
echo "" >> config.py

# Comment Tags for Playlist Creation
echo "TARGET_COMMENT = os.getenv(\"TARGET_COMMENT\", \"${RECOMMAND_TARGET_COMMENT:-lb_recommendation}\")" >> config.py
echo "LASTFM_TARGET_COMMENT = os.getenv(\"LASTFM_TARGET_COMMENT\", \"${RECOMMAND_LASTFM_TARGET_COMMENT:-lastfm_recommendation}\")" >> config.py
echo "ALBUM_RECOMMENDATION_COMMENT = os.getenv(\"ALBUM_RECOMMENDATION_COMMENT\", \"${RECOMMAND_ALBUM_RECOMMENDATION_COMMENT:-album_recommendation}\")" >> config.py
echo "" >> config.py

# History Tracking
echo "PLAYLIST_HISTORY_FILE = os.getenv(\"PLAYLIST_HISTORY_FILE\", \"/app/playlist_history.txt\")" >> config.py
echo "" >> config.py

# Caching for fresh releases (in seconds)
echo "FRESH_RELEASES_CACHE_DURATION = int(os.getenv(\"FRESH_RELEASES_CACHE_DURATION\", \"${RECOMMAND_FRESH_RELEASES_CACHE_DURATION:-300}\"))" >> config.py
echo "" >> config.py

# Deezer API Rate Limiting
echo "DEEZER_MAX_CONCURRENT_REQUESTS = int(os.getenv(\"DEEZER_MAX_CONCURRENT_REQUESTS\", \"${RECOMMAND_DEEZER_MAX_CONCURRENT_REQUESTS:-3}\"))" >> config.py
echo "" >> config.py

# Set up cron job
# Run every Tuesday at 00:00 (Usually guarantees that the LB playlist is released)
mkdir -p /app/logs
touch /app/logs/re-command.log
# Run cleanup first, then recommendations
echo "0 0 * * 2 root /usr/local/bin/python3 /app/re-command.py --cleanup >> /app/logs/re-command.log 2>&1 && /usr/local/bin/python3 /app/re-command.py >> /app/logs/re-command.log 2>&1" > /etc/cron.d/re-command-cron
chmod 0644 /etc/cron.d/re-command-cron

# Replace ARL placeholder in streamrip_config.toml
if [ -n "${RECOMMAND_DEEZER_ARL}" ]; then
    sed -i "s|arl = \"REPLACE_WITH_ARL\"|arl = \"${RECOMMAND_DEEZER_ARL}\"|" /root/.config/streamrip/config.toml
    # Create .arl file for deemix in /root/.config/deemix/
    echo "${RECOMMAND_DEEZER_ARL}" > /root/.config/deemix/.arl
fi

# Replace downloads folder in streamrip_config.toml
sed -i "s|folder = \"/home/ubuntu/StreamripDownloads\"|folder = \"/app/temp_downloads\"|" /root/.config/streamrip/config.toml

# Set Deezer quality to 0 (autoselect) in streamrip_config.toml
sed -i '/^\[deezer\]/,/^\[[a-z]*\]/ s/quality = [0-9]*/quality = 0/' /root/.config/streamrip/config.toml

# Deemix Configuration
DEEMIX_CONFIG_PATH="/root/.config/deemix/config.json"
if [ ! -f "$DEEMIX_CONFIG_PATH" ]; then
    echo "Creating default deemix config.json"
    mkdir -p "$(dirname "$DEEMIX_CONFIG_PATH")"
    echo '{"maxBitrate": "1"}' > "$DEEMIX_CONFIG_PATH"
else
    echo "Updating deemix config.json"
    # Use jq to update maxBitrate for free deezer accounts (remove this line if you have a premium account)
    jq '.maxBitrate = "1"' "$DEEMIX_CONFIG_PATH" > "$DEEMIX_CONFIG_PATH.tmp" && mv "$DEEMIX_CONFIG_PATH.tmp" "$DEEMIX_CONFIG_PATH"
fi

# Start syslog service (required for cron)
rsyslogd

# Give syslog a moment to start
sleep 2

# Start cron service
cron &

# Start Gunicorn server for the Flask app in the background
gunicorn --bind 0.0.0.0:5000 --timeout 300 "web_ui.app:app" &

# Execute the main command & keep container running
exec "$@"
