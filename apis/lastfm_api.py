import pylast
import time
import os
import requests
import webbrowser
import importlib

class LastFmAPI:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.network = None

    def authenticate_lastfm(self):
        """Authenticates with Last.fm using pylast."""
        api_key = self.config_manager.get("LASTFM_API_KEY")
        api_secret = self.config_manager.get("LASTFM_API_SECRET")
        username = self.config_manager.get("LASTFM_USERNAME")
        password_hash = self.config_manager.get("LASTFM_PASSWORD_HASH")
        session_key = self.config_manager.get("LASTFM_SESSION_KEY")

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
                    self.config_manager.set("LASTFM_SESSION_KEY", session_key)
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

        username = self.config_manager.get("LASTFM_USERNAME")
        recommendations = []

        url = f"https://www.last.fm/player/station/user/{username}/recommended"
        headers = {
            'Referer': 'https://www.last.fm/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
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
        except KeyError:
            print("Unexpected Last.fm API response structure for recommendations.")
            return []

    def get_lastfm_recommendations(self):
        """Fetches recommended tracks from Last.fm and returns them as a list."""
        if not self.config_manager.get("LASTFM_ENABLED"):
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

        songs = []
        for track in recommended_tracks:
            songs.append({
                "artist": track["artist"],
                "title": track["title"],
                "album": track["album"],
                "release_date": track["release_date"],
                "recording_mbid": None,
                "release_mbid": None,
                "source": "Last.fm"
            })
        return songs
