"""Select a provider without sharing API credentials between services."""
from .decision_engine import DecisionEngine
from .gemini import GeminiEngine


def engine_from_config(cfg, store):
    if cfg.decision_provider == 'gemini':
        return GeminiEngine(cfg.gemini_api_key, cfg.gemini_model, cfg.gemini_timeout_seconds,
                            cfg.gemini_max_calls_per_day, store)
    return DecisionEngine(cfg.decision_provider, cfg.typesafe_api_key, cfg.jev_endpoint,
                          cfg.jev_model, timeout=cfg.jev_timeout_seconds)
