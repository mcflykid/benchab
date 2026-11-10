"""Chaque test dispose d'un atelier privé jetable, y compris les lectures par défaut."""

import ipaddress
import os
import socket

import pytest


@pytest.fixture(autouse=True)
def prive_jetable(tmp_path, monkeypatch, request):
    """Isole le privé et bloque les résolutions/connexions externes, sauf opt-in déclaré."""
    monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "prive"))
    monkeypatch.setenv("CADGEN_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setenv("CADGEN_STATE_DIR", str(tmp_path / "prive/cache/cadgen/etat"))
    monkeypatch.setenv("CADGEN_CACHE_DIR", str(tmp_path / "prive/cache/cadgen/cache"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "prive/cache/matplotlib"))

    # L'opt-in vient seulement de la fixture dédiée, jamais de l'environnement hôte.
    monkeypatch.setenv("BENCHAB_TEST_RESEAU_EXTERNE", "0")
    request.node.user_properties.append(("isolation_reseau", "externe bloqué, loopback autorisé"))

    natifs_reseau = {
        nom: getattr(objet, nom)
        for objet, noms in (
            (socket, ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo")),
            (socket.socket, ("connect", "connect_ex", "sendto", "sendmsg")),
        )
        for nom in noms if hasattr(objet, nom)
    }
    request.node._benchab_natifs_reseau = natifs_reseau

    def est_local(hote):
        """Reconnaît localhost et adresses IP loopback, sans résolution DNS externe."""
        if isinstance(hote, bytes):
            hote = hote.decode("ascii", errors="ignore")
        if isinstance(hote, str) and hote.rstrip(".").lower() in {"localhost", "localhost.localdomain"}:
            return True
        try:
            return ipaddress.ip_address(hote.split("%", 1)[0]).is_loopback
        except (ValueError, AttributeError):
            return False

    def hote_externe(hote):
        """Refuse les noms et adresses externes avant leur résolution par le système."""
        if os.environ.get("BENCHAB_TEST_RESEAU_EXTERNE") == "1":
            return False
        if hote is not None and not est_local(hote):
            raise RuntimeError(f"réseau externe bloqué par défaut dans les tests : résolution de {hote!r}")
        return False

    def getaddrinfo_isole(hote, *args, **kwargs):
        """Empêche DNS externe avant toute requête système ; conserve les noms locaux."""
        hote_externe(hote)
        return natifs_reseau["getaddrinfo"](hote, *args, **kwargs)

    def gethostbyname_isole(hote):
        """Protège la résolution IPv4 historique avant l'appel natif."""
        hote_externe(hote)
        return natifs_reseau["gethostbyname"](hote)

    def gethostbyname_ex_isole(hote):
        """Protège la résolution IPv4 détaillée avant l'appel natif."""
        hote_externe(hote)
        return natifs_reseau["gethostbyname_ex"](hote)

    def gethostbyaddr_isole(hote):
        """Protège la résolution inverse avant l'appel natif."""
        hote_externe(hote)
        return natifs_reseau["gethostbyaddr"](hote)

    def getnameinfo_isole(adresse, drapeaux):
        """Protège la résolution inverse d'adresse avant l'appel natif."""
        hote_externe(adresse[0] if adresse else None)
        return natifs_reseau["getnameinfo"](adresse, drapeaux)

    def connect_isole(sock, adresse):
        """Autorise AF_UNIX et loopback, refuse les destinations Internet avant connect()."""
        if os.environ.get("BENCHAB_TEST_RESEAU_EXTERNE") == "1":
            return natifs_reseau["connect"](sock, adresse)
        if sock.family != socket.AF_UNIX and isinstance(adresse, tuple) and adresse and not est_local(adresse[0]):
            raise RuntimeError(f"réseau externe bloqué par défaut dans les tests : connexion vers {adresse[0]!r}")
        return natifs_reseau["connect"](sock, adresse)

    def connect_ex_isole(sock, adresse):
        """Applique la même barrière aux connexions qui renvoient un code d'erreur."""
        if os.environ.get("BENCHAB_TEST_RESEAU_EXTERNE") == "1":
            return natifs_reseau["connect_ex"](sock, adresse)
        if sock.family != socket.AF_UNIX and isinstance(adresse, tuple) and adresse and not est_local(adresse[0]):
            raise RuntimeError(f"réseau externe bloqué par défaut dans les tests : connexion vers {adresse[0]!r}")
        return natifs_reseau["connect_ex"](sock, adresse)

    def sendto_isole(sock, donnees, *args, **kwargs):
        """Refuse l'envoi UDP externe avant émission, tout en gardant le loopback."""
        if os.environ.get("BENCHAB_TEST_RESEAU_EXTERNE") != "1":
            adresse = kwargs.get("address")
            if adresse is None:
                adresse = args[-1] if len(args) == 2 else args[0] if args else None
            if sock.family != socket.AF_UNIX and isinstance(adresse, tuple) and adresse and not est_local(adresse[0]):
                raise RuntimeError(f"réseau externe bloqué par défaut dans les tests : envoi vers {adresse[0]!r}")
        return natifs_reseau["sendto"](sock, donnees, *args, **kwargs)

    def sendmsg_isole(sock, buffers, *args, **kwargs):
        """Refuse sendmsg vers une destination externe avant émission du datagramme."""
        adresse = kwargs.get("address")
        if adresse is None and len(args) >= 3:
            adresse = args[-1]
        if os.environ.get("BENCHAB_TEST_RESEAU_EXTERNE") != "1":
            if adresse is not None and sock.family != socket.AF_UNIX and isinstance(adresse, tuple):
                hote_externe(adresse[0])
        return natifs_reseau["sendmsg"](sock, buffers, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo_isole)
    for nom, wrapper in (
        ("gethostbyname", gethostbyname_isole),
        ("gethostbyname_ex", gethostbyname_ex_isole),
        ("gethostbyaddr", gethostbyaddr_isole),
        ("getnameinfo", getnameinfo_isole),
    ):
        if nom in natifs_reseau:
            monkeypatch.setattr(socket, nom, wrapper)
    monkeypatch.setattr(socket.socket, "connect", connect_isole)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex_isole)
    monkeypatch.setattr(socket.socket, "sendto", sendto_isole)
    if "sendmsg" in natifs_reseau:
        monkeypatch.setattr(socket.socket, "sendmsg", sendmsg_isole)


@pytest.fixture
def reseau_natifs(prive_jetable, request):
    """Expose les appels natifs capturés pour vérifications factices sans trafic."""
    return request.node._benchab_natifs_reseau


@pytest.fixture
def reseau_externe_autorise(monkeypatch, request):
    """Opt-in explicite à demander par une recette qui doit réellement contacter Internet."""
    monkeypatch.setenv("BENCHAB_TEST_RESEAU_EXTERNE", "1")
    request.node.user_properties.append(("isolation_reseau", "opt-in externe explicite demandé"))


@pytest.fixture
def atelier_initialise(tmp_path):
    """Initialise explicitement la Base jetable des tests métier ; aucune unité ni commande machine.

    Opt-in : les tests d'atelier absent et d'aide restent sans cette fixture
    (docs/reference/chemins-et-atelier.md).
    """
    from benchab.donnees.base import Base

    base = Base(tmp_path / "prive", creer=True)
    try:
        return base.prive
    finally:
        base.fermer()
