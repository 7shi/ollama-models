"""Inspect and manage Ollama model stores directly from the models directory."""

from importlib.metadata import version
__version__ = version("ollama-models")  # single source of truth, avoids hard-coding version here
