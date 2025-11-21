# re-command: Automated Music Recommendation System for Navidrome

![Re-command Logo](web_ui/assets/logo.svg)

`re-command` is a modern, containerized music recommendation and automation system that enhances your Navidrome music experience. It automatically discovers and downloads music recommendations from [ListenBrainz](https://listenbrainz.org) and [Last.fm](https://www.last.fm) using [Streamrip](https://github.com/nathom/streamrip) or [Deemix](https://deemix.org/), then organizes and tags them in your music library.

## Key Features

*   **Multi-Source Recommendations:** Fetches music recommendations playlists from both ListenBrainz and Last.fm. Includes a built-in cron scheduling for weekly automated downloads
*   **Dual Download Methods:** Supports both modern Streamrip v2 and legacy Deemix for downloading from Deezer
*   **Fresh Releases Discovery:** Automatically shows newly released albums from ListenBrainz with a quick download button
*   **Modern Web Interface:** Clean, responsive web UI for configuration, monitoring, and manual controls
*   **Dynamic Playlist Support:** Downloaded tracks are tagged with configurable comment markers for dynamic playlists
*   **Automated Library Maintenance:** Removes tracks from previous recommendations based on your Navidrome ratings
*   **Containerized Deployment:** Full Docker support with automated setup and configuration

![Screenshot](web_ui/assets/Screenshot.jpg)

## Quick Start with Docker

### Prerequisites

- [Docker](https://www.docker.com/get-started) installed
- A running [Navidrome](https://www.navidrome.org/) instance
- [Deezer](https://www.deezer.com/) account with ARL token
- A [ListenBrainz](https://listenbrainz.org/) and/or [Last.fm](https://www.last.fm/) account (I recommend both !)

### 1. Get Your Deezer ARL Token

1. Log into [Deezer](https://www.deezer.com/) (free accounts supported)
2. Open browser Developer Tools (F12)
3. Go to Application → Cookies
4. Copy the `arl` cookie value

### 2. Run the Container

Enter the repo:
```bash
cd re-command
```
Launch the docker run script:
```bash
chmod +X docker/run-re-command.sh
sh docker/run-re-command.sh
```
Then, simply enter all the required info about your navidrome instance, Deezer arl, API keys for ListenBrainz and Last.fm, music download location, etc.

### 3. Access the Web Interface

Open your browser and go to `http://localhost:5000` to access the web interface.

### 4. Create a Dynamic Playlist
In your music player or directly in Navidrome, create a playlist that includes *Comment is lb_recommendation* AND *Comment is lastfm_recommendation*. Or do a separate playlist for each. Adding another filter *Rating < 1* is quite convenient as well to get rid of the tracks you don't like.

## Local Development Setup (non-dockerized)

### Prerequisites

- Python 3.11+
- Git
- Navidrome server (local or remote)
- Deezer ARL token

### 1. Clone the Repository

```bash
git clone <repository_url>
cd re-command
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Edit the configuration file:

```bash
nano config.py
```

### 4. Run the Application

**Command Line Interface:**
```bash
python re-command.py
```

**Web Interface:**
```bash
python web_ui/app.py
```

Then open `http://localhost:5000` in your browser.

## Configuration

### Environment Variables (Docker)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `RECOMMAND_ROOT_ND` | Navidrome server URL | Yes | - |
| `RECOMMAND_USER_ND` | Navidrome username | Yes | - |
| `RECOMMAND_PASSWORD_ND` | Navidrome password | Yes | - |
| `RECOMMAND_DEEZER_ARL` | Deezer ARL token | Yes | - |
| `RECOMMAND_LISTENBRAINZ_ENABLED` | Enable ListenBrainz | No | `false` |
| `RECOMMAND_TOKEN_LB` | ListenBrainz API token | No | - |
| `RECOMMAND_USER_LB` | ListenBrainz username | No | - |
| `RECOMMAND_LASTFM_ENABLED` | Enable Last.fm | No | `false` |
| `RECOMMAND_LASTFM_API_KEY` | Last.fm API key | No | - |
| `RECOMMAND_LASTFM_API_SECRET` | Last.fm API secret | No | - |
| `RECOMMAND_LASTFM_USERNAME` | Last.fm username | No | - |

### Configuration File (Local)

```python
# Navidrome Configuration
ROOT_ND = "http://your-navidrome-server:4533"
USER_ND = "your_username"
PASSWORD_ND = "your_password"
MUSIC_LIBRARY_PATH = "/path/to/music"
TEMP_DOWNLOAD_FOLDER = "/path/to/temp"

# ListenBrainz Configuration
LISTENBRAINZ_ENABLED = True
TOKEN_LB = "your_token"
USER_LB = "your_username"

# Last.fm Configuration
LASTFM_ENABLED = True
LASTFM_API_KEY = "your_key"
LASTFM_API_SECRET = "your_secret"
LASTFM_USERNAME = "your_username"

# Deezer Configuration
DEEZER_ARL = "your_arl_token"

# Download Method
DOWNLOAD_METHOD = "streamrip"  # or "deemix"
```
### API Endpoints

The web interface exposes RESTful APIs:

- `GET /api/config` - Get current configuration
- `POST /api/update_arl` - Update Deezer ARL token
- `POST /api/update_cron` - Update scheduling
- `GET /api/get_listenbrainz_playlist` - Get ListenBrainz recommendations
- `POST /api/trigger_listenbrainz_download` - Trigger ListenBrainz download
- `GET /api/get_lastfm_playlist` - Get Last.fm recommendations
- `POST /api/trigger_lastfm_download` - Trigger Last.fm download
- `GET /api/get_fresh_releases` - Get fresh releases
- `POST /api/trigger_fresh_release_download` - Download specific release
- `POST /api/trigger_navidrome_cleanup` - Run library cleanup

## Usage Modes

### 1. Automated Weekly Downloads

Runs automatically every Tuesday at 00:00 (configurable) to:
- Remove unrated or low-rated tracks (≤3 stars)
- Download new recommendations
- Organize and tag new tracks

### 2. Fresh Releases Discovery

Discovery of newly released albums:
- Fetches from ListenBrainz fresh releases API each time you load the web page
- Displays last 10 albums with album art
- Allows selective downloading
- Organizes into music library

### 3. Manual Control

Via web interface or command line:
```bash
# Download only ListenBrainz recommendations
python re-command.py --source listenbrainz

# Download only Last.fm recommendations
python re-command.py --source lastfm

# Download all available fresh releases
python re-command.py --source fresh_releases

# Bypass playlist change detection
python re-command.py --bypass-playlist-check
```

## Advanced Configuration

### Custom Download Quality
If you have a Deezer Premium account, you can get better mp3 quality.

Edit the Streamrip configuration in Docker:
```bash
docker exec -it re-command bash
# Edit /root/.config/streamrip/config.toml
```

Or edit the Deemix config if you use it:
```bash
docker exec -it re-command bash
# Edit /root/.config/deemix/config.json
```

### Custom Scheduling

The default cron schedule runs weekly. Customize in the web interface or by editing the cron configuration in the container.

### Custom Tag Comments

Modify the comment tags for playlist creation:
- `TARGET_COMMENT`: ListenBrainz tracks (default: lb_recommendation)
- `LASTFM_TARGET_COMMENT`: Last.fm tracks (default: lastfm_recommendation)

## Troubleshooting

### Quick Fixes

**Container Won't Start:**
- Check all required environment variables are set
- Verify Navidrome server is accessible
- Ensure Deezer ARL token is valid

**Downloads Failing:**
- Verify ARL token is fresh (not expired)
- Check Deezer account status (free accounts limited to 128kbps)
- Ensure sufficient disk space

**Web Interface Not Loading:**
- Check port 5000 is not in use
- Verify container is running: `docker ps`
- Check logs: `docker logs re-command`

**Navidrome Integration Issues:**
- Verify server URL and credentials
- Check Navidrome version (v0.49.0+ recommended)
- Ensure music library path is writable

### Logs and Debugging

```bash
# View container logs
docker logs -f re-command

# Access container shell
docker exec -it re-command bash
```

## Contributing

Contributions are welcome! Areas for improvement:

- Additional music recommendations (ie LLMs + scrobbling could be interesting)
- Performance optimizations (notably for the webUI)

### Development Setup

```bash
git clone <repository_url>
cd re-command
pip install -r requirements.txt
python re-command.py  # Test CLI
python web_ui/app.py  # Test web UI
```

## Roadmap

- [ ] **PWA Support:** Progressive Web App for mobile installation (and sharing links to re-command from mobile to the PWA for quick downloading)
- [ ] **Single Song Listening/Downloading:** Preview tracks before download
- [ ] **Enhanced Feedback:** Positive/negative feedback to Last.fm and Listenbrainz depending on the track rating.
