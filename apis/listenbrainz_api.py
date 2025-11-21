import requests
import time
import os
import subprocess
import asyncio
import sys
from streamrip.client import DeezerClient
from mutagen.id3 import ID3, COMM
from apis.deezer_api import DeezerAPI
from config import *

class ListenBrainzAPI:
    def __init__(self):
        self.root_lb = ROOT_LB
        self.token_lb = TOKEN_LB
        self.user_lb = USER_LB
        self.auth_header_lb = {"Authorization": f"Token {self.token_lb}"}
        self.playlist_history_file = PLAYLIST_HISTORY_FILE

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

    async def has_playlist_changed(self):
        """Checks if the playlist has changed since the last run asynchronously."""
        current_playlist_name = await self.get_latest_playlist_name()
        last_playlist_name = self._get_last_playlist_name()

        if current_playlist_name == last_playlist_name:
            return False

        self._save_playlist_name(current_playlist_name)
        return True

    async def get_latest_playlist_name(self):
        """Retrieves the name of the latest *recommendation* playlist from ListenBrainz asynchronously."""
        playlist_json = await self._get_recommendation_playlist(self.user_lb)

        for playlist in playlist_json["playlists"]:
            if playlist["playlist"]["title"].startswith(f"Weekly Exploration for {self.user_lb}"):
                latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                latest_playlist = await self._get_playlist_by_mbid(latest_playlist_mbid)
                return latest_playlist['playlist']['title']

        print("Error: 'Weekly Exploration' playlist not found.")
        return None

    async def _make_request_with_retries(self, method, url, headers, params=None, json=None, max_retries=5, retry_delay=5):
        """
        Makes an HTTP request with retry logic for connection errors, asynchronously.
        """
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, params=params))
                elif method == "POST":
                    response = await loop.run_in_executor(None, lambda: requests.post(url, headers=headers, json=json))
                elif method == "HEAD":
                    response = await loop.run_in_executor(None, lambda: requests.head(url, headers=headers, params=params))
                response.raise_for_status()
                return response
            except requests.exceptions.ConnectionError as e:
                print(f"ListenBrainz API: Connection error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.Timeout as e:
                print(f"ListenBrainz API: Timeout error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.HTTPError as e:
                print(f"ListenBrainz API: HTTP error on attempt {attempt + 1}/{max_retries} to {url}: {e.response.status_code} - {e.response.text}", file=sys.stderr)
                raise
            except requests.exceptions.RequestException as e:
                print(f"ListenBrainz API: General request error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                raise
        return None

    async def _get_recommendation_playlist(self, username, **params):
        """Fetches the recommendation playlist from ListenBrainz asynchronously."""
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/user/{username}/playlists/recommendations",
            params=params,
            headers=self.auth_header_lb,
        )
        return response.json()

    async def _get_playlist_by_mbid(self, playlist_mbid, **params):
        """Fetches a playlist by its MBID from ListenBrainz asynchronously."""
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/playlist/{playlist_mbid}",
            params=params,
            headers=self.auth_header_lb,
        )
        return response.json()

    async def get_track_info(self, recording_mbid):
        """Fetches track information from MusicBrainz asynchronously."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?fmt=json&inc=artist-credits+releases"
        
        try:
            response = await self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers,
                retry_delay=2
            )
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
            await asyncio.sleep(3)
            return artist, title, album, release_date, release_mbid
        except requests.exceptions.RequestException as e:
            print(f"Error getting track info for {recording_mbid}: {e}", file=sys.stderr)
            return None, None, None, None, None

    async def get_listenbrainz_recommendations(self):
        """Fetches recommended songs from ListenBrainz and returns them as a list."""
        if not LISTENBRAINZ_ENABLED:
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

        latest_playlist_name = await self.get_latest_playlist_name()

        if latest_playlist_name is None:
            print("Error: Could not retrieve the latest ListenBrainz playlist name.")
            return []

        playlist_json = await self._get_recommendation_playlist(self.user_lb)
        latest_playlist_mbid = None
        for playlist in playlist_json["playlists"]:
            if playlist["playlist"]["title"] == latest_playlist_name:
                latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                break

        if latest_playlist_mbid is None:
            print(f"Error: Could not find ListenBrainz playlist with name '{latest_playlist_name}'.")
            return []

        latest_playlist = await self._get_playlist_by_mbid(latest_playlist_mbid)

        tracks = latest_playlist["playlist"]["track"]
        recommended_songs = []
        for track in tracks:
            artist = track.get("creator", "Unknown Artist")
            title = track.get("title", "Unknown Title")
            album = track.get("album", "Unknown Album")
            recording_mbid = track.get("id")
            if artist and title:
                song = {
                    "artist": artist,
                    "title": title,
                    "album": album,
                    "release_date": None,  # Not available in playlist
                    "album_art": None, # Fetched by frontend
                    "recording_mbid": recording_mbid,
                    "source": "ListenBrainz"
                }
                recommended_songs.append(song)
        return recommended_songs

    async def _fetch_album_art_from_caa(self, release_mbid):
        """Fetches album art from Cover Art Archive using release MBID asynchronously."""
        if not release_mbid:
            return None
        url = f"https://coverartarchive.org/release/{release_mbid}/front-250"
        try:
            response = await self._make_request_with_retries(
                method="HEAD",
                url=url,
                headers={},
                retry_delay=1
            )
            return url
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"  No cover art found for MBID {release_mbid}")
                return None
            else:
                raise
        except Exception as e:
            print(f"  Error fetching from CAA for MBID {release_mbid}: {e}")
            return None

    async def _fetch_album_art_for_track(self, artist, title, release_mbid=None):
        """Fetches album art for a single track asynchronously."""
        print(f"Fetching album art for: {artist} - {title}")
        # CAA first if mbid available
        if release_mbid:
            print(f"  Trying CAA with MBID {release_mbid}")
            try:
                caa_url = await self._fetch_album_art_from_caa(release_mbid)
                if caa_url:
                    print(f"  Success: Found album art from CAA for {artist} - {title}")
                    return caa_url
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    print(f"  CAA returned 404 for MBID {release_mbid}, trying Deezer")
                else:
                    print(f"  CAA error {e.response.status_code} for MBID {release_mbid}, not trying Deezer")
                    return '/assets/default-album.svg'
            except Exception as e:
                print(f"  Error with CAA for MBID {release_mbid}: {e}, not trying Deezer")
                return '/assets/default-album.svg'

        # Fall back to Deezer if no mbid or CAA failed with 404
        loop = asyncio.get_event_loop()
        deezer_api = DeezerAPI()
        print(f"  Trying Deezer album search for {artist} - {title}")
        try:
            details = await loop.run_in_executor(None, lambda: deezer_api.get_deezer_album_art(artist, title))
            if details and details.get('album_art'):
                print(f"  Success: Found album art from Deezer for {artist} - {title}")
                return details.get('album_art')
        except Exception as e:
            print(f"  Deezer search failed for {artist} - {title}: {e}")

        print(f"  All attempts failed for {artist} - {title}, using placeholder")
        return '/assets/default-album.svg'

    async def get_fresh_releases(self, sort="release_date", past=True, future=True):
        """Fetches fresh releases for the user from ListenBrainz asynchronously."""
        params = {
            "sort": sort,
            "past": str(past).lower(),
            "future": str(future).lower()
        }
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/user/{self.user_lb}/fresh_releases",
            headers=self.auth_header_lb,
            params=params
        )
        data = response.json()
        releases = data.get('payload', {}).get('releases', [])

        # Sort by release_date in descending order and take the first 10
        releases.sort(key=lambda x: x.get('release_date', ''), reverse=True)
        latest_10_releases = releases[:10]

        for release in latest_10_releases:
            release['album_art'] = None # Placeholder, fetched by fronted

        return {'payload': {'releases': latest_10_releases}}

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
