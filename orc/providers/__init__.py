import os
import time
from typing import List, Optional
from .base import AIProvider, ChatMessage, ChatResponse
from .groq import GroqProvider
from .openrouter import OpenRouterProvider
from .nvidia import NvidiaProvider
from .gemini import GeminiProvider
from orc.logger import get_logger, log_ai

try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    load_dotenv(os.path.abspath(_env_path))
except Exception:
    pass

logger = get_logger('providers')

from orc.circuit_breaker import CircuitBreaker
from orc.budgets import BudgetGroup, Budget

# Per-provider circuit breakers (one failing shouldn't block others)
_provider_cbs: dict = {}


def _get_cb(name: str) -> CircuitBreaker:
    if name not in _provider_cbs:
        _provider_cbs[name] = CircuitBreaker(
            name=f"provider:{name}",
            failure_threshold=0.5,
            window=10,
            cooldown=30,
        )
    return _provider_cbs[name]

PROVIDER_MAP = {
    'gemini': GeminiProvider,
    'groq': GroqProvider,
    'openrouter': OpenRouterProvider,
    'nvidia': NvidiaProvider,
}

# Complexity thresholds (in chars of user+system message content)
COMPLEXITY_SIMPLE_MAX = 300
COMPLEXITY_MEDIUM_MAX = 1500

HARD_TIMEOUT_SEC = 30


def _is_transient_error(error: Exception) -> bool:
    s = str(error).lower()
    return (
        '429' in s or 'rate' in s or 'tokens per minute' in s or
        '503' in s or 'unavailable' in s or 'high demand' in s or
        'overloaded' in s or 'temporarily' in s or 'timeout' in s or
        'timed out' in s or 'connection' in s
    )


def _estimate_complexity(messages: List[ChatMessage]) -> str:
    total_chars = sum(len(m.content or '') for m in messages)
    has_code = any('```' in (m.content or '') for m in messages)
    has_xml = any('<' in (m.content or '') and '>' in (m.content or '') for m in messages)
    has_long_lines = any(len(line) > 100 for m in messages for line in (m.content or '').split('\n'))

    if total_chars > COMPLEXITY_MEDIUM_MAX or has_code or has_long_lines:
        return 'complex'
    if total_chars > COMPLEXITY_SIMPLE_MAX:
        return 'medium'
    return 'simple'


def _retry_chat(provider, messages, model=None, max_tokens=1000, temperature=0.7, max_retries=3, tools=None):
    cb = _get_cb(provider.name)
    if not cb.allow_request():
        logger.warning(f"Circuit breaker open for provider {provider.name}, skipping")
        raise Exception(f"circuit open for {provider.name}")

    import inspect
    sig = inspect.signature(provider.chat)
    kwargs = {}
    if tools is not None and 'tools' in sig.parameters:
        kwargs['tools'] = tools

    for attempt in range(max_retries):
        deadline = time.time() + HARD_TIMEOUT_SEC
        try:
            if time.time() > deadline:
                raise Exception(f"hard timeout ({HARD_TIMEOUT_SEC}s) exceeded for {provider.name}")
            response = provider.chat(
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )
            cb.record(True)
            return response
        except Exception as e:
            if _is_transient_error(e) and attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                logger.warning(f"Transient error on {provider.name}: {e}. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            cb.record(False)
            raise

    cb.record(False)
    raise Exception(f"all {max_retries} retries exhausted for {provider.name}")


class ProviderManager:
    def __init__(self, config: dict):
        self.config = config
        self.providers: List[AIProvider] = []
        self._active_index = 0
        self._coding_provider: Optional[AIProvider] = None
        self._init_providers()

    def _init_providers(self):
        ai_config = self.config.get('ai', {})
        primary_name = ai_config.get('provider', 'openrouter')
        if primary_name in PROVIDER_MAP:
            provider = PROVIDER_MAP[primary_name]()
            if provider.is_available():
                self.providers.append(provider)
                logger.info(f"Primary provider: {primary_name}")
        for fb in ai_config.get('fallback', []):
            fb_name = fb.get('provider')
            if fb_name in PROVIDER_MAP:
                provider = PROVIDER_MAP[fb_name]()
                if provider.is_available():
                    self.providers.append(provider)
                    logger.info(f"Fallback provider: {fb_name}")
        coding_name = ai_config.get('coding_provider')
        coding_model = ai_config.get('coding_model')
        if coding_name and coding_name in PROVIDER_MAP:
            coding_provider = PROVIDER_MAP[coding_name]()
            if coding_provider.is_available():
                self._coding_provider = coding_provider
                self._coding_model = coding_model
                logger.info(f"Coding provider: {coding_name} ({coding_model})")
        if not self.providers:
            logger.warning("No AI providers available")

    @property
    def active(self) -> Optional[AIProvider]:
        if self.providers:
            return self.providers[self._active_index]
        return None

    @property
    def active_name(self) -> str:
        return self.active.name if self.active else "none"

    @property
    def active_model(self) -> str:
        return self.config.get('ai', {}).get('model', 'unknown')

    def chat(self, messages, model=None, max_tokens=None, temperature=None, coding=False, tools=None):
        ai_config = self.config.get('ai', {})
        max_tokens = max_tokens or ai_config.get('max_tokens', 1000)
        temperature = temperature or ai_config.get('temperature', 0.7)

        # Complexity-based routing
        complexity = _estimate_complexity(messages)
        logger.debug(f"Message complexity: {complexity}")

        # Use coding provider if requested, or automatically for complex tasks
        use_coding = coding or complexity == 'complex'
        if use_coding and self._coding_provider:
            coding_model = self._coding_model or ai_config.get('coding_model')
            try:
                response = _retry_chat(
                    self._coding_provider,
                    messages,
                    model=coding_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                )
                log_ai(provider=response.provider, model=response.model,
                       tokens_in=response.tokens_in, tokens_out=response.tokens_out,
                       latency_ms=response.latency_ms)
                return response
            except Exception as e:
                logger.warning(f"Coding provider {self._coding_provider.name} failed: {e}")

        # Try active provider first, then remaining fallbacks via per-provider circuit breaker
        last_error = None
        ordered_providers = self.providers[self._active_index:] + self.providers[:self._active_index]
        for provider in ordered_providers:
            cb = _get_cb(provider.name)
            if not cb.allow_request():
                logger.debug(f"Skipping {provider.name} (circuit open)")
                continue
            try:
                response = _retry_chat(
                    provider,
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                )
                log_ai(provider=response.provider, model=response.model,
                       tokens_in=response.tokens_in, tokens_out=response.tokens_out,
                       latency_ms=response.latency_ms)
                return response
            except Exception as e:
                last_error = e
                logger.warning(f"Provider {provider.name} failed: {e}")
                continue

        raise Exception(f"All providers failed. Last error: {last_error}")

    def chat_coding(self, messages, max_tokens=None, temperature=None):
        return self.chat(messages, max_tokens=max_tokens, temperature=temperature, coding=True)

    def switch_provider(self, name: str) -> bool:
        for i, p in enumerate(self.providers):
            if p.name == name:
                self._active_index = i
                logger.info(f"Switched to provider: {name}")
                return True
        return False

    def list_available(self) -> List[dict]:
        return [{"name": p.name, "models": p.list_models()} for p in self.providers]
