"""`npm run verify:models`: each provider's key through this Mac's own Keychain, which
the checks stand in for. Never part of `npm run check`. It writes only under a service
of its own, never SecureFolio's, so the owner's saved keys are never read or touched."""

import sys
import uuid

import keyring
import pytest

from backend import keychain, providers

pytestmark = [
    pytest.mark.real_models,
    pytest.mark.skipif(sys.platform != "darwin", reason="the Keychain is macOS's"),
]


@pytest.fixture
def own_service(monkeypatch):
    service = f"SecureFolio test {uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(keychain, "SERVICE", service)
    monkeypatch.setattr(keychain, "_use_stand_in", lambda: False)
    yield service
    for provider in providers.PROVIDERS:
        account = providers.PROVIDERS[provider].account
        if keyring.get_password(service, account) is not None:
            keyring.delete_password(service, account)


@pytest.mark.parametrize("provider", list(providers.PROVIDERS))
def test_a_key_per_provider_round_trips_in_the_keychain(own_service, provider):
    key = f"sk-verify-{provider}-{uuid.uuid4().hex[:6]}-9Zk1"
    keychain.set_key(key, provider)
    assert (
        keyring.get_password(own_service, providers.PROVIDERS[provider].account) == key
    )
    assert keychain.get_key(provider) == key
    assert keychain.hint(provider) == "••••9Zk1"
    others = [p for p in providers.PROVIDERS if p != provider]
    assert all(keychain.get_key(other) is None for other in others)
    keychain.delete_key(provider)
    assert keychain.get_key(provider) is None
