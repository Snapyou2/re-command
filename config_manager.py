import os
import json
import pylast
import sys
import subprocess
import requests

class ConfigManager:
    def __init__(self, config_file="config.py"):
        self.config_file = config_file
        self.config = {}
        self._load_config()

    def _load_config(self):
        """Loads configuration from the config.py file."""
        if not os.path.exists(self.config_file):
            self.first_time_setup()
        
        # Temporarily add the TO_REFACTOR directory to sys.path to import config
        sys.path.insert(0, os.path.dirname(self.config_file))
        try:
            import config as user_config
            # Reload the module to ensure latest changes are picked up
            importlib.reload(user_config)
            for key in dir(user_config):
                if not key.startswith("__"):
                    self.config[key] = getattr(user_config, key)
        except ImportError:
            print(f"Error: Could not import {self.config_file}. Please ensure it's a valid Python file.")
            sys.exit(1)
        finally:
            sys.path.pop(0) # Remove the added path

    def _save_config(self):
        """Saves the current configuration to the config.py file."""
        try:
            with open(self.config_file, "w") as f:
                f.write("# Re-command Recommendation Script Configuration\n")
                for key, value in self.config.items():
                    if isinstance(value, str):
                        f.write(f"{key} = \"{value}\"\n")
                    else:
                        f.write(f"{key} = {value}\n")
            print(f"Configuration saved to {self.config_file}.")
        except OSError as e:
            print(f"Error saving configuration file: {e}")
            sys.exit(1)

    def get(self, key, default=None):
        """Retrieves a configuration value."""
        return self.config.get(key, default)

    def set(self, key, value):
        """Sets a configuration value and saves it."""
        self.config[key] = value
        self._save_config()

    def first_time_setup(self):
        """Guides the user through the initial configuration."""
        print("\nWelcome to the Navidrome Recommendation Script! Let's set things up.\n")

        # --- Navidrome Configuration ---
        self.config["ROOT_ND"] = input("Enter your Navidrome root URL (e.g., http://your-navidrome-server:4533): ")
        self.config["USER_ND"] = input("Enter your Navidrome username: ")
        self.config["PASSWORD_ND"] = input("Enter your Navidrome password: ")
        self.config["MUSIC_LIBRARY_PATH"] = input("Enter the full path to your music library directory: ")
        self.config["TEMP_DOWNLOAD_FOLDER"] = input("Enter the full path to a temporary download folder: ")

        # --- ListenBrainz Configuration ---
        use_listenbrainz = input("Do you want to use ListenBrainz? (yes/no): ").lower()
        self.config["LISTENBRAINZ_ENABLED"] = use_listenbrainz == "yes"

        if self.config["LISTENBRAINZ_ENABLED"]:
            self.config["ROOT_LB"] = "https://api.listenbrainz.org"
            print("\nTo get your ListenBrainz token:")
            print("1. Go to https://listenbrainz.org/profile/")
            print("2. Click on 'Edit Profile'.")
            print("3. Scroll down to 'API Keys'.")
            print("4. Generate a new token or copy an existing one.\n")
            self.config["TOKEN_LB"] = input("Enter your ListenBrainz token: ")
            self.config["USER_LB"] = input("Enter your ListenBrainz username: ")
        else:
            self.config["ROOT_LB"] = ""
            self.config["TOKEN_LB"] = ""
            self.config["USER_LB"] = ""

        # --- Last.fm Configuration ---
        use_lastfm = input("Do you want to use Last.fm? (yes/no): ").lower()
        self.config["LASTFM_ENABLED"] = use_lastfm == "yes"

        if self.config["LASTFM_ENABLED"]:
            self.config["LASTFM_USERNAME"] = input("Enter your Last.fm username: ")
            print("\nTo get your Last.fm API key and secret:")
            print("1. Go to https://www.last.fm/api/account/create")
            print("2. Create a new API account (if you don't have one).")
            print("3. Fill in the application details (you can use placeholder values for most fields).")
            print("4. Copy the API key and shared secret.\n")
            self.config["LASTFM_API_KEY"] = input("Enter your Last.fm API key: ")
            self.config["LASTFM_API_SECRET"] = input("Enter your Last.fm API secret: ")
            
            # Attempt to get session key
            try:
                network = pylast.LastFMNetwork(api_key=self.config["LASTFM_API_KEY"], api_secret=self.config["LASTFM_API_SECRET"])
                skg = pylast.SessionKeyGenerator(network)
                url = skg.get_web_auth_url()

                print(f"Please authorize this script to access your Last.fm account: {url}\n")
                import webbrowser
                webbrowser.open(url)
                
                input("Press Enter after you have authorized the application in your browser...")
                session_key = skg.get_web_auth_session_key(url)
                self.config["LASTFM_SESSION_KEY"] = session_key
                print("Last.fm session key obtained and saved.")
            except Exception as e:
                print(f"Error during Last.fm session key generation: {e}")
                self.config["LASTFM_SESSION_KEY"] = ""
        else:
            self.config["LASTFM_USERNAME"] = ""
            self.config["LASTFM_API_KEY"] = ""
            self.config["LASTFM_API_SECRET"] = ""
            self.config["LASTFM_SESSION_KEY"] = ""

        # --- Deezer ARL Configuration for Streamrip ---
        print("\nTo get your Deezer ARL (required for downloading):")
        print("1. Log in to Deezer in your web browser.")
        print("2. Open the Developer Tools (usually by pressing F12).")
        print("3. Go to the 'Application' or 'Storage' tab.")
        print("4. Find the 'Cookies' section and expand it.")
        print("5. Locate the cookie named 'arl'.")
        print("6. Copy the value of the 'arl' cookie.\n")
        self.config["DEEZER_ARL"] = input("Enter your Deezer ARL: ")

        # --- Deemix Configuration (if used) ---
        # This part assumes deemix is installed and configured separately,
        # or we can provide a basic config.json for it.
        # For now, we'll just ensure the .arl file is created for deemix.
        home_dir = os.path.expanduser("~")
        deemix_config_dir = os.path.join(home_dir, ".config", "deemix")
        arl_file_path = os.path.join(deemix_config_dir, ".arl")
        os.makedirs(deemix_config_dir, exist_ok=True)
        try:
            with open(arl_file_path, "w") as arl_file:
                arl_file.write(self.config["DEEZER_ARL"])
            print(f"Deezer ARL saved to {arl_file_path} for deemix.")
        except OSError as e:
            print(f"Error saving Deezer ARL to file for deemix: {e}")

        # --- Other Configuration ---
        self.config["DOWNLOAD_METHOD"] = input("Preferred download method (deemix/streamrip): ").lower()
        self.config["TARGET_COMMENT"] = "lb_recommendation"
        self.config["LASTFM_TARGET_COMMENT"] = "lastfm_recommendation"
        self.config["PLAYLIST_HISTORY_FILE"] = "playlist_history.txt"

        self._save_config()
        print("\nInitial configuration complete. You can edit config.py later if needed.")

# This allows for direct import of config variables from config.py
# without needing to instantiate ConfigManager every time for simple access.
# However, for setting/saving, ConfigManager instance should be used.
import importlib
