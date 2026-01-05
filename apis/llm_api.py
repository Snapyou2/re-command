import google.generativeai as genai
import requests
import json
import sys
import re

class LlmAPI:
    def __init__(self, provider, gemini_api_key=None, openrouter_api_key=None, llama_api_key=None, model_name=None, base_url=None):
        self.provider = provider
        self.gemini_api_key = gemini_api_key
        self.openrouter_api_key = openrouter_api_key
        self.llama_api_key = llama_api_key
        self.model_name = model_name
        self.base_url = base_url

        if self.provider == 'gemini' and self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)
            gemini_model = self.model_name or 'gemini-2.5-flash'
            self.model = genai.GenerativeModel(gemini_model)
        elif self.provider == 'openrouter' and self.openrouter_api_key:
            # Use custom base URL if provided, otherwise use OpenRouter's default
            self.openrouter_url = self.base_url or "https://openrouter.ai/api/v1/chat/completions"
            self.headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
        elif self.provider == 'llama' and self.base_url:
            # For Llama.cpp, use the base URL as the endpoint
            self.llama_url = self.base_url
            self.headers = {
                "Content-Type": "application/json"
            }
            # Add API key if provided (some Llama.cpp servers might require it)
            if self.llama_api_key:
                self.headers["Authorization"] = f"Bearer {self.llama_api_key}"
        else:
            raise ValueError("LLM provider is not configured correctly. Please provide API keys and base URL for Llama.cpp.")

    def _build_prompt(self, scrobbles_json):
        """Builds the prompt for the LLM."""
        prompt = f"""
You are a music expert assistant. Based on the following list of recently listened tracks in JSON format, please recommend 25 new songs that this listener might like.
The recommendations should be for a user who enjoys the artists and genres represented in the listening history. Only recommend tracks that are not already in the listening history.

My listening history:
{scrobbles_json}

Please provide your response as a single JSON array of objects, where each object represents a recommended track and has the keys "artist", "title", and "album". Do not include any other text or explanations in your response, only the JSON array.

Example response format:
[
  {{"artist": "Example Artist 1", "track": "Example Song 1", "album": "Example Album 1"}},
  {{"artist": "Example Artist 2", "track": "Example Song 2", "album": "Example Album 2"}}
]
"""
        return prompt

    def get_recommendations(self, scrobbles):
        """
        Gets music recommendations from the configured LLM provider.
        'scrobbles' is a list of dicts with 'artist' and 'track'.
        """
        if not scrobbles:
            return []

        scrobbles_json = json.dumps(scrobbles, indent=2)
        prompt = self._build_prompt(scrobbles_json)

        try:
            if self.provider == 'gemini':
                response = self.model.generate_content(prompt)
                response_text = response.text
            elif self.provider == 'openrouter':
                openrouter_model = self.model_name or "tngtech/deepseek-r1t2-chimera:free"
                data = {
                    "model": openrouter_model,
                    "messages": [{"role": "user", "content": prompt}]
                }
                api_response = requests.post(self.openrouter_url, headers=self.headers, json=data)
                api_response.raise_for_status()
                response_text = api_response.json()['choices'][0]['message']['content']
            elif self.provider == 'llama':
                # For Llama.cpp, use the OpenAI-compatible API format
                llama_model = self.model_name or "local-model"
                data = {
                    "model": llama_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1000
                }
                api_response = requests.post(self.llama_url, headers=self.headers, json=data)
                if api_response.status_code != 200:
                    print(f"LLM API Error: {api_response.status_code} {api_response.text}", file=sys.stderr)
                api_response.raise_for_status()
                response_text = api_response.json()['choices'][0]['message']['content']
            else:
                return []

            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if not json_match:
                print(f"LLM API Error: Could not find a JSON array in the response.\nLLM Raw Response: {response_text}", file=sys.stderr)
                return []
            
            recommendations = json.loads(json_match.group(0))

            # Normalize keys to handle variations from different LLM models
            normalized_recommendations = []
            for rec in recommendations:
                if isinstance(rec, dict):
                    normalized_rec = {}
                    # Map common variations to standard keys
                    key_mappings = {
                        'artist': ['artist', 'artist_name'],
                        'title': ['title', 'song', 'track', 'name'],
                        'album': ['album', 'album_name', 'album_title']
                    }

                    for standard_key, possible_keys in key_mappings.items():
                        for possible_key in possible_keys:
                            if possible_key in rec:
                                normalized_rec[standard_key] = rec[possible_key]
                                break
                        # If no mapping found, set to empty string
                        if standard_key not in normalized_rec:
                            normalized_rec[standard_key] = ''

                    normalized_recommendations.append(normalized_rec)
                else:
                    # If not a dict, skip this recommendation
                    continue

            return normalized_recommendations
        except Exception as e:
            print(f"Error getting recommendations from {self.provider}: {e}", file=sys.stderr)
            return []
