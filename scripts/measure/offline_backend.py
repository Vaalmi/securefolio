"""Starts the backend with a stand-in key, for offline_sandbox.py: a key must exist for
a cut network to be tested at all, and this one is never accepted by anyone."""

from backend import keychain, main

keychain.get_key = lambda: "sk-ant-not-a-real-key-offline-sandbox"
main.main()
