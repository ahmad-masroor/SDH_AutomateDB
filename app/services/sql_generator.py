"""
SQL generating LLM, called through Groq API (not local) for now but Ollama also present (local) for future perspective.
Requires Ollama installed locally and the model already pulled once: ollama pull qwen2.5-coder:7b
Temperature is fixed at 0 (see settings.ollama_temperature) — this is a
query writer, not a creative writer; the same question should produce the
same SQL every time.
"""
from __future__ import annotations
import logging
from typing import Protocol
import re 
import ollama
from groq import Groq
logger = logging.getLogger(__name__)

class LLMProvider(Protocol):
    def generate(self, prompt: str) -> str: ...
 
def _strip_markdown_fence(text: str) -> str:
    """
    Models frequently wrap SQL in a fenced code block even when told to
    return only SQL, and often add a header before it and an explanation or note after it. Have to remove all of that to get the raw SQL for validation and execution.
    """
    text = text.strip()
    fence_match = re.search(r"```(?:sql)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return text
 
class OllamaSQLGenerator:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        temperature: float = 0.0,
        timeout_seconds: int = 120,
        num_ctx: int = 4096,
    ):
        self.model = model
        self.client = ollama.Client(host=host, timeout=timeout_seconds)
        self.temperature = temperature
        self.num_ctx = num_ctx
 
    def generate(self, prompt: str) -> str:
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={"temperature": self.temperature, "num_ctx": self.num_ctx},
                stream=False,
            )
        except Exception as exc:  
            logger.error("Ollama call failed: %s", exc)
            raise
 
        raw = (response.response or "").strip()
        return _strip_markdown_fence(raw)
 
class GroqSQLGenerator:
    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.0,
        timeout_seconds: int = 60,
    ):
        if not api_key:
            raise ValueError(
                "APP_GROQ_API_KEY is not set in .env "
            )
        self.model = model
        self.client = Groq(api_key=api_key, timeout=timeout_seconds)
        self.temperature = temperature
 
    def generate(self, prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
            )
        except Exception as exc:  
            logger.error("Groq call failed: %s", exc)
            raise
 
        raw = (response.choices[0].message.content or "").strip()
        return _strip_markdown_fence(raw)