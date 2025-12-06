import google.generativeai as genai
import requests
import json
import sys
import re

class LlmAPI:
    def __init__(self, provider, gemini_api_key=None, openrouter_api_key=None, model_name=None):
        self.provider = provider
        self.gemini_api_key = gemini_api_key
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
        self.model_name = model_name

        if self.provider == 'gemini' and self.gemini_api_key:
            genai.configure(api_key=self.gemini_api_key)
            gemini_model = self.model_name or 'gemini-2.5-flash'
            self.model = genai.GenerativeModel(gemini_model)
        elif self.provider == 'openrouter' and self.openrouter_api_key:
            self.headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
        else:
            raise ValueError("LLM provider is not configured correctly. Please provide API keys.")

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
  {{"artist": "Example Artist 1", "title": "Example Song 1", "album": "Example Album 1"}},
  {{"artist": "Example Artist 2", "title": "Example Song 2", "album": "Example Album 2"}}
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
            else:
                return []

            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if not json_match:
                print(f"LLM API Error: Could not find a JSON array in the response.\nLLM Raw Response: {response_text}", file=sys.stderr)
                return []
            
            recommendations = json.loads(json_match.group(0))
            return recommendations
        except Exception as e:
            print(f"Error getting recommendations from {self.provider}: {e}", file=sys.stderr)
            return []