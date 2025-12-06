#!/bin/bash

# Check for skip setup flag
MIN_SETUP=false
if [[ "$1" == "--min-setup" ]]; then
    MIN_SETUP=true
    echo "Minimal interactive setup. Using default values or existing configuration."
    echo "You can configure the application via the web UI at http://localhost:5000"
    echo ""
fi

echo "Welcome to the re-command Docker deployment script!"
if [[ "$MIN_SETUP" == false ]]; then
    echo "This script will prompt you for configuration details to set up the container."
    echo "Alternatively, run with --min-setup to use defaults and configure via web UI."
else
    echo "Running with default configuration. Configure via web UI at http://localhost:5000"
fi
echo ""

# Set default values
CONTAINER_NAME="re-command-container"
IMAGE_NAME="re-command-app"
ROOT_ND=""
USER_ND=""
PASSWORD_ND=""
LISTENBRAINZ_ENABLED="False"
TOKEN_LB=""
USER_LB=""
LASTFM_ENABLED="False"
LASTFM_USERNAME=""
LASTFM_API_KEY=""
LASTFM_API_SECRET=""
LASTFM_SESSION_KEY=""
LLM_ENABLED="True"
LLM_PROVIDER="gemini"
LLM_API_KEY=""
LLM_MODEL_NAME=""
LLM_TARGET_COMMENT="llm_recommendation"
DEEZER_ARL=""
DOWNLOAD_METHOD="streamrip"
MUSIC_PATH="/tmp/music"

# Always prompt for music library path (can not do it later)
echo ""
echo "=== Volume Mounts ==="
read -p "Enter the full path to your music library directory on the host (default: /tmp/music): " MUSIC_PATH
MUSIC_PATH=${MUSIC_PATH:-/tmp/music}
TEMP_PATH="$MUSIC_PATH/.tempfolder"

if [[ "$MIN_SETUP" == false ]]; then
    # Prompt for container and image names
    read -p "Enter container name (default: re-command-container): " CONTAINER_NAME
    CONTAINER_NAME=${CONTAINER_NAME:-re-command-container}

    read -p "Enter image name (default: re-command-app): " IMAGE_NAME
    IMAGE_NAME=${IMAGE_NAME:-re-command-app}

    # Prompt for Navidrome configuration
    echo ""
    echo "=== Navidrome Configuration ==="
    read -p "Enter your Navidrome root URL (e.g., http://your-navidrome-server:4533): " ROOT_ND
    read -p "Enter your Navidrome username: " USER_ND
    read -p "Enter your Navidrome password: " PASSWORD_ND
    echo ""

    # Prompt for ListenBrainz configuration
    echo ""
    echo "=== ListenBrainz Configuration ==="
    read -p "Enable ListenBrainz integration? (y/n): " ENABLE_LB
    case $ENABLE_LB in
        [Yy]*)
            LISTENBRAINZ_ENABLED="True"
            echo "To get your ListenBrainz token:"
            echo "1. Go to https://listenbrainz.org/profile/"
            echo "2. Click on 'Edit Profile'."
            echo "3. Scroll down to 'API Keys'."
            echo "4. Generate a new token or copy an existing one."
            read -p "Enter your ListenBrainz token: " TOKEN_LB
            read -p "Enter your ListenBrainz username: " USER_LB
            ;;
        *)
            LISTENBRAINZ_ENABLED="False"
            TOKEN_LB=""
            USER_LB=""
            ;;
    esac

    # Prompt for Last.fm configuration
    echo ""
    echo "=== Last.fm Configuration ==="
    read -p "Enable Last.fm integration? (y/n): " ENABLE_LASTFM
    case $ENABLE_LASTFM in
        [Yy]*)
            LASTFM_ENABLED="True"
            read -p "Enter your Last.fm username: " LASTFM_USERNAME
            echo "To get your Last.fm API key and secret:"
            echo "1. Go to https://www.last.fm/api/account/create"
            echo "2. Create a new API account (if you don't have one)."
            echo "3. Fill in the application details (you can use placeholder values for most fields)."
            echo "4. Copy the API key and shared secret."
            read -p "Enter your Last.fm API key: " LASTFM_API_KEY
            read -p "Enter your Last.fm API secret: " LASTFM_API_SECRET
            read -p "Enter your Last.fm session key (leave blank if you don't have one): " LASTFM_SESSION_KEY
            ;;
        *)
            LASTFM_ENABLED="False"
            LASTFM_USERNAME=""
            LASTFM_API_KEY=""
            LASTFM_API_SECRET=""
            LASTFM_SESSION_KEY=""
            ;;
    esac

    # Prompt for LLM configuration
    echo ""
    echo "=== LLM Configuration ==="
    read -p "Enable LLM recommendations? (y/n) [default: y]: " ENABLE_LLM
    ENABLE_LLM=${ENABLE_LLM:-y}
    case $ENABLE_LLM in
        [Yy]*)
            LLM_ENABLED="True"
            echo "Available LLM providers: gemini, openrouter"
            read -p "Choose LLM provider (gemini/openrouter) [default: gemini]: " LLM_PROVIDER
            LLM_PROVIDER=${LLM_PROVIDER:-gemini}
            if [[ "$LLM_PROVIDER" == "gemini" ]]; then
                echo "To get your Gemini API key:"
                echo "1. Go to https://makersuite.google.com/app/apikey"
                echo "2. Create a new API key or use an existing one."
                read -p "Enter your Gemini API key: " LLM_API_KEY
                read -p "Enter Gemini model name (leave blank for default 'gemini-2.5-flash'): " LLM_MODEL_NAME
                LLM_MODEL_NAME=${LLM_MODEL_NAME:-gemini-2.5-flash}
            elif [[ "$LLM_PROVIDER" == "openrouter" ]]; then
                echo "To get your OpenRouter API key:"
                echo "1. Go to https://openrouter.ai/keys"
                echo "2. Create a new API key or use an existing one."
                read -p "Enter your OpenRouter API key: " LLM_API_KEY
                read -p "Enter OpenRouter model name (leave blank for default 'tngtech/deepseek-r1t2-chimera:free'): " LLM_MODEL_NAME
                LLM_MODEL_NAME=${LLM_MODEL_NAME:-tngtech/deepseek-r1t2-chimera:free}
            else
                echo "Invalid provider selected. Using default Gemini."
                LLM_PROVIDER="gemini"
                read -p "Enter your Gemini API key: " LLM_API_KEY
                LLM_MODEL_NAME="gemini-2.5-flash"
            fi
            ;;
        *)
            LLM_ENABLED="False"
            LLM_API_KEY=""
            LLM_MODEL_NAME=""
            ;;
    esac

    # Prompt for Deezer ARL
    echo ""
    echo "=== Deezer Configuration ==="
    echo "To get your Deezer ARL (required for downloading):"
    echo "1. Log in to Deezer in your web browser."
    echo "2. Open the Developer Tools (usually by pressing F12)."
    echo "3. Go to the 'Application' or 'Storage' tab."
    echo "4. Find the 'Cookies' section and expand it."
    echo "5. Locate the cookie named 'arl'."
    echo "6. Copy the value of the 'arl' cookie."
    read -p "Enter your Deezer ARL: " DEEZER_ARL

    # Prompt for download method
    echo ""
    echo "=== Download Configuration ==="
    read -p "Preferred download method (streamrip/deemix) [default: streamrip]: " DOWNLOAD_METHOD
    DOWNLOAD_METHOD=${DOWNLOAD_METHOD:-streamrip}
