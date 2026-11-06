"""The only code that reads or writes a cloud key, one Keychain account per provider.
Off macOS and in the tests it uses a stand-in in memory, and it never writes a key
anywhere else."""

import os
import sys

import keyring

from backend import build_mode, providers

SERVICE = "SecureFolio"
LEGACY_SERVICE = "DocSynth"  # SecureFolio's name before 2026-11-06
HINT_MASK = "••••"

_memory: dict[str, str] = {}


TEST_KEY_ENV = "SECUREFOLIO_TEST_KEY"  # Anthropic's; another provider's adds its id


def _test_key_env(provider: str) -> str:
    if provider == providers.ANTHROPIC:
        return TEST_KEY_ENV
    return f"{TEST_KEY_ENV}_{provider.upper()}"


def _use_stand_in() -> bool:
    return sys.platform != "darwin" or build_mode.uses_stub_models()


def _account(provider: str) -> str:
    return providers.PROVIDERS[provider].account


# `npm run eval` in development reads each provider's key from .env where it's set.
DEVELOPMENT_KEY_ENVS = {
    providers.ANTHROPIC: "ANTHROPIC_API_KEY",
    providers.OPENAI: "OPENAI_API_KEY",
    providers.OPENROUTER: "OPENROUTER_API_KEY",
}
_from_environment = {"on": False}


def prefer_environment() -> None:
    """For the evaluation in development: a provider's key from its variable in .env
    where it's set, the Keychain's otherwise. Never in a release build."""
    _from_environment["on"] = not build_mode.is_release()


def from_environment() -> bool:
    """Whether keys come from .env: the evaluation in development, never the app."""
    return _from_environment["on"]


def get_key(provider: str = providers.ANTHROPIC) -> str | None:
    account = _account(provider)
    if _use_stand_in():
        # The end-to-end tests' processes share a key through the environment.
        test_key = (
            os.environ.get(_test_key_env(provider))
            if not build_mode.is_release()
            else None
        )
        return _memory.get(account) or test_key
    if _from_environment["on"]:  # the evaluation in development, never the checks
        found = os.environ.get(DEVELOPMENT_KEY_ENVS[provider])
        if found:
            return found
    return keyring.get_password(SERVICE, account)


def set_key(key: str, provider: str = providers.ANTHROPIC) -> None:
    account = _account(provider)
    if _use_stand_in():
        _memory[account] = key
    else:
        keyring.set_password(SERVICE, account, key)


def delete_key(provider: str = providers.ANTHROPIC) -> None:
    account = _account(provider)
    if _use_stand_in():
        _memory.pop(account, None)
    elif keyring.get_password(SERVICE, account) is not None:
        keyring.delete_password(SERVICE, account)


def hint(provider: str = providers.ANTHROPIC) -> str | None:
    """The key's last four characters, and nothing else of it."""
    key = get_key(provider)
    return f"{HINT_MASK}{key[-4:]}" if key else None


def copy_legacy_keys() -> None:
    """Once, as DocSynth's library moves to SecureFolio's folder: each provider's key
    under DocSynth's Keychain service is copied to SecureFolio's, unless SecureFolio
    has one already. DocSynth's entries stay as they were."""
    if _use_stand_in():
        return
    for provider in providers.PROVIDERS:
        account = _account(provider)
        old = keyring.get_password(LEGACY_SERVICE, account)
        if old and keyring.get_password(SERVICE, account) is None:
            keyring.set_password(SERVICE, account, old)
