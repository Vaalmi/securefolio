"""The network blocker and the Offline guard do what the privacy tests rely on."""

import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from backend import netguard
from testing import network
from testing.network import OffMacConnection
from testing.stub_models import stub_anthropic

OFF_THE_MAC = ("192.0.2.1", 80)


def test_a_connection_off_the_mac_fails_the_test():
    with pytest.raises(OffMacConnection):
        socket.create_connection(OFF_THE_MAC, timeout=0.1)
    assert network.deactivate()  # the teardown would have failed on it


def test_connect_ex_a_datagram_and_a_name_lookup_are_all_caught():
    with socket.socket() as stream, pytest.raises(OffMacConnection):
        stream.connect_ex(OFF_THE_MAC)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram,
        pytest.raises(OffMacConnection),
    ):
        datagram.sendto(b"x", OFF_THE_MAC)
    with pytest.raises(OffMacConnection):
        socket.getaddrinfo("example.com", 443)
    assert len(network.deactivate()) == 3


def test_a_loopback_connection_is_allowed():
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=1):
        pass
    server.server_close()


def test_the_stub_records_what_the_client_sends():
    with stub_anthropic() as stub:
        body = {"model": "m", "messages": ["planted"]}
        httpx.post(f"{stub.url}/v1/messages", json=body)
    assert "planted" in stub.all_text()


def test_the_blocker_survives_the_app_installing_its_guard(app_client):
    assert app_client.get("/api/health").is_success
    with pytest.raises(OffMacConnection):
        socket.create_connection(OFF_THE_MAC, timeout=0.1)
    assert network.deactivate()


class TestOfflineGuard:
    """With the test blocker off, so only the app's own guard is at work."""

    @pytest.fixture(autouse=True)
    def offline(self):
        network.deactivate()
        netguard.install(lambda: True)
        yield
        netguard.install(lambda: False)

    def test_it_refuses_every_way_off_the_mac(self):
        with pytest.raises(netguard.OfflineRefused):
            socket.create_connection(OFF_THE_MAC, timeout=0.1)
        with socket.socket() as stream, pytest.raises(netguard.OfflineRefused):
            stream.connect_ex(OFF_THE_MAC)
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram,
            pytest.raises(netguard.OfflineRefused),
        ):
            datagram.sendto(b"x", OFF_THE_MAC)
        with pytest.raises(netguard.OfflineRefused):
            socket.getaddrinfo("example.com", 443)

    def test_it_refuses_a_program_that_could_reach_the_network(self):
        with pytest.raises(netguard.OfflineRefused):
            subprocess.run(["curl", "https://example.com"], check=False)  # noqa: S607

    def test_it_refuses_another_interpreter_but_not_the_worker(self):
        with pytest.raises(netguard.OfflineRefused):
            subprocess.run([sys.executable, "-c", "pass"], check=False)
        worker = (sys.executable, [sys.executable, "-m", "worker.main"], None, None)
        assert netguard.leaves_the_mac("subprocess.Popen", worker) is None
        assert netguard.leaves_the_mac(
            "subprocess.Popen", ("open", ["open", "x"], None, None)
        )
        assert netguard.leaves_the_mac("os.system", ("python3 -V",))

    @pytest.mark.parametrize(
        ("program", "arguments", "allowed"),
        [
            ("/usr/bin/caffeinate", ["caffeinate", "-i", "-w", "42"], True),
            ("/usr/bin/caffeinate", ["caffeinate", "/usr/bin/curl", "x"], False),
            ("caffeinate", ["caffeinate", "-i", "-w", "42"], False),
            ("/usr/bin/pmset", ["pmset", "-g", "batt"], True),
            ("/usr/sbin/taskpolicy", ["taskpolicy", "-b", "-p", "42"], True),
            ("/usr/sbin/taskpolicy", ["taskpolicy", "-c", "utility", "-p", "42"], True),
            ("/usr/sbin/taskpolicy", ["taskpolicy", "-c", "utility", "curl"], False),
        ],
    )
    def test_system_tools_only_in_forms_that_run_nothing_else(
        self, program, arguments, allowed
    ):
        spawn = (program, arguments, None, None)
        assert (netguard.leaves_the_mac("subprocess.Popen", spawn) is None) is allowed

    def test_it_refuses_a_reverse_lookup(self):
        with pytest.raises(netguard.OfflineRefused):
            socket.getnameinfo(OFF_THE_MAC, 0)

    def test_it_allows_loopback(self):
        with stub_anthropic() as stub:
            assert httpx.get(f"{stub.url}/x").is_success
        assert socket.getaddrinfo("localhost", 80)


def test_the_guard_lets_everything_through_when_offline_is_off():
    netguard.install(lambda: False)
    netguard._hook("socket.connect", (None, OFF_THE_MAC))


def test_a_unix_socket_and_an_empty_address_stay_on_the_mac():
    assert netguard.leaves_the_mac("socket.connect", (None, "/x")) is None
    assert netguard.leaves_the_mac("socket.sendto", (None, None)) is None
    assert netguard.leaves_the_mac("socket.getaddrinfo", (None, 80)) is None
    assert netguard.leaves_the_mac("socket.getnameinfo", (("127.0.0.1", 80), 0)) is None
    assert netguard.leaves_the_mac("socket.getaddrinfo", ("bad name", 80))