else
    # Skip setup - use defaults for API configs
    echo "Using default configuration values for API integrations."
fi

# Stop and remove existing container
echo "Stopping and removing existing $CONTAINER_NAME..."
sudo docker stop $CONTAINER_NAME || true
sudo docker rm $CONTAINER_NAME || true

# Build the Docker image
echo "Building $IMAGE_NAME Docker image..."
sudo docker build -t $IMAGE_NAME -f docker/Dockerfile .

# Run the Docker container
echo ""
echo "Running $CONTAINER_NAME..."
sudo docker run -d \
  --name $CONTAINER_NAME \
  -p 5000:5000 \
  -e RECOMMAND_ROOT_ND="$ROOT_ND" \
  -e RECOMMAND_USER_ND="$USER_ND" \
  -e RECOMMAND_PASSWORD_ND="$PASSWORD_ND" \
  -e RECOMMAND_LISTENBRAINZ_ENABLED="$LISTENBRAINZ_ENABLED" \
  -e RECOMMAND_TOKEN_LB="$TOKEN_LB" \
  -e RECOMMAND_USER_LB="$USER_LB" \
  -e RECOMMAND_LASTFM_ENABLED="$LASTFM_ENABLED" \
  -e RECOMMAND_LASTFM_API_KEY="$LASTFM_API_KEY" \
  -e RECOMMAND_LASTFM_API_SECRET="$LASTFM_API_SECRET" \
  -e RECOMMAND_LASTFM_USERNAME="$LASTFM_USERNAME" \
  -e RECOMMAND_LASTFM_SESSION_KEY="$LASTFM_SESSION_KEY" \
  -e RECOMMAND_LLM_ENABLED="$LLM_ENABLED" \
  -e RECOMMAND_LLM_PROVIDER="$LLM_PROVIDER" \
  -e RECOMMAND_LLM_API_KEY="$LLM_API_KEY" \
  -e RECOMMAND_LLM_MODEL_NAME="$LLM_MODEL_NAME" \
  -e RECOMMAND_LLM_TARGET_COMMENT="$LLM_TARGET_COMMENT" \
  -e RECOMMAND_DEEZER_ARL="$DEEZER_ARL" \
  -e RECOMMAND_DOWNLOAD_METHOD="$DOWNLOAD_METHOD" \
  -e RECOMMAND_TARGET_COMMENT="lb_recommendation" \
  -e RECOMMAND_LASTFM_TARGET_COMMENT="lastfm_recommendation" \
  -e RECOMMAND_ALBUM_RECOMMENDATION_COMMENT="album_recommendation" \
  -v "$MUSIC_PATH:/app/music" \
  -v "$TEMP_PATH:/app/temp_downloads" \
  $IMAGE_NAME

echo ""
echo "Container $CONTAINER_NAME is now running!"
echo "You can access the web UI at http://localhost:5000"
echo "The script will run automatically every Tuesday at 00:00 via cron."
echo ""
echo "To manually run the recommendation script inside the container:"
echo "sudo docker exec $CONTAINER_NAME python3 /app/re-command.py"
echo ""
echo "Script finished."
