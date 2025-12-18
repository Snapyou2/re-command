# re-command: Automated Music Recommendation System for Navidrome

<p align="center">
  <img src="web_ui/assets/logo.svg" width="200" alt="Re-command Logo">
</p>

`re-command` is a modern, containerized music recommendation and automation system that enhances your Navidrome music experience. It automatically discovers and downloads music recommendations from [ListenBrainz](https://listenbrainz.org) and [Last.fm](https://www.last.fm) using [Streamrip](https://github.com/nathom/streamrip) or [Deemix](https://deemix.org/), then organizes and tags them in your music library.

## Key Features

*   **Multi-Source Recommendations:** Fetches music recommendations playlists from ListenBrainz, Last.fm, and LLM-powered suggestions (gemini/openrouter/llama.cpp). Includes a built-in cron scheduling for weekly automated downloads
*   **Dual Download Methods:** Supports both modern Streamrip v2 and legacy Deemix for downloading from Deezer
*   **Fresh Releases Discovery:** Automatically shows newly released albums from ListenBrainz with a quick download button
*   **Universal Link Downloads:** Download music straight to your sever with Spotify, YouTube, Deezer, and other platforms links using Songlink API integration (still in beta)
*   **Track Previews & Feedback:** Preview tracks before downloading and submit feedback manually to ListenBrainz/Last.fm
*   **Dynamic Playlist Support:** Downloaded tracks are tagged with configurable comment markers for dynamic playlists
*   **Automated Library Maintenance:** Removes tracks from previous recommendations and submit scrobbling feedbacks based on your Navidrome ratings
*   **Containerized Deployment:** Full Docker support with automated setup and configuration

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start with Docker Compose](#quick-start-with-docker-compose)
- [Alternative: Quick Start with Docker (Script)](#alternative-quick-start-with-docker-script)
- [Screenshots](#screenshots)
- [Usage Modes](#usage-modes)
- [Local Development Setup (non-dockerized)](#local-development-setup-non-dockerized)
- [Manual Configuration](#manual-configuration)
- [LLM Model Comparison](#llm-model-comparison)
- [Advanced Configuration](#advanced-configuration)
- [Troubleshooting](#troubleshooting)
- [Contributing / Roadmap](#contributing--roadmap)

## Prerequisites

- [Docker](https://www.docker.com/get-started) and [Docker Compose](https://docs.docker.com/compose/) installed
- A running [Navidrome](https://www.navidrome.org/) instance
- [Deezer](https://www.deezer.com/) account with ARL token
- A [ListenBrainz](https://listenbrainz.org/) account for ListenBrainz recommendations, fresh releases and LLM playlists

Optional
- A [Last.fm API account](https://www.last.fm/api/account/create) for Last.fm recommendations
- A LLM API key or base URL for llama.cpp 

## Quick Start with Docker Compose Image

### 1. Download only the docker.yml 

```bash
wget https://raw.githubusercontent.com/Snapyou2/re-command/refs/heads/main/docker/docker-compose.yml

```

Edit the file and set at least the volumes to your Navidrome music library path. Replace the whole "{MUSIC_PATH:-../music}" with the full library path.
It should look like this:
```
    volumes:
      - /home/snapyou2/Music:/app/music
      - /home/snapyou2/Music/.tempfolder:/app/temp_downloads
```

### 2. Start the Application

```bash
docker compose up -d
```

### 3. Access the Web Interface

Open `http://localhost:5000` in your browser. Configure Navidrome access, playlist providers, and Deezer ARL in the settings. You can also click the "Create Smart Playlists" after you configured everything and then trigger a rescan of your Navidrome library.

## Screenshots

![Web Interface](web_ui/assets/screenshot.jpg)

![Sources](web_ui/assets/sources.jpg)

![Playlist View](web_ui/assets/playlist.jpg)

![Settings](web_ui/assets/settings.jpg)

## Usage Modes

### 1. Automated Weekly Downloads

Runs automatically every Tuesday at 00:00 (configurable) via cron job. The process runs in two phases:

**Phase 1: Library Cleanup & Feedback**
- Scans your Navidrome library for tracks with recommendation comments
- **1 star**: Sends negative feedback and deletes the track
- **2-3 stars**: Deletes the track (no feedback)
- **4 stars**: Keeps the track and removes the recommendations comment (no feedback, but out of your dynamic playlist)
- **5 stars**: Sends positive feedback, keeps the track and removes the recommendation comment
- Feedback is submitted to ListenBrainz and Last.fm based on your ratings

**Phase 2: Download New Recommendations**
- Fetches new recommendations from ListenBrainz, Last.fm and/or LLM playlists (based on what is enabled)
- Downloads and tags new tracks using Streamrip or Deemix
- Organizes downloaded music into path/artist/album/track

### 2. Fresh Releases Discovery

Discovery of newly released albums:
- Fetches from ListenBrainz fresh releases API each time you load the web page
- Displays last 10 albums with album art
- Allows selective downloading (only for one week if set up in the settings)
- Organizes into music library

### 3. Link Downloads

Download music from any supported platform:
- Paste a music link from your favorite music app and get them downloaded on your server using Songlink API. Links supported by service :
  - Spotify : tracks/albums
  - Deezer : tracks/albums
  - Apple music : tracks/albums
  - Tidal : tracks/albums
  - Youtube Music : tracks/some playlists
  - Amazon Music : very experimental

### 4. Individual Track Downloads from Recommendation Playlists

Via web interface:
- Preview tracks before downloading (30-second previews)
- Download individual tracks from recommendations
- Submit manual like/dislike feedback to the playlist provider (defaults to ListenBrainz for LLM playlists)

### 5. Library Maintenance

Cleans up your music library based on ratings (done automatically with the cron job but can be manually triggered in the settings):
- Automatically removes tracks rated 3 stars or below
- Submits feedback to ListenBrainz for disliked tracks
- Clears recommendation tags from highly rated tracks

### 6. Manual Control

Via web interface or command line:
```bash
# Download only ListenBrainz recommendations
python re-command.py --source listenbrainz

# Download only Last.fm recommendations
python re-command.py --source lastfm

# Download only LLM recommendations
python re-command.py --source llm

# Download all available fresh releases
python re-command.py --source fresh_releases

# Run library cleanup based on ratings
python re-command.py --cleanup

# Bypass playlist change detection for Listenbrainz (redownload a playlist previously downloaded)
python re-command.py --bypass-playlist-check
```

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

## Manual Configuration

### Environment Variables (Docker)

| Variable | Description |
|----------|-------------|
| `RECOMMAND_ROOT_ND` | Navidrome server URL |
| `RECOMMAND_USER_ND` | Navidrome username |
| `RECOMMAND_PASSWORD_ND` | Navidrome password |
| `RECOMMAND_DEEZER_ARL` | Deezer ARL token |
| `RECOMMAND_LISTENBRAINZ_ENABLED` | Enable ListenBrainz |
| `RECOMMAND_TOKEN_LB` | ListenBrainz API token |
| `RECOMMAND_USER_LB` | ListenBrainz username |
| `RECOMMAND_LASTFM_ENABLED` | Enable Last.fm |
| `RECOMMAND_LASTFM_USERNAME` | Last.fm username |
| `RECOMMAND_LASTFM_PASSWORD` | Last.fm password |
| `RECOMMAND_LASTFM_API_KEY` | Last.fm API key |
| `RECOMMAND_LASTFM_API_SECRET` | Last.fm API secret |
| `RECOMMAND_LLM_ENABLED` | Enable LLM suggestions |
| `RECOMMAND_LLM_PROVIDER` | LLM provider (gemini/openrouter/llama) |
| `RECOMMAND_LLM_API_KEY` | LLM API key |
| `RECOMMAND_LLM_MODEL_NAME` | LLM model name |

### Configuration File (Local)

Open the config.py and fill it with the proper information.

### API Endpoints

The web interface exposes RESTful APIs:

- `GET /api/config` - Get current configuration
- `POST /api/update_config` - Update configuration settings
- `POST /api/update_arl` - Update Deezer ARL token
- `POST /api/update_cron` - Update scheduling
- `POST /api/toggle_cron` - Enable/disable automatic downloads
- `GET /api/get_listenbrainz_playlist` - Get ListenBrainz recommendations
- `POST /api/trigger_listenbrainz_download` - Trigger ListenBrainz playlist download
- `GET /api/get_lastfm_playlist` - Get Last.fm recommendations
- `POST /api/trigger_lastfm_download` - Trigger Last.fm playlist download
- `GET /api/get_llm_playlist` - Get LLM-powered recommendations
- `POST /api/trigger_llm_download` - Trigger LLM playlist download
- `GET /api/get_fresh_releases` - Get fresh releases
- `POST /api/trigger_fresh_release_download` - Download specific release
- `POST /api/trigger_navidrome_cleanup` - Run library cleanup
- `POST /api/submit_listenbrainz_feedback` - Submit feedback for ListenBrainz tracks
- `POST /api/submit_lastfm_feedback` - Submit feedback for Last.fm tracks
- `GET /api/get_track_preview` - Get track preview URL
- `POST /api/trigger_track_download` - Download individual track
- `POST /api/download_from_link` - Download from universal music links
- `GET /api/get_deezer_album_art` - Get album art from Deezer

## LLM Model Comparison

re-command supports various Large Language Models for music recommendations. From experience, gemini-2.5-flash remains the best available free model amongst external APIs recommendations options. Here is a performance comparison of free OpenRouter models I tested for music discovery:

### Best to Worst Performance:

| Model | Response Time | Originality | Song Finding Reliability | Notes |
|-------|---------------|-------------|---------------------------|-------|
| **tngtech/deepseek-r1t2-chimera:free** | 1.9 min | 8/10 | 7/10 | Excellent creativity, good at finding songs |
| **google/gemma-3-27b-it:free** | 1.4 min | 7/10 | 8/10 | Fast, reliable song discovery |
| **meta-llama/llama-3.3-70b-instruct:free** | 1.5 min | 5/10 | 8/10 | Very reliable, but less creative |
| **z-ai/glm-4.5-air:free** | 2.7 min | 6/10 | 7/10 | Decent but slow |
| **amazon/nova-2-lite-v1:free** | 1.3 min | 7/10 | 5/10 | Fast but misses some songs |
| **mistralai/mistral-small-3.1-24b-instruct:free** | 1.2 min | 4/10 | 5/10 | Fastest but least creative |
| **qwen/qwen3-235b-a22b:free** | 3 min | 4/10 | 7/10 | Slow but reliable |
| **arcee-ai/trinity-mini:free** | 1.2 min | 3/10 | 5/10 | Fast but poor performance |
| **openai/gpt-oss-20b:free** | Failed | - | - | Not working |
| **moonshotai/kimi-k2:free** | Failed | - | - | Not working |
| **openai/gpt-oss-120b:free** | Failed | - | - | Not working |
| **allenai/olmo-3-32b-think:free** | Failed | - | - | Not working |


## Advanced Configuration

### Custom Download Quality
If you have a Deezer Premium account, you can get better mp3 quality.

Edit the Streamrip configuration in Docker:
```bash
docker exec -it re-command bash
# Edit /root/.config/streamrip/config.toml
```

Or edit the Deemix config if you are using it:
```bash
docker exec -it re-command bash
# Edit /root/.config/deemix/config.json
```

## Troubleshooting

### Quick Fixes

**Container Won't Start:**
- Check all required environment variables are set
- Verify Navidrome server is accessible

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

### Logs

Please add the docker logs when creating an issue:

```bash
# View container logs
docker logs -f re-command-container
```

## Contributing / Roadmap

Contributions are welcome! Areas for improvement:

- Really looking forward sharing links to an Android re-command PWA (I tried and failed many times so PRs are welcomed!)
- Adding Tidal as a streamrip option to get higher resolution downloads (quite unstable for now)
