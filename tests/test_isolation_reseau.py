"""Le réseau des tests est bloqué par défaut, avec accès loopback explicite."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import urlopen

import pytest


@pytest.mark.parametrize(
    ("nom", "arguments"),
    [
        ("getaddrinfo", ("example.com", 443)),
        ("gethostbyname", ("example.com",)),
        ("gethostbyname_ex", ("example.com",)),
        ("gethostbyaddr", ("203.0.113.1",)),
        ("getnameinfo", (("203.0.113.1", 443), socket.NI_NAMEREQD)),
    ],
)
def test_resolutions_externes_refusees_avant_appel_natif(nom, arguments, monkeypatch, reseau_natifs):
    """Toute API DNS connue échoue avant d'appeler la fonction native."""
    appele = False

    def natif_factice(*_args, **_kwargs):
        nonlocal appele
        appele = True
        return None

    if not hasattr(socket, nom):
        pytest.skip(f"socket.{nom} indisponible sur cette plateforme")
    monkeypatch.setitem(reseau_natifs, nom, natif_factice)
    with pytest.raises(RuntimeError, match="réseau externe bloqué par défaut"):
        getattr(socket, nom)(*arguments)
    assert not appele


def test_connexion_externe_refusee_avant_socket_connect():
    """Le contrôle de socket refuse une IP externe avant l'appel système."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        with pytest.raises(RuntimeError, match="réseau externe bloqué par défaut"):
            client.connect_ex(("203.0.113.1", 443))


@pytest.mark.skipif(not hasattr(socket.socket, "sendmsg"), reason="socket.sendmsg indisponible")
def test_sendmsg_udp_externe_refuse_avant_appel_natif(monkeypatch, reseau_natifs):
    """La variante vecteur de l'envoi UDP applique aussi l'isolation."""
    appele = False

    def natif_factice(*_args, **_kwargs):
        nonlocal appele
        appele = True
        return 1

    monkeypatch.setitem(reseau_natifs, "sendmsg", natif_factice)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(RuntimeError, match="réseau externe bloqué par défaut"):
            client.sendmsg([b"test"], [], 0, ("203.0.113.1", 53))
    finally:
        client.close()
    assert not appele


def test_optin_explicite_passe_aux_appels_natifs_factices(
    reseau_externe_autorise, monkeypatch, reseau_natifs,
):
    """La fixture d'opt-in débloque les wrappers vers des natifs factices, sans Internet."""
    appele = []
    monkeypatch.setitem(reseau_natifs, "gethostbyname", lambda hote: appele.append(hote) or "192.0.2.1")
    monkeypatch.setitem(reseau_natifs, "connect", lambda _sock, adresse: appele.append(adresse))

    assert socket.gethostbyname("test.invalid") == "192.0.2.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect(("203.0.113.1", 443))
    assert appele == ["test.invalid", ("203.0.113.1", 443)]


def test_connexion_visionneuse_locale_autorisee():
    """La boucle locale reste accessible aux tests des serveurs de visionneuse."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """Répond au contrôle local sans ressource externe."""
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            """Garde la sortie de test silencieuse."""
            pass

    serveur = HTTPServer(("127.0.0.1", 0), Handler)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        with urlopen(f"http://127.0.0.1:{serveur.server_port}/", timeout=2) as reponse:
            assert reponse.read() == b"ok"
    finally:
        serveur.shutdown()
        serveur.server_close()
        fil.join(timeout=2)
