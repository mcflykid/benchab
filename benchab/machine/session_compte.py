"""Compte Anycubic autonome : PKCE officiel, cache privé et renouvellement.

Implémentation stdlib du protocole décrit dans cloud-authentification-nexus.md.
Aucun code de service ni client tiers incorporé.
Le décodage JWT contrôle les claims, pas la signature : l'API Cloud valide le jeton.
Ni lecture de session slicer, ni commande imprimante, ni service persistant.
"""

import base64
import fcntl
import hashlib
import http.cookiejar
import json
import math
import os
import secrets
import ssl
import stat
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from benchab.chemins import atelier_prive, est_temporaire_hors_depot, sans_lien
from benchab.machine.cloud_lecture import CloudErreur, SansRedirection, _json, _octets

ISSUER = "https://uc.makeronline.com"
CLIENT = "b269a3136d26ac6488bb"
REDIRECT = "acnext://login"
WEEK = 7 * 86400
LIMIT = 65536
REPOSITORY = Path(__file__).resolve().parents[2]


def _location(path):
    try:
        value = sans_lien(Path(path).expanduser().absolute())
    except ValueError as erreur:
        raise CloudErreur("COMPTE_HORS_DEPOT_REQUIS") from erreur
    prive = atelier_prive()
    if not value.is_relative_to(prive) and not est_temporaire_hors_depot(value):
        raise CloudErreur("COMPTE_HORS_DEPOT_REQUIS")
    return value


def chemins():
    """Renvoie les chemins de compte explicitement configurables, sans création ni lecture."""
    base = atelier_prive() / "config/anycubic"
    return (
        Path(os.environ.get("BENCHAB_ANYCUBIC_ACCOUNT_FILE", base / "anycubic-compte.json")),
        Path(os.environ.get("BENCHAB_ANYCUBIC_SESSION_FILE", base / "anycubic-session.json")),
    )


def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


@contextmanager
def _parent(path, create=False):
    """Parcours openat sans symlink ; jamais chmod d'un dossier existant."""
    path = _location(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, 448, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield (fd, path.name)
    finally:
        os.close(fd)


def _private(info, directory=False):
    regular = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not regular
        or info.st_uid != os.getuid()
        or info.st_mode & 63
        or (not directory and (stat.S_IMODE(info.st_mode) != 384 or info.st_nlink != 1 or info.st_size > LIMIT))
    ):
        raise CloudErreur("FICHIER_COMPTE_NON_PRIVE")


def _read(path, absent=False):
    try:
        with _parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _private(before)
            raw = stream.read(LIMIT + 1)
            after = os.fstat(stream.fileno())

        def identity(x):
            return (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)

        if len(raw) > LIMIT or identity(before) != identity(after):
            raise CloudErreur("FICHIER_COMPTE_INSTABLE")
        value = _json(raw)
        if not isinstance(value, dict):
            raise CloudErreur("FICHIER_COMPTE_INVALIDE")
        return value
    except FileNotFoundError:
        if absent:
            return {}
        raise CloudErreur("CONFIGURATION_COMPTE_REQUISE") from None
    except OSError:
        raise CloudErreur("FICHIER_COMPTE_INACCESSIBLE") from None


