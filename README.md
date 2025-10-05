
# re-command: Automated Music Recommendation System for Navidrome

`re-command` is a modern, async Python-based tool designed to enhance your Navidrome music experience by automatically downloading music recommendations from [ListenBrainz](https://listenbrainz.org) and [Last.fm](https://www.last.fm) using [Deemix](https://deemix.org/) and [Streamrip](https://github.com/nathom/streamrip). It acts as your behind-the-scenes music curator, downloading, tagging, organizing, and importing recommended tracks, while also cleaning up your library based on your ratings.

## Key Features

*   **Multi-Source Recommendations:** Fetches recommendations from both ListenBrainz and Last.fm, with intelligent duplicate detection and removal.
*   **Dual Download Methods:** Supports both Deemix and Streamrip v2 for downloading tracks from Deezer, providing flexibility and reliability.
*   **Intelligent Metadata Tagging:** Automatically tags downloaded tracks with comprehensive metadata including artist, title, album, release date, and MusicBrainz ID (when available) using kid3-cli.
*   **Dynamic Playlist Support:** Downloaded tracks are tagged with configurable comment markers, enabling you to create dynamic playlists in Navidrome or other compatible music players.
*   **Automated Library Maintenance:** Removes tracks from previous recommendations based on your Navidrome ratings (3 stars or lower, including unrated tracks), keeping your library filled with music you enjoy.
*   **ListenBrainz Feedback Integration:** Automatically submits negative feedback to ListenBrainz for 1-star rated tracks, helping improve future recommendations.
*   **Progress Visualization:** Real-time progress bars provide feedback on library processing and track downloads.
*   **Directory Cleanup:** Automatically removes empty folders within your music library, maintaining a tidy and organized structure.
*   **Interactive Setup:** Guided first-time setup process with automatic configuration file generation.
*   **Modular Architecture:** Clean, maintainable code structure with separate API classes for each service.

## Prerequisites

*   **Python 3.7+**
*   **Required Python Libraries:**
    ```bash
    pip install requests tqdm pylast deemix streamrip mutagen
    ```
    or simply:
    ```bash
    pip install -r requirements.txt
    ```
*   **External Tools:**
    *   `kid3-cli` (for audio file tagging)

    Installation examples (may vary depending on your OS):
    ```bash
    # Debian/Ubuntu
    sudo apt install kid3-cli
    # Arch Linux
    yay -S kid3-common
    # macOS
    brew install kid3
    # Fedora/CentOS
    sudo dnf install kid3-cli
    ```
*   **Navidrome Server:** A running Navidrome instance (v0.49.0 or later recommended)
*   **ListenBrainz Account (Optional):** A ListenBrainz user account for music recommendations
*   **Last.fm Account (Optional):** A Last.fm user account for enhanced music discovery
*   **Deezer Account (Free or Premium) & ARL Token:** Your Deezer ARL token for downloading tracks. You can find it in your browser's developer tools under "Application" > "Cookies" after logging into Deezer. Free accounts are limited to 128 kbps MP3 quality.

## Setup

1. **Clone the Repository:**
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2. **Install requirements:**
    ```bash
    pip install -r requirements.txt
    ```

3. **Configuration:**

    *   **First-time setup:** Run the script once (`python3 re-command.py`). It will detect that `config.py` is missing and guide you through an interactive setup process to create it.

    *   **Manual Configuration (Optional):** If you prefer, you can create `config.py` manually. Use the following template:

        ```python
        # Navidrome API Configuration
        ROOT_ND = 'http://your-navidrome-server:4533'      # Replace with your Navidrome URL
        USER_ND = 'your_navidrome_username'                # Your Navidrome username
        PASSWORD_ND = 'your_navidrome_password'             # Your Navidrome password
        MUSIC_LIBRARY_PATH = "/path/to/your/music/library" # Full path to your music library
        TEMP_DOWNLOAD_FOLDER = "/path/to/temp/downloads"    # Temporary folder for downloads

        # ListenBrainz API Configuration (Optional)
        ROOT_LB = 'https://api.listenbrainz.org'            # ListenBrainz API base URL (leave as is)
        TOKEN_LB = 'your_listenbrainz_token'                # Your ListenBrainz API token
        USER_LB = "your_listenbrainz_username"              # Your ListenBrainz username
        LISTENBRAINZ_ENABLED = True                         # Enable/disable ListenBrainz integration

        # Last.fm API Configuration (Optional)
        LASTFM_ENABLED = True                               # Enable/disable Last.fm integration
        LASTFM_API_KEY = "your_lastfm_api_key"              # Your Last.fm API key
        LASTFM_API_SECRET = "your_lastfm_api_secret"        # Your Last.fm API secret
        LASTFM_USERNAME = "your_lastfm_username"            # Your Last.fm username
        LASTFM_SESSION_KEY = "your_lastfm_session_key"      # Your Last.fm session key (more secure)

        # Deezer Configuration (Required for downloads)
        DEEZER_ARL = "your_deezer_arl_token"                # Your Deezer ARL token for downloading

        # Download Method (choose one)
        DOWNLOAD_METHOD = "streamrip"                       # Use "streamrip" (recommended) or "deemix"

        # Comment Tags for Playlist Creation
        TARGET_COMMENT = "lb_recommendation"                # Comment tag for ListenBrainz tracks
        LASTFM_TARGET_COMMENT = "lastfm_recommendation"     # Comment tag for Last.fm tracks

        # History Tracking
        PLAYLIST_HISTORY_FILE = "playlist_history.txt"      # File to track processed playlists
        ```

## Usage

1. **Make the script executable:**
    ```bash
    chmod +x re-command.py
    ```

2. **Run the script:**
    ```bash
    python3 re-command.py
    ```
    **Note:** You may need to be root or part of the `opc` user group to get required permissions for editing the Navidrome music folder.

    The script will perform the following actions:
    *   Parse your Navidrome library, removing previously recommended tracks that you've rated 3 stars or below (including unrated tracks).
    *   Clean up any empty directories in your music library.
    *   Submit negative feedback to ListenBrainz for tracks you've rated 1 star.
    *   Check for new recommendations from enabled services (ListenBrainz and/or Last.fm).
    *   Display found recommendations with source attribution.
    *   Download new tracks using your configured method (Deemix or Streamrip) to a temporary folder.
    *   Automatically tag downloaded tracks with comprehensive metadata using kid3-cli.
    *   Organize tracks into your music library using Artist/Album/Title structure.
    *   Display a summary of successfully downloaded tracks.

3. **Choose your download method:**
    - **Streamrip** (recommended): Modern async downloader with better performance
    - **Deemix**: Legacy downloader, alternative option if Streamrip has issues

**Automation with `cron` (Recommended):**

To run `re-command` automatically on a weekly schedule (e.g., every Monday at 11 PM), you can add a cron job:

1. Edit your crontab:
    ```bash
    crontab -e
    ```

2. Add the following line (adjust the path and time as needed):
    ```
    0 23 * * 1 /usr/bin/python3 /path/to/re-command.py >> /path/to/re-command.log 2>&1
    ```
3. Add your user to the opc user group (otherwise it will not be able to manage the navidrome library):
   ```
   sudo usermod -a -G opc yourusername
   ```

**Dynamic Playlist Tip:**

In your music player, create a dynamic playlist that filters for tracks with the comment tag you set in `config.py` (e.g., "lb-recommendation" or "lastfm_recommendation"). This playlist will automatically update with your latest recommendations.

## Known Issues
*   **Positive ListenBrainz Feedback:** Positive feedback submission to ListenBrainz is not working at all times yet (still in debugging)
*   **Last.fm Album Information:** Due to Last.fm API limitations, album folders for Last.fm downloads may be "UNKNOWN ALBUM" and the downloads may not exactly match the ones on your Last.fm recommendations page (but are usually very close matches)
*   **kid3-cli Dependency:** The script requires kid3-cli for metadata tagging. Ensure it's properly installed and accessible in your PATH

## Recent Improvements
*   **Async Architecture:** Complete rewrite using async/await for better performance and reliability
*   **Streamrip v2 Support:** Added support for the modern Streamrip v2 API as the recommended download method
*   **Enhanced Error Handling:** Improved error handling and logging throughout the application
*   **Better Path Resolution:** More robust file path resolution for organizing music in Navidrome libraries
*   **Modular Design:** Refactored into separate API classes for better maintainability

## Contributing

Contributions to `re-command` are welcome! If you have ideas for improvements, bug fixes, or new features, please feel free to submit issues or pull requests on the project's repository.

## Future Development

*   **Enhanced Feedback System:** Implement submission of positive feedback to ListenBrainz and Last.fm for highly-rated tracks
*   **LLM-based Music Discovery:** Support for LLM based suggestions.
