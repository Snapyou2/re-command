#!/bin/bash

# Generate config.py from environment variables
echo "# Generated config.py from Docker environment variables" > config.py

# Navidrome Configuration
echo "ROOT_ND = \"${RECOMMAND_ROOT_ND}\"" >> config.py
echo "USER_ND = \"${RECOMMAND_USER_ND}\"" >> config.py
echo "PASSWORD_ND = \"${RECOMMAND_PASSWORD_ND}\"" >> config.py
echo "MUSIC_LIBRARY_PATH = \"/app/music\"" >> config.py
echo "TEMP_DOWNLOAD_FOLDER = \"/app/temp_downloads\"" >> config.py

# ListenBrainz API Configuration (Optional)
echo "LISTENBRAINZ_ENABLED = ${RECOMMAND_LISTENBRAINZ_ENABLED:-False}" >> config.py
echo "ROOT_LB = \"${RECOMMAND_ROOT_LB:-https://api.listenbrainz.org}\"" >> config.py
echo "TOKEN_LB = \"${RECOMMAND_TOKEN_LB}\"" >> config.py
echo "USER_LB = \"${RECOMMAND_USER_LB}\"" >> config.py

# Last.fm API Configuration (Optional)
echo "LASTFM_ENABLED = ${RECOMMAND_LASTFM_ENABLED:-False}" >> config.py
echo "LASTFM_API_KEY = \"${RECOMMAND_LASTFM_API_KEY}\"" >> config.py
echo "LASTFM_API_SECRET = \"${RECOMMAND_LASTFM_API_SECRET}\"" >> config.py
echo "LASTFM_USERNAME = \"${RECOMMAND_LASTFM_USERNAME}\"" >> config.py
echo "LASTFM_PASSWORD_HASH = \"${RECOMMAND_LASTFM_PASSWORD_HASH}\"" >> config.py
echo "LASTFM_SESSION_KEY = \"${RECOMMAND_LASTFM_SESSION_KEY}\"" >> config.py

# Deezer Configuration (Required for downloads)
echo "DEEZER_ARL = \"${RECOMMAND_DEEZER_ARL}\"" >> config.py

# Download Method (choose one)
echo "DOWNLOAD_METHOD = \"${RECOMMAND_DOWNLOAD_METHOD:-streamrip}\"" >> config.py

# Comment Tags for Playlist Creation
echo "TARGET_COMMENT = \"${RECOMMAND_TARGET_COMMENT:-lb_recommendation}\"" >> config.py
echo "LASTFM_TARGET_COMMENT = \"${RECOMMAND_LASTFM_TARGET_COMMENT:-lastfm_recommendation}\"" >> config.py

# History Tracking
echo "PLAYLIST_HISTORY_FILE = \"/app/playlist_history.txt\"" >> config.py

# Set up cron job
# Run every Tuesday at 00:00 PM (Usually the LB playlist release)
echo "0 0 * * 2 /usr/bin/python3 /app/re-command.py >> /var/log/re-command.log 2>&1" > /etc/cron.d/re-command-cron
chmod 0644 /etc/cron.d/re-command-cron
crontab /etc/cron.d/re-command-cron

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

# Start cron service
cron -f &

# Start Gunicorn server for the Flask app in the background
gunicorn --bind 0.0.0.0:5000 --timeout 300 "web_ui.app:app" &

# Execute the main command & keep container running
exec "$@"
