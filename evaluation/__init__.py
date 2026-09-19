"""Offline casesheet accuracy evaluation tools.

This package is deliberately independent from the FastAPI runtime.  Importing
it does not initialize the database, load Whisper, or call an LLM provider.
"""