def _write(path, value, replace=True):
    with _parent(path, create=True) as (parent, name):
        _private(os.fstat(parent), directory=True)
        try:
            previous = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            _private(previous)
            if not replace:
                raise CloudErreur("COMPTE_EXISTANT_CONSERVE")
        temp = "." + name + "." + secrets.token_hex(12)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384, dir_fd=parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.unlink(temp, dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass


def configurer(email, password, path=None):
    if (
        not isinstance(email, str)
        or not 3 <= len(email) <= 320
        or "@" not in email
        or any((ord(c) < 33 for c in email))
    ):
        raise CloudErreur("IDENTIFIANTS_COMPTE_INVALIDES")
    if not isinstance(password, str) or not 1 <= len(password) <= 4096 or "\x00" in password:
        raise CloudErreur("IDENTIFIANTS_COMPTE_INVALIDES")
    _write(path or chemins()[0], {"email": email, "password": password}, replace=False)


def claims(token):
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 16384 or len(token.split(".")) != 3:
            return {}
        body = token.split(".")[1]
        value = _json(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
        if not isinstance(value, dict) or value.get("iss") != ISSUER or value.get("tokenType") != "access-token":
            return {}
        if (
            not isinstance(value.get("sub"), str)
            or not 1 <= len(value["sub"]) <= 512
            or (not _number(value.get("exp")))
        ):
            return {}
        return value
    except (ValueError, UnicodeError):
        return {}


def etat():
    """Diagnostic sans lecture du contenu, connexion ou accès au slicer."""
    path, _ = chemins()
    try:
        with _parent(path) as (parent, name):
            _private(os.stat(name, dir_fd=parent, follow_symlinks=False))
        state = "COMPTE_CONFIGURE_NON_VALIDE"
    except (OSError, CloudErreur):
        state = "CONFIGURATION_COMPTE_REQUISE"
    return {
        "statut": state,
        "authentification_autonome": True,
        "validation_reelle": False,
        "session_slicer_inspectee": False,
        "commandes_machine": False,
    }


class SessionCompte:
    def __init__(self, compte=None, session=None, *, opener=None, clock=None):
        defaults = chemins()
        self._compte = _location(compte or defaults[0])
        self._session = _location(session or defaults[1])
        if self._compte == self._session:
            raise CloudErreur("CHEMINS_COMPTE_SESSION_IDENTIQUES")
        self._clock = clock or time.time
        self.renouvele = False
        self._open = (
            opener
            or urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                SansRedirection(),
                urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            ).open
        )

    def __repr__(self):
        return "<SessionCompte Anycubic, secrets masques>"

    def __reduce__(self):
        raise TypeError("Session non serialisable")

    def _post(self, route, value, *, query=None, form=False):
        if route not in ("/api/login", "/api/login/oauth/access_token", "/api/login/oauth/refresh_token"):
            raise CloudErreur("ROUTE_AUTH_INTERDITE")
        url = ISSUER + route + ("?" + urllib.parse.urlencode(query) if query else "")
        data = urllib.parse.urlencode(value).encode() if form else json.dumps(value).encode()
        content = "application/x-www-form-urlencoded" if form else "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": content, "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        result = _json(_octets(request, self._open, LIMIT))
        if not isinstance(result, dict):
            raise CloudErreur("REPONSE_AUTH_INVALIDE")
        return result

    def _login(self, account):
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        response = self._post(
            "/api/login",
            {
                "application": "ac_slicer_next",
                "organization": "anycubic",
                "username": account["email"],
                "password": account["password"],
                "type": "code",
                "autoSignin": True,
            },
            query={
                "clientId": CLIENT,
                "responseType": "code",
                "redirectUri": REDIRECT,
                "type": "code",
                "scope": "read",
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            },
        )
        if response.get("state") is not None and response["state"] != state:
            raise CloudErreur("ETAT_PKCE_INCOHERENT")
        code = response.get("data")
        if response.get("status") != "ok" or not isinstance(code, str) or (not 1 <= len(code) <= 2048):
            raise CloudErreur("AUTHENTIFICATION_COMPTE_REFUSEE")
        return self._post(
            "/api/login/oauth/access_token",
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT,
            },
            form=True,
        )

    def _usable(self, value):
        c = claims(value.get("access_token"))
        expiry = value.get("expires_at", 0)
        return bool(c) and _number(expiry) and (min(c["exp"], expiry) > self._clock() + 300)

    def _accept(self, response, old, fingerprint):
        c = claims(response.get("access_token"))
        if not c or c["exp"] <= self._clock() + 300:
            raise CloudErreur("JETON_COMPTE_INVALIDE")
        if old.get("subject") and c["sub"] != old["subject"]:
            raise CloudErreur("COMPTE_DIFFERENT_REFUSE")
        expiry = c["exp"]
        seconds = response.get("expires_in")
        if seconds is not None:
            if not _number(seconds) or seconds <= 300:
                raise CloudErreur("EXPIRATION_COMPTE_INVALIDE")
            expiry = min(expiry, self._clock() + seconds)
        refresh = response.get("refresh_token", old.get("refresh_token"))
        if refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 16384):
            raise CloudErreur("JETON_RENOUVELLEMENT_INVALIDE")
        return {
            "access_token": response["access_token"],
            "refresh_token": refresh,
            "obtained_at": self._clock(),
            "expires_at": expiry,
            "subject": c["sub"],
            "account_hash": fingerprint,
            "retry_after": 0,
        }

    def jeton(self, *, force=False):
        """Une tentative de refresh puis au plus un PKCE ; aucun rejeu machine."""
        self.renouvele = False
        with _parent(self._session, create=True) as (parent, name):
            _private(os.fstat(parent), directory=True)
            fd = os.open(name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 384, dir_fd=parent)
        with os.fdopen(fd, "r+") as lock:
            _private(os.fstat(lock.fileno()))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CloudErreur("CONNEXION_COMPTE_EN_COURS") from None
            account = _read(self._compte)
            if (
                not isinstance(account.get("email"), str)
                or not isinstance(account.get("password"), str)
                or (not account["password"])
            ):
                raise CloudErreur("IDENTIFIANTS_COMPTE_INVALIDES")
            fingerprint = hashlib.sha256(account["email"].strip().casefold().encode()).hexdigest()
            current = _read(self._session, absent=True)
            if current.get("account_hash", fingerprint) != fingerprint:
                raise CloudErreur("COMPTE_DIFFERENT_REFUSE")
            valid = self._usable(current) and (not force)
            now = self._clock()
            obtained = current.get("obtained_at", 0)
            retry = current.get("retry_after", 0)
            if not _number(obtained) or not _number(retry):
                raise CloudErreur("SESSION_COMPTE_INVALIDE")
            if valid and now - obtained <= WEEK:
                return current["access_token"]
            if now < retry:
                if valid:
                    return current["access_token"]
                raise CloudErreur("CONNEXION_COMPTE_TEMPORISEE")
            try:
                response = None
                if current.get("refresh_token"):
                    try:
                        response = self._post(
                            "/api/login/oauth/refresh_token",
                            {
                                "grant_type": "refresh_token",
                                "refresh_token": current["refresh_token"],
                                "scope": "read",
                                "client_id": CLIENT,
                            },
                        )
                        candidate = self._accept(response, current, fingerprint)
                    except CloudErreur as exc:
                        if str(exc) == "COMPTE_DIFFERENT_REFUSE":
                            raise
                        response = None
                if response is None:
                    candidate = self._accept(self._login(account), current, fingerprint)
                _write(self._session, candidate)
                self.renouvele = True
                return candidate["access_token"]
            except CloudErreur as exc:
                if str(exc) == "COMPTE_DIFFERENT_REFUSE":
                    raise
                failed = {**current, "retry_after": now + 300, "account_hash": fingerprint}
                if force:
                    failed["expires_at"] = 0
                _write(self._session, failed)
                if valid:
                    return current["access_token"]
                raise CloudErreur("CONNEXION_COMPTE_INDISPONIBLE") from None


def main(argv=None):
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configurer", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.configurer:
            if not sys.stdin.isatty():
                raise CloudErreur("TERMINAL_INTERACTIF_REQUIS")
            configurer(input("E-mail Anycubic : ").strip(), getpass.getpass("Mot de passe (masque) : "))
        print(json.dumps(etat(), ensure_ascii=False))
        return 0
    except (OSError, ValueError):
        print(json.dumps({"statut": "CONFIGURATION_COMPTE_REFUSEE", "commandes_machine": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
