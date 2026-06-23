import requests
from google import genai
from openai import OpenAI
from typing import Dict, Any, Optional, List
from src.core.config import settings

class AIService:
    def __init__(self):
        self.gemini_client = None
        self.openai_client = None
        self.mistral_api_key = settings.MISTRAL_API_KEY
        self.mistral_api_url = "https://api.mistral.ai/v1/chat/completions"

        # Initialize Gemini Client
        has_gemini = settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "placeholder_gemini_key"
        if has_gemini:
            try:
                self.gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            except Exception as e:
                print(f"Failed to initialize Gemini client: {e}")

        # Initialize OpenAI Client
        has_openai = settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "placeholder_openai_key"
        if has_openai:
            try:
                self.openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
            except Exception as e:
                print(f"Failed to initialize OpenAI client: {e}")

    def call_mistral(self, messages: List[Dict[str, str]], model: Optional[str] = None) -> Dict[str, Any]:
        """Direct cloud API call to Mistral AI with timeout and exponential backoff retry logic."""
        if not self.mistral_api_key or self.mistral_api_key == "your_mistral_api_key":
            return {"success": False, "error": "Mistral API key is not configured"}

        headers = {
            "Authorization": f"Bearer {self.mistral_api_key}",
            "Content-Type": "application/json"
        }
        
        model_name = model or "mistral-large-latest"
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.7
        }

        max_retries = 3
        backoff_factor = 2.0
        timeout = 10.0
        
        import time

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.mistral_api_url, 
                    json=payload, 
                    headers=headers,
                    timeout=timeout
                )
                
                # Check for rate limits or transient server errors to trigger retry
                if response.status_code == 429:
                    sleep_time = backoff_factor ** attempt
                    print(f"Mistral API rate limit hit (429). Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                    continue
                elif response.status_code >= 500:
                    sleep_time = backoff_factor ** attempt
                    print(f"Mistral API server error ({response.status_code}). Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                    continue

                response_json = response.json()
                if response.status_code != 200:
                    return {
                        "success": False, 
                        "error": response_json.get("message", response_json.get("error", {}).get("message", "Error from Mistral API")),
                        "status_code": response.status_code
                    }
                    
                return {
                    "success": True,
                    "data": response_json
                }
            except requests.exceptions.Timeout as e:
                sleep_time = backoff_factor ** attempt
                print(f"Mistral API timeout. Retrying in {sleep_time}s...")
                if attempt < max_retries - 1:
                    time.sleep(sleep_time)
                else:
                    return {"success": False, "error": "Mistral API request timed out after retries"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Max retries exceeded for Mistral API"}

    def generate_text(self, prompt: str, provider: str = "gemini", model: Optional[str] = None) -> Dict[str, Any]:
        """Generate text using Gemini, OpenAI, or Mistral AI depending on the selection."""
        model_name = model or settings.DEFAULT_MODEL
        
        if provider.lower() == "mistral":
            messages = [{"role": "user", "content": prompt}]
            res = self.call_mistral(messages=messages, model=model)
            if not res["success"]:
                return res
            
            data = res["data"]
            choices = data.get("choices", [])
            text = choices[0].get("message", {}).get("content", "") if choices else ""
            usage = data.get("usage", {})
            return {
                "success": True,
                "provider": "mistral",
                "text": text,
                "model": data.get("model", "mistral-large-latest"),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0)
                }
            }

        elif provider.lower() == "gemini":
            if not self.gemini_client:
                return {
                    "success": True,
                    "provider": "gemini (mock)",
                    "text": f"Mock response for prompt: '{prompt}' using model: '{model_name}'. (Gemini API key is not configured)"
                }
            try:
                response = self.gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return {
                    "success": True,
                    "provider": "gemini",
                    "text": response.text,
                    "model": model_name,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif provider.lower() == "openai":
            if not self.openai_client:
                return {
                    "success": True,
                    "provider": "openai (mock)",
                    "text": f"Mock response for prompt: '{prompt}' using OpenAI. (OpenAI API key is not configured)"
                }
            try:
                open_ai_model = model or "gpt-4o-mini"
                response = self.openai_client.chat.completions.create(
                    model=open_ai_model,
                    messages=[{"role": "user", "content": prompt}]
                )
                return {
                    "success": True,
                    "provider": "openai",
                    "text": response.choices[0].message.content,
                    "model": open_ai_model,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        else:
            return {"success": False, "error": f"Unsupported AI provider: {provider}"}

ai_service = AIService()
