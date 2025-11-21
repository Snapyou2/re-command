import pylast
import time
import os
import requests
import webbrowser
import importlib
import concurrent.futures
from apis.deezer_api import DeezerAPI
from config import *

class LastFmAPI:
    def __init__(self):
        self.network = None

    def _make_request_with_retries(self, method, url, headers, params=None, json=None, max_retries=5, retry_delay=5):
        """
        Makes an HTTP request with retry logic for connection errors.
        """
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, params=params)
                elif method == "POST":
                    response = requests.post(url, headers=headers, json=json)
                response.raise_for_status()
                return response
            except requests.exceptions.ConnectionError as e:
                print(f"Connection error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.RequestException as e:
                print(f"Request error on attempt {attempt + 1}/{max_retries}: {e}")
                raise
        return None

    def authenticate_lastfm(self):
        """Authenticates with Last.fm using pylast."""
        api_key = LASTFM_API_KEY
        api_secret = LASTFM_API_SECRET
        username = LASTFM_USERNAME
        password_hash = LASTFM_PASSWORD_HASH
        session_key = LASTFM_SESSION_KEY

        if not (api_key and api_secret and username):
            print("Last.fm API key, secret, or username not configured.")
            return None

        if session_key:
            self.network = pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                username=username,
                session_key=session_key
            )
        elif password_hash:
            self.network = pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                username=username,
                password_hash=password_hash
            )
        else:
            # Get session key if not configured
            self.network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
            skg = pylast.SessionKeyGenerator(self.network)
            url = skg.get_web_auth_url()

            print(f"Please authorize this script to access your account: {url}\n")
            webbrowser.open(url)

            time.sleep(5)

            while True:
                input("Press Enter after you have authorized the application...")
                try:
                    session_key = skg.get_web_auth_session_key(url)
                    self.network.session_key = session_key
                    break
                except pylast.WSError as e:
                    if e.details == "The token supplied to this request is invalid. It has either expired or not yet been authorised.":
                        print("Token still invalid or not authorized yet. Please ensure you've authorized and try again.")
                    else:
                        print(f"Error during authentication: {e.details}")
                        return None
        return self.network

    def get_recommended_tracks(self, limit=100):
        """
        Fetches recommended tracks from Last.fm using the undocumented /recommended endpoint.
        """
        if not self.network:
            print("Last.fm not authenticated.")
            return []

        username = LASTFM_USERNAME
        recommendations = []

        url = f"https://www.last.fm/player/station/user/{username}/recommended"
        headers = {
            'Referer': 'https://www.last.fm/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        try:
            response = self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers
            )
            if response is None:
                print("Failed to get response from Last.fm API after retries.")
                return []
            data = response.json()

            for track_data in data["playlist"]:
                artist = track_data["artists"][0]["name"]
                title = track_data["name"]
                recommendations.append({
                    "artist": artist,
                    "title": title,
                    "album": "Unknown Album",
                    "release_date": None
                })

                if len(recommendations) >= limit:
                    break
            return recommendations
        except requests.exceptions.RequestException as e:
            print(f"Error fetching Last.fm recommendations: {e}")
            return []
        except KeyError as e:
            print(f"Unexpected Last.fm API response structure for recommendations: missing key {e}")
            return []
        except Exception as e:
            print(f"Unexpected error in Last.fm API: {e}")
            return []

    def get_lastfm_recommendations(self):
        """Fetches recommended tracks from Last.fm and returns them as a list."""
        if not LASTFM_ENABLED:
            return []

        print("\nChecking for new Last.fm recommendations...")
        print("\n\033[31m")
        print("###                                   #####              ")
        print("#%#                      ###         ##%#                ")
        print("#%#    #####     #####  ##%####     ##%%##### ####  #### ")
        print("#%#  #### ####  ### #####%%####     ##%#####%############")
        print("#%#  #%#    #%% ####     %%#         #%#   #%#   %%#  #%#")
        print("#%# ##%#    #%%#  #####  #%#         #%#   #%#   %%#  #%#")
        print("#%#  ####  ######   #### ###  # #### #%#   #%#   %%#  #%#")
        print(" ####  ######  #######    ##### ###  ###   ###   ###  ###")
        print("\033[0m")
        
        network = self.authenticate_lastfm()
        if not network:
            print("Failed to authenticate with Last.fm. Cannot get Last.fm recommendations.")
            return []

        recommended_tracks = self.get_recommended_tracks()

        if not recommended_tracks:
            print("No recommendations found from Last.fm.")
            return []

        # Parallel album arts fetching
        def fetch_art(track):
            deezer_api = DeezerAPI()
            details = deezer_api.get_deezer_track_details_from_artist_title(track["artist"], track["title"])
            return details

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            album_details = list(executor.map(fetch_art, recommended_tracks))

        songs = []
        for i, track in enumerate(recommended_tracks):
            song = {
                "artist": track["artist"],
                "title": track["title"],
                "album": track["album"],
                "release_date": track["release_date"],
                "album_art": None,
                "recording_mbid": None,
                "source": "Last.fm"
            }
            details = album_details[i]
            if details:
                song["album_art"] = details.get("album_art")
                song["album"] = details.get("album", song["album"])
            songs.append(song)
        return songs
