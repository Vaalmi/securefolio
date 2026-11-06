"""The key lives behind one module, and never in a file of the vault."""

from backend import keychain, vault


def test_the_key_round_trips_and_stays_out_of_the_vault():
    keychain.set_key("sk-ant-planted-secret-7Qx2")
    assert keychain.get_key() == "sk-ant-planted-secret-7Qx2"
    assert keychain.hint() == "••••7Qx2"
    for path in vault.root().rglob("*"):
        if path.is_file():
            assert b"planted-secret" not in path.read_bytes()
    keychain.delete_key()
    assert keychain.get_key() is None
    assert keychain.hint() is None
