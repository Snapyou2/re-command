# re-command: Automated Music Recommendation System for Navidrome

<p align="center">
  <img src="web_ui/assets/logo.svg" width="200" alt="Re-command Logo">
</p>

`re-command` is a modern, containerized music recommendation and automation system that enhances your Navidrome music experience. It automatically discovers and downloads music recommendations from [ListenBrainz](https://listenbrainz.org) and [Last.fm](https://www.last.fm) using [Streamrip](https://github.com/nathom/streamrip) or [Deemix](https://deemix.org/), then organizes and tags them in your music library.

## Key Features

*   **Multi-Source Recommendations:** Fetches music recommendations playlists from both ListenBrainz and Last.fm. Includes a built-in cron scheduling for weekly automated downloads
*   **Dual Download Methods:** Supports both modern Streamrip v2 and legacy Deemix for downloading from Deezer
*   **Fresh Releases Discovery:** Automatically shows newly released albums from ListenBrainz with a quick download button
*   **Universal Link Downloads:** Download music straight to your sever with Spotify, YouTube, Deezer, and other platforms links using Songlink API integration
*   **Track Previews & Feedback:** Preview tracks before downloading and submit feedback manually to ListenBrainz/Last.fm
*   **Dynamic Playlist Support:** Downloaded tracks are tagged with configurable comment markers for dynamic playlists
*   **Automated Library Maintenance:** Removes tracks from previous recommendations and submit scrobbling feedbacks based on your Navidrome ratings
*   **Containerized Deployment:** Full Docker support with automated setup and configuration

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
Recommended: configuration done through the web interface:
```bash
chmod +X docker/run-re-command.sh
sh docker/run-re-command.sh --min-setup
```
This will start the container with the minimal config to do, and you just need to enter your navidrome music folder. You can then access your server on port 5000 and use the Settings → Configuration section to set up all your preferences. 

If you are fine with a lot of CLI copy-pasting, you can also run the same commands without the --min-setup flag.

### 3. Access the Web Interface

Open your browser and go to `http://localhost:5000` to access the web interface.

### 4. Create a Dynamic Playlist
In your music player or directly in Navidrome, create a playlist that includes *Comment is lb_recommendation* AND *Comment is lastfm_recommendation*. Or do a separate playlist for each. Adding another filter *Rating > 1* is quite convenient as well to get rid of the tracks you don't like.

## Screenshots

![Web Interface](web_ui/assets/screenshot.jpg)

![Playlist View](web_ui/assets/playlist.jpg)

![Settings](web_ui/assets/settings.png)

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
- `GET /api/get_fresh_releases` - Get fresh releases
- `POST /api/trigger_fresh_release_download` - Download specific release
- `POST /api/trigger_navidrome_cleanup` - Run library cleanup
- `POST /api/submit_listenbrainz_feedback` - Submit feedback for ListenBrainz tracks
- `POST /api/submit_lastfm_feedback` - Submit feedback for Last.fm tracks
- `GET /api/get_track_preview` - Get track preview URL
- `POST /api/trigger_track_download` - Download individual track
- `POST /api/download_from_link` - Download from universal music links
- `GET /api/get_deezer_album_art` - Get album art from Deezer

## Usage Modes

### 1. Automated Weekly Downloads

Runs automatically every Tuesday at 00:00 (configurable) to:
- Send positive and negative feedback to ListenBrainz and Last.fm for 1 star / 5 stars tracks
- Remove unrated and low-rated tracks (≤3 stars)
- Download new recommendations from playlist providers
- Organize and tag new tracks

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
- Submit manual feedback (like/dislike) to improve future recommendations

### 5. Library Maintenance

Cleans up your music library based on ratings (done automatically with the cron job, can be manually triggered in the settings or disabled):
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

# Download all available fresh releases
python re-command.py --source fresh_releases

# Run library cleanup based on ratings
python re-command.py --cleanup

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

### Logs and Debugging

```bash
# View container logs
docker logs -f re-command-container
```

## Contributing / Roadmap

Contributions are welcome! Areas for improvement:

- Really looking forward sharing links to an Android re-command PWA (I have many failed attemps so PRs are welcomed !)
- Additional music recommendations (ie LLMs + scrobbling could be interesting)
- Adding Tidal as a streamrip option to get higher resolution downloads (quite unstable for now)

### Development Setup

```bash
git clone <repository_url>
cd re-command
pip install -r requirements.txt
python re-command.py  # Test CLI
python web_ui/app.py  # Test web UI
```
