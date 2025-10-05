import requests
import time
import os
import subprocess
import asyncio
from streamrip.client import DeezerClient
from mutagen.id3 import ID3, COMM

class ListenBrainzAPI:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.root_lb = self.config_manager.get("ROOT_LB")
        self.token_lb = self.config_manager.get("TOKEN_LB")
        self.user_lb = self.config_manager.get("USER_LB")
        self.auth_header_lb = {"Authorization": f"Token {self.token_lb}"}
        self.playlist_history_file = self.config_manager.get("PLAYLIST_HISTORY_FILE")

    def _get_last_playlist_name(self):
        """Retrieves the last playlist name from the history file."""
        try:
            with open(self.playlist_history_file, "r") as f:
                return f.readline().strip()
        except FileNotFoundError:
            return None

    def _save_playlist_name(self, playlist_name):
        """Saves the playlist name to the history file."""
        try:
            with open(self.playlist_history_file, "w") as f:
                f.write(playlist_name)
        except OSError as e:
            print(f"Error saving playlist name to file: {e}")

    def has_playlist_changed(self):
        """Checks if the playlist has changed since the last run."""
        current_playlist_name = self.get_latest_playlist_name()
        last_playlist_name = self._get_last_playlist_name()

        if current_playlist_name == last_playlist_name:
            return False

        self._save_playlist_name(current_playlist_name)
        return True

    def get_latest_playlist_name(self):
        """Retrieves the name of the latest *recommendation* playlist from ListenBrainz."""
        playlist_json = self._get_recommendation_playlist(self.user_lb)

        for playlist in playlist_json["playlists"]:
            if playlist["playlist"]["title"].startswith(f"Weekly Exploration for {self.user_lb}"):
                latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                latest_playlist = self._get_playlist_by_mbid(latest_playlist_mbid)
                return latest_playlist['playlist']['title']

        print("Error: 'Weekly Exploration' playlist not found.")
        return None

    def _get_recommendation_playlist(self, username, **params):
        """Fetches the recommendation playlist from ListenBrainz."""
        response = requests.get(
            url=f"{self.root_lb}/1/user/{username}/playlists/recommendations",
            params=params,
            headers=self.auth_header_lb,
        )
        response.raise_for_status()
        return response.json()

    def _get_playlist_by_mbid(self, playlist_mbid, **params):
        """Fetches a playlist by its MBID from ListenBrainz."""
        response = requests.get(
            url=f"{self.root_lb}/1/playlist/{playlist_mbid}",
            params=params,
            headers=self.auth_header_lb,
        )
        response.raise_for_status()
        return response.json()

    def get_track_info(self, recording_mbid, max_retries=3, retry_delay=5):
        """Fetches track information from MusicBrainz."""
        headers = {
            'User-Agent': 're-command-script/1.0 (https://github.com/yourusername/recommand)'
        }
        for attempt in range(max_retries):
            url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?fmt=json&inc=artist-credits+releases"
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                artist_credit = data["artist-credit"][0]
                artist = artist_credit["name"]
                title = data["title"]
                if data["releases"]:
                    album = data["releases"][0]["title"]
                    release_date = data["releases"][0].get("date")
                    release_mbid = data["releases"][0]["id"]
                else:
                    album = "Unknown Album"
                    release_date = None
                    release_mbid = None
                return artist, title, album, release_date, release_mbid
            elif response.status_code == 503:
                time.sleep(retry_delay)
            else:
                print(f"Error getting track info for {recording_mbid}: Status code {response.status_code}")
                return None, None, None, None, None
        return None, None, None, None, None

    def get_listenbrainz_recommendations(self):
        """Fetches recommended songs from ListenBrainz and returns them as a list."""
        if not self.config_manager.get("LISTENBRAINZ_ENABLED"):
            return []

        print("\nChecking for new ListenBrainz recommendations...")
        print("\n")
        print("       @                                                                                                                    ")
        print("   @@@@@ @@@@@      @@@      @@@@          @@@@                      @@@@@@@@                   @@@                         ")
        print(" @@@@@@@ @@@@@@     @@@              @@    @@@@@    @@@    @   @@    @@@   @@@  @   @     @              @@    @@    @      ")
        print(" @@@@@@@ @@@@@@     @@@      @@@  @@@ @@@@ @@@@@ @@@@ @@@  @@@@@@@@  @@@ @@@@   @@@@@@@@@  @@@  @@@ @@@@@@@@@ @@@@@@@@      ")
        print(" @@@@@@@ @@@@@@     @@@      @@@  @@@@@     @@   @@@@@@@@@ @@   @@@  @@@ @ @@@  @@@      @@@@@  @@@  @@   @@@    @@@        ")
        print(" @@@@@@@ @@@@@@     @@@      @@@       @@@  @@   @@@       @@   @@@  @@     @@@ @@@   @@@   @@  @@@  @@   @@@   @@          ")
        print("    @@@@ @@@        @@@@@@@@ @@@@ @@@@@@@   @@@@  @@@@@@@  @@@  @@@  @@@@@@@@   @@@   @@@@@@@@  @@@ @@@   @@@ @@@@@@@@      ")
        print("       @                                                                                                                    ")
        print("                                                                                                                            ")

        latest_playlist_name = self.get_latest_playlist_name()

        if latest_playlist_name is None:
            print("Error: Could not retrieve the latest ListenBrainz playlist name.")
            return []

        playlist_json = self._get_recommendation_playlist(self.user_lb)
        latest_playlist_mbid = None
        for playlist in playlist_json["playlists"]:
            if playlist["playlist"]["title"] == latest_playlist_name:
                latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                break

        if latest_playlist_mbid is None:
            print(f"Error: Could not find ListenBrainz playlist with name '{latest_playlist_name}'.")
            return []

        latest_playlist = self._get_playlist_by_mbid(latest_playlist_mbid)

        recommended_songs = []
        for track in latest_playlist["playlist"]["track"]:
            recording_mbid = track["identifier"][0].split("/")[-1]
            artist, title, album, release_date, release_mbid = self.get_track_info(recording_mbid)
            if artist and title:
                recommended_songs.append({
                    "artist": artist,
                    "title": title,
                    "album": album,
                    "release_date": release_date,
                    "recording_mbid": recording_mbid,
                    "release_mbid": release_mbid,
                    "source": "ListenBrainz"
                })
        return recommended_songs

    def submit_feedback(self, recording_mbid, score):
        """Submits feedback for a recording to ListenBrainz."""
        payload = {"recording_mbid": recording_mbid, "score": score}

        response = requests.post(
            url=f"{self.root_lb}/1/feedback/recording-feedback",
            json=payload,
            headers=self.auth_header_lb
        )
        response.raise_for_status()
        print(f"Feedback submitted for {recording_mbid}: {score}")
