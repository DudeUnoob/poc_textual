"""Shared provider settings for the API, worker, extraction CLI and benchmark."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')


def credential(name):
    value = os.environ.get(name, '')
    if not value and os.environ.get(name + '_FILE'):
        value = Path(os.environ[name + '_FILE']).read_text().strip()
    return value


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    portkey_model: str
    comparison_model: str
    gateway: str
    timeout: float
    attempts: int
    max_tokens: int

    def snapshot(self, model=None):
        selected = model or self.model
        return {'provider': 'portkey' if selected.startswith('@') else 'gemini',
                'model': selected, 'strategy': 'portkey_full_page' if selected.startswith('@') else 'row_blocks',
                'gateway': self.gateway if selected.startswith('@') else None,
                'timeout_seconds': self.timeout, 'max_attempts': self.attempts,
                'max_output_tokens': self.max_tokens,
                'prompt_version': 'portkey-v2' if selected.startswith('@') else '1950-v3'}


def get_provider_config():
    provider = os.environ.get('CENSUS_PROVIDER', 'portkey').lower()
    if provider not in ('portkey', 'gemini'):
        raise ValueError('CENSUS_PROVIDER must be portkey or gemini')
    primary = os.environ.get('PORTKEY_MODEL', '@anthropic-census/claude-opus-5-5')
    comparison = os.environ.get('PORTKEY_COMPARISON_MODEL', '@openai-census/gpt-6-astra')
    for model in (primary, comparison):
        if not model.startswith('@') or '/' not in model or not all(model[1:].split('/', 1)):
            raise ValueError('Portkey models must use @catalog-slug/model syntax')
    gateway = os.environ.get('PORTKEY_BASE_URL', 'https://api.portkey.ai/v1').rstrip('/')
    url = urlsplit(gateway)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('PORTKEY_BASE_URL must be an HTTPS URL without embedded credentials, query or fragment')
    timeout = float(os.environ.get('PORTKEY_TIMEOUT_SECONDS', '240'))
    attempts = int(os.environ.get('PORTKEY_MAX_ATTEMPTS', '3'))
    tokens = int(os.environ.get('PORTKEY_MAX_OUTPUT_TOKENS', '16000'))
    if not 1 <= timeout <= 1800 or not 1 <= attempts <= 5 or not 256 <= tokens <= 128000:
        raise ValueError('Invalid Portkey timeout, retry count or output token limit')
    return ProviderConfig(provider, primary if provider == 'portkey' else os.environ.get('GEMINI_MODEL','gemini-3.5-flash'),
                          primary, comparison, gateway, timeout, attempts, tokens)


def require_credentials(model):
    if model.startswith('@'):
        if not credential('PORTKEY_API_KEY'):
            raise RuntimeError('PORTKEY_API_KEY is not configured. Set it in .env or use PORTKEY_API_KEY_FILE.')
    else:
        if get_provider_config().provider != 'gemini':
            raise RuntimeError('Legacy Gemini extraction is disabled in the current provider mode. Enable explicit legacy mode to run it.')
        if not (credential('GEMINI_API_KEY') or credential('GOOGLE_API_KEY')):
            raise RuntimeError('GEMINI_API_KEY is not configured for this legacy run.')
