"""Cloud Anycubic international, acquisition explicite et lecture seule.

Aucun accès au compte ou à sa configuration sans demande explicite. Aucun MQTT,
commande machine, caméra, téléchargement de G-code ou écriture de profil.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from benchab.machine import telemetrie

API = "https://cloud-universe.anycubic.com/p/p/workbench/api"
REVISION = "ad9022649930c7cbfb73854393163b9409ad8809"
PUBLIC_REVISION = "5663f5bf33584a5cf9abcdf6ff53acca924934bd"
PUBLIC_URL = (
    "https://raw.githubusercontent.com/Nino6689/anycubic-cloud-api/"
    + PUBLIC_REVISION
    + "/src/anycubic_cloud_api/const/const.py"
)
PUBLIC_SHA256 = "f98bdd821d441356f7fdd15dd4e56652cca191f378de45c36366de777c48a567"
SOURCE = "ioBroker.anycubic-cloud@" + REVISION
LIMIT = 1000000
TIMEOUT = 10
ENV_TOKEN = "BENCHAB_ANYCUBIC_ACCESS_TOKEN"


class CloudErreur(ValueError):
    """Code stable uniquement : ne restitue ni réponse distante ni identifiants."""


class SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudErreur("REDIRECTION_REFUSEE")


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SansRedirection(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    ).open


def _octets(request, opener, maximum=LIMIT, *, auth_http=False):
    try:
        with opener(request, timeout=TIMEOUT) as response:
            if response.geturl() != request.full_url:
                raise CloudErreur("REDIRECTION_REFUSEE")
            if auth_http and response.status in (401, 403):
                raise CloudErreur("AUTHENTIFICATION_REFUSEE")
            if response.status != 200:
                raise CloudErreur("REPONSE_HTTP_REFUSEE")
            size = response.headers.get("Content-Length")
            if size is not None and (not str(size).isdigit() or int(size) > maximum):
                raise CloudErreur("REPONSE_TROP_VOLUMINEUSE")
            result = bytearray()
            deadline = time.monotonic() + 25
            while True:
                if time.monotonic() > deadline:
                    raise CloudErreur("DELAI_DEPASSE")
                chunk = response.read1(min(65536, maximum + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > maximum:
                    raise CloudErreur("REPONSE_TROP_VOLUMINEUSE")
            return bytes(result)
    except CloudErreur:
        raise
    except urllib.error.HTTPError as exc:
        if auth_http and exc.code in (401, 403):
            raise CloudErreur("AUTHENTIFICATION_REFUSEE") from None
        raise CloudErreur("CLOUD_INDISPONIBLE") from None
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        raise CloudErreur("CLOUD_INDISPONIBLE") from None


def _json(raw):

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CloudErreur("JSON_INVALIDE")
            result[key] = value
        return result

    try:
        return json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(CloudErreur("JSON_INVALIDE"))
        )
    except (ValueError, UnicodeError, RecursionError):
        raise CloudErreur("JSON_INVALIDE") from None


def identite_application(opener=None):
    """Lit trois constantes publiques à une révision ET une empreinte figées, sans exécuter Python."""
    raw = _octets(urllib.request.Request(PUBLIC_URL, method="GET"), opener or _opener(), 65536)
    if hashlib.sha256(raw).hexdigest() != PUBLIC_SHA256:
        raise CloudErreur("IDENTITE_APPLICATION_ALTEREE")
    wanted = {"AC_KNOWN_AID": "app_id", "AC_KNOWN_SEC": "app_secret", "AC_KNOWN_VID_SLICER_NEXT": "version"}
    values = {}
    try:
        for node in ast.parse(raw).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and (type(node.value.value) is str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in wanted:
                        values[wanted[target.id]] = node.value.value
    except (SyntaxError, ValueError):
        raise CloudErreur("IDENTITE_APPLICATION_INVALIDE") from None
    if len(values) != 3:
        raise CloudErreur("IDENTITE_APPLICATION_INVALIDE")
    return values


def _local_json(path):
    """Petit JSON fourni explicitement, chaque ancêtre ouvert sans suivre de lien."""
    path = Path(path).absolute()
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
            raise CloudErreur("FICHIER_INVALIDE")
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    if len(raw) > LIMIT or identity(before) != identity(after):
        raise CloudErreur("FICHIER_INSTABLE")
    result = _json(raw)
    if not isinstance(result, dict):
        raise CloudErreur("JSON_INVALIDE")
    return result


def _copie_session(path):
    """Seule une copie explicitement fournie dans l'espace privé est lisible."""
    from benchab.machine.session_compte import _location

    if path is None:
        raise CloudErreur("COPIE_SESSION_EXPLICITE_REQUISE")
    return _location(path)


def session_slicer(path=None):
    """Inspection des clés seulement ; aucune décryption, extraction ou recherche en mémoire."""
    if path is None:
        raise CloudErreur("COPIE_SESSION_EXPLICITE_REQUISE")
    try:
        path = _copie_session(path)
        data = _local_json(path)
        cloud = data.get("anycubic_cloud")
        if not isinstance(cloud, dict):
            return {"statut": "SECTION_CLOUD_ABSENTE", "reutilisable": False}
        if not cloud:
            return {"statut": "SECTION_CLOUD_VIDE", "reutilisable": False}
        if isinstance(cloud.get("access_token"), str) and cloud["access_token"]:
            return {"statut": "SESSION_LISIBLE_SUR_DEMANDE", "reutilisable": True}
        return {
            "statut": "SESSION_OPAQUE_EXTRACTION_EXPLICITE_REQUISE",
            "reutilisable": False,
            "qualification_version": "NON_FAITE",
            "dechiffrement_effectue": False,
        }
    except (ValueError, OSError, TypeError, CloudErreur):
        return {"statut": "CONFIGURATION_ABSENTE_OU_INVALIDE", "reutilisable": False}


def token_slicer(path=None, dechiffrer=False):
    """Réutilisation explicite ; déchiffrement local seulement avec opt-in distinct."""
    path = _copie_session(path)
    data = _local_json(path)
    cloud = data.get("anycubic_cloud")
    token = cloud.get("access_token") if isinstance(cloud, dict) else None
    if not _secret(token) and dechiffrer:
        from benchab.machine.session_slicer import extraire

        token = extraire(cloud)
    if not _secret(token):
        raise CloudErreur("SESSION_CHIFFREE_OU_JETON_ABSENT")
    return token


def _secret(value):
    return isinstance(value, str) and 0 < len(value) <= 16384 and all((33 <= ord(c) <= 126 for c in value))


def etat(root=None):
    """État de configuration, sans réseau ni lecture implicite du fichier de compte."""
    from benchab.machine.session_compte import etat as etat_compte

    account = etat_compte()
    configured = account["statut"] == "COMPTE_CONFIGURE_NON_VALIDE"
    return {
        "statut": "CONFIGURE_NON_VALIDE"
        if configured or _secret(os.environ.get(ENV_TOKEN))
        else "AUTHENTIFICATION_REQUISE",
        "mode": "LECTURE_SEULE",
        "validation_reelle": False,
        "commandes_machine": False,
        "session_slicer_inspectee": False,
        "compte_autonome": account,
        "mode_authentification": "COMPTE_AUTONOME" if configured else "JETON_EXPLICITE_REQUIS",
    }


def _id(value):
    if isinstance(value, str) and re.fullmatch("[1-9][0-9]{0,18}", value):
        value = int(value)
    if type(value) is not int or not 0 < value < 2**63:
        raise CloudErreur("MACHINE_INVALIDE")
    return value


def _date(clock):
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise CloudErreur("HORLOGE_INVALIDE")
    return now.astimezone(timezone.utc).isoformat()


def _champ(value, source, recu):
    return {
        "valeur": value,
        "qualite": "rapporte_age_source_inconnu" if value is not None else "non_rapporte",
        "source": source,
        "recu_a": recu,
        "mesure_a": None,
        "age_source_secondes": None,
        "frais": None,
    }


def projeter(detail, printer_id, *, recu_a=None, origine="INSTANTANE_LOCAL_NON_AUTHENTIFIE"):
    """Projection allowlist ; l'heure de réception ne devient jamais l'heure de mesure."""
    wanted = _id(printer_id)
    if not isinstance(detail, dict) or telemetrie._integer(detail.get("id")) != wanted:
        raise CloudErreur("REPONSE_MACHINE_INCOHERENTE")
    recu = recu_a or datetime.now(timezone.utc).isoformat()
    raw_job = detail.get("project")
    raw_job = raw_job if isinstance(raw_job, dict) else {}
    job = telemetrie.project_job(
        {**raw_job, "settings": {"curr_layer": raw_job.get("curr_layer"), "total_layers": raw_job.get("total_layers")}}
    )
    stats = telemetrie.project_statistics(detail)
    param = detail.get("parameter")
    param = param if isinstance(param, dict) else {}
    version = detail.get("version")
    version = version if isinstance(version, dict) else {}
    firmware = version.get("firmware_version")
    if not isinstance(firmware, str) or not re.fullmatch("[A-Za-z0-9_.+-]{1,80}", firmware):
        firmware = None
    online = detail.get("device_status")
    connected = online == 1 if type(online) is int and online in (0, 1) else None
    values = {
        "connectee": connected,
        "firmware": firmware,
        "etat_impression": job["state"] if job["state"] != "unknown" else None,
        "progression_pourcent": job["progress_percent"],
        "reste_secondes": job["remaining_seconds"],
        "duree_secondes": job["elapsed_seconds"],
        "couche_courante": job["current_layer"],
        "couches_totales": job["total_layers"],
        "buse_c": telemetrie._number(param.get("curr_nozzle_temp"), maximum=500),
        "plateau_c": telemetrie._number(param.get("curr_hotbed_temp"), maximum=200),
        "nombre_impressions": stats["print_count"],
        "duree_totale_secondes": stats["total_duration_seconds"],
        "filament_total_kg": stats["total_filament_kg"],
    }
    boxes = telemetrie.project_ace(detail.get("multi_color_box"))
    for box in boxes:
        box["temperature_c"] = None
        box["humidity_percent"] = None
        box.update(source=SOURCE + ":printer/info.multi_color_box", mesure_a=None, age_source_secondes=None, frais=None)
        for slot in box["slots"]:
            reported = slot.get("remaining_percent")
            source = slot.get("information_source")
            slot["disponibilite"] = {
                "pourcentage_rapporte": reported,
                "nature": "declaration_manuelle_cloud"
                if source == "manual_entry_reported"
                else "estimation_cloud"
                if reported is not None
                else "non_rapporte",
                "masse_disponible_g": None,
                "validation_physique": False,
                "fraicheur_source_connue": False,
            }
            slot["sku"] = None
            material = slot.get("material")
            slot["material"] = (
                material.upper()
                if isinstance(material, str)
                and material.upper()
                in {
                    "PLA",
                    "PLA+",
                    "PETG",
                    "ABS",
                    "ASA",
                    "TPU",
                    "PA",
                    "PC",
                    "PVA",
                    "HIPS",
                    "PET",
                    "PCTG",
                    "PA-CF",
                    "PLA-CF",
                    "PETG-CF",
                }
                else None
            )
            if slot["information_source"] == "manual_entry_reported":
                slot["remaining_percent"] = None
                slot["sku"] = None
            slot["rfid_uid"] = None
    return {
        "schema": "benchab.cloud.lecture.v1",
        "statut": "RAPPORT_RECU" if origine == "CLOUD_TLS" else "INSTANTANE_FOURNI",
        "origine": origine,
        "source_protocole": SOURCE,
        "validation_reelle": False,
        "acquisition_reseau": origine == "CLOUD_TLS",
        "validation_physique": False,
        "commandes_machine": False,
        "donnees": {k: _champ(v, SOURCE + ":printer/info", recu) for k, v in values.items()},
        "ace": boxes,
        "limites": [
            "Fraicheur des mesures source inconnue ; reception REST distincte de la mesure.",
            "Disponibilite ACE : pourcentage rapporte seulement, sans masse pesee ni inventaire de toutes les bobines.",
            "Temperature et humidite ACE REST non qualifiees et masquees.",
            "Aucun nom de compte, identifiant machine, nom de fichier, URL ou jeton restitue.",
            "Aucune commande, camera ou validation physique.",
        ],
    }


class CloudLecture:
    def __init__(self, access_token, app_id, app_secret, version, *, opener=None, clock=None):
        if not all((_secret(v) for v in (access_token, app_id, app_secret, version))):
            raise CloudErreur("CONFIGURATION_CLOUD_INCOMPLETE")
        self._access = access_token
        self._aid = app_id
        self._secret = app_secret
        self._version = version
        self._transport_injecte = opener is not None
        self._open = opener or _opener()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token = None

    def __repr__(self):
        return "<CloudLecture lecture seule, secrets masques>"

    def __reduce__(self):
        raise TypeError("Client Cloud non serialisable")

    def _requete(self, route, *, machine=None, auth=False):
        if route not in ("/v3/public/loginWithAccessToken", "/v2/printer/info", "/work/printer/getPrinters"):
            raise CloudErreur("ROUTE_INTERDITE")
        if auth != (route == "/v3/public/loginWithAccessToken"):
            raise CloudErreur("ROUTE_INTERDITE")
        ts = str(int(self._clock().timestamp() * 1000))
        nonce = str(uuid.uuid4())
        signature = hashlib.md5(
            (self._aid + ts + self._version + self._secret + nonce + self._aid).encode()
        ).hexdigest()
        headers = {
            "Xx-Device-Type": "pcf",
            "Xx-Is-Cn": "1",
            "Xx-Nonce": nonce,
            "Xx-Signature": signature,
            "Xx-Timestamp": ts,
            "Xx-Version": self._version,
            "XX-LANGUAGE": "US",
            "Content-Type": "application/json",
        }
        if not auth:
            if not _secret(self._token):
                raise CloudErreur("AUTHENTIFICATION_REQUISE")
            headers["XX-Token"] = self._token
        url = API + route
        if route == "/v2/printer/info":
            url += "?" + urllib.parse.urlencode({"id": _id(machine)})
        elif machine is not None:
            raise CloudErreur("ROUTE_INTERDITE")
        payload = {"device_type": "pcf"}
        if auth:
            payload["access_token"] = self._access
        body = json.dumps(payload).encode() if auth else None
        result = _json(
            _octets(
                urllib.request.Request(url, data=body, headers=headers, method="POST" if auth else "GET"),
                self._open,
                auth_http=True,
            )
        )
        if isinstance(result, dict) and type(result.get("code")) is int and (result["code"] == 10001):
            raise CloudErreur("AUTHENTIFICATION_REFUSEE")
        expected = list if route == "/work/printer/getPrinters" else dict
        if (
            not isinstance(result, dict)
            or type(result.get("code")) is not int
            or result["code"] not in (0, 1, 200)
            or (not isinstance(result.get("data"), expected))
        ):
            raise CloudErreur("SERVICE_REFUSE_OU_SCHEMA_INCONNU")
        return result["data"]

    def _authentifier(self):
        _date(self._clock)
        login = self._requete("/v3/public/loginWithAccessToken", auth=True)
        if not _secret(login.get("token")):
            raise CloudErreur("SERVICE_REFUSE_OU_SCHEMA_INCONNU")
        self._token = login["token"]

    def _masquer(self, value):
        if isinstance(value, dict):
            return {k: self._masquer(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._masquer(v) for v in value]
        if isinstance(value, str):
            for secret in (self._access, self._aid, self._secret, self._token):
                if secret and secret in value:
                    value = value.replace(secret, "[MASQUE]")
        return value

    def lister_machines(self):
        """Découverte explicite : ID de sélection et modèle ; aucun nom privé ni clé appareil."""
        try:
            self._authentifier()
            return self._lister_authentifie()
        finally:
            self._token = None

    def _lister_authentifie(self):
        rows = self._requete("/work/printer/getPrinters")
        if len(rows) > 100:
            raise CloudErreur("LISTE_MACHINES_INVALIDE")
        machines = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise CloudErreur("LISTE_MACHINES_INVALIDE")
            ident = _id(row.get("id"))
            if ident in seen:
                raise CloudErreur("LISTE_MACHINES_INVALIDE")
            seen.add(ident)
            model = telemetrie._integer(row.get("machine_type"))
            online = row.get("device_status")
            machines.append(
                {
                    "id": ident,
                    "modele_id": model,
                    "modele": "Anycubic Kobra S1" if telemetrie.capabilities_model(model)["model_qualified"] else None,
                    "connectee": online == 1 if type(online) is int and online in (0, 1) else None,
                    "source": SOURCE + ":getPrinters",
                }
            )
        return self._masquer(
            {
                "schema": "benchab.cloud.machines.v1",
                "statut": "CHOISIR_MACHINE" if machines else "AUCUNE_MACHINE_RAPPORTEE",
                "machines": machines,
                "origine": "TRANSPORT_INJECTE" if self._transport_injecte else "CLOUD_TLS",
                "validation_reelle": False,
                "commandes_machine": False,
                "limite": (
                    "Identifiants de sélection du compte explicitement consulté ; présenc"
                    "e et fraîcheur physiques non établies."
                ),
            }
        )

    def _relever_authentifie(self, wanted):
        result = projeter(
            self._requete("/v2/printer/info", machine=wanted),
            wanted,
            recu_a=_date(self._clock),
            origine="TRANSPORT_INJECTE" if self._transport_injecte else "CLOUD_TLS",
        )
        result["validation_connexion_reelle"] = not self._transport_injecte
        return self._masquer(result)

    def relever_notre_machine(self):
        """Une authentification puis découverte et lecture seulement si cible unique qualifiée."""
        try:
            self._authentifier()
            listing = self._lister_authentifie()
            machines = listing["machines"]
            if len(machines) != 1 or machines[0]["modele"] != "Anycubic Kobra S1":
                listing["selection_automatique"] = False
                listing["raison_selection"] = "CIBLE_UNIQUE_KOBRA_S1_NON_ETABLIE"
                return listing
            result = self._relever_authentifie(machines[0]["id"])
            result["selection_automatique"] = True
            result["selection"] = "UNIQUE_MACHINE_KOBRA_S1_DECOUVERTE"
            return result
        finally:
            self._token = None

    def relever(self, printer_id):
        wanted = _id(printer_id)
        try:
            self._authentifier()
            return self._relever_authentifie(wanted)
        finally:
            self._token = None


def relever_avec_jeton(token, machine=None, *, opener=None, identite=None, clock=None, notre_machine=False):
    """Point d'entrée UI explicite ; sans machine, présente seulement la liste à choisir.

    Ne conserve ni jeton, ni client, ni réponse brute dans un registre global.
    Le transport/identité injectés servent aux tests et ne viennent jamais du JSON HTTP.
    """
    if not _secret(token):
        raise CloudErreur("AUTHENTIFICATION_REQUISE")
    if machine is not None:
        machine = _id(machine)
    if type(notre_machine) is not bool or (notre_machine and machine is not None):
        raise CloudErreur("SELECTION_MACHINE_INVALIDE")
    identity = identite_application(opener) if identite is None else identite
    if not isinstance(identity, dict) or set(identity) != {"app_id", "app_secret", "version"}:
        raise CloudErreur("IDENTITE_APPLICATION_INCOMPLETE")
    client = CloudLecture(token, **identity, opener=opener, clock=clock)
    if notre_machine:
        return client.relever_notre_machine()
    return client.lister_machines() if machine is None else client.relever(machine)


def relever_avec_compte(machine=None, *, session=None, opener=None, identite=None, clock=None, notre_machine=False):
    """Connexion autonome explicite ; aucun accès à la session du slicer.

    Renouvellement après expiration/échéance ou un refus d'authentification connu.
    Les seuls parcours rejouables sont l'authentification et les lectures REST.
    """
    from benchab.machine.session_compte import SessionCompte

    account = session or SessionCompte()
    token = account.jeton()
    try:
        result = relever_avec_jeton(
            token, machine, opener=opener, identite=identite, clock=clock, notre_machine=notre_machine
        )
    except CloudErreur as exc:
        if str(exc) != "AUTHENTIFICATION_REFUSEE":
            raise
        if getattr(account, "renouvele", False):
            raise
        token = account.jeton(force=True)
        result = relever_avec_jeton(
            token, machine, opener=opener, identite=identite, clock=clock, notre_machine=notre_machine
        )
    result["authentification"] = "COMPTE_AUTONOME_PKCE"
    result["signature_jwt_verifiee_localement"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cloud", action="store_true")
    mode.add_argument("--instantane", type=Path)
    mode.add_argument("--inspecter-session", action="store_true")
    parser.add_argument("--machine")
    parser.add_argument(
        "--session-slicer",
        action="store_true",
        help="Réutiliser explicitement une session locale ; format chiffré uniquement avec --dechiffrer-session",
    )
    parser.add_argument(
        "--compte",
        action="store_true",
        help="Compte autonome privé : PKCE et refresh, aucun slicer ; sans --machine, liste les imprimantes",
    )
    parser.add_argument(
        "--notre-machine",
        action="store_true",
        help="Découvrir puis lire seulement une unique Kobra S1 qualifiée, sinon demander une sélection",
    )
    parser.add_argument("--saisir-jeton", action="store_true", help="Saisir le jeton sans écho dans un terminal")
    parser.add_argument(
        "--dechiffrer-session",
        action="store_true",
        help="Autoriser explicitement le déchiffrement en mémoire du slicer macOS précisément pris en charge",
    )
    parser.add_argument(
        "--copie-session", type=Path, help="Copie explicite privée de la configuration, jamais le profil installé"
    )
    args = parser.parse_args(argv)
    if (args.session_slicer or args.saisir_jeton or args.compte) and (not args.cloud):
        parser.error("--cloud requis pour authentifier")
    if args.compte and (args.session_slicer or args.saisir_jeton):
        parser.error("choisir compte autonome ou source explicite de jeton")
    if args.notre_machine and (not args.compte or not args.cloud or args.machine is not None):
        parser.error("--cloud --compte requis et --machine incompatible avec --notre-machine")
    if args.session_slicer and args.saisir_jeton:
        parser.error("choisir une seule source de jeton")
    if args.dechiffrer_session and (not (args.cloud and args.session_slicer)):
        parser.error("--cloud --session-slicer requis pour déchiffrer")
    try:
        if args.inspecter_session:
            result = session_slicer(args.copie_session)
        elif args.instantane:
            if args.machine is None:
                raise CloudErreur("MACHINE_REQUISE")
            raw = _local_json(args.instantane)
            result = projeter(raw.get("detail", raw.get("data", raw)), args.machine)
        elif args.cloud:
            if args.compte:
                result = relever_avec_compte(args.machine, notre_machine=args.notre_machine)
                print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
                return 0
            if args.machine is None:
                raise CloudErreur("MACHINE_REQUISE")
            if args.session_slicer:
                token = token_slicer(args.copie_session, dechiffrer=args.dechiffrer_session)
            elif args.saisir_jeton:
                import getpass
                import sys

                if not sys.stdin.isatty():
                    raise CloudErreur("TERMINAL_INTERACTIF_REQUIS")
                token = getpass.getpass("Jeton Anycubic (masque) : ")
            else:
                token = os.environ.get(ENV_TOKEN)
            if not _secret(token):
                raise CloudErreur("AUTHENTIFICATION_REQUISE")
            names = ("APP_ID", "APP_SECRET", "APP_VERSION")
            values = [os.environ.get("BENCHAB_ANYCUBIC_" + n) for n in names]
            if any(values) and (not all(values)):
                raise CloudErreur("IDENTITE_APPLICATION_INCOMPLETE")
            identite = dict(zip(("app_id", "app_secret", "version"), values)) if all(values) else identite_application()
            result = CloudLecture(token, **identite).relever(args.machine)
        else:
            result = etat()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (CloudErreur, OSError, ValueError, TypeError) as exc:
        safe = {
            "SESSION_CHIFFREE_OU_JETON_ABSENT",
            "AUTHENTIFICATION_REQUISE",
            "MACHINE_REQUISE",
            "MACHINE_INVALIDE",
            "REPONSE_MACHINE_INCOHERENTE",
            "IDENTITE_APPLICATION_ALTEREE",
            "TERMINAL_INTERACTIF_REQUIS",
        }
        reason = (
            str(exc) if isinstance(exc, CloudErreur) and str(exc) in safe else "CLOUD_OU_CONFIGURATION_INDISPONIBLE"
        )
        print(
            json.dumps(
                {"statut": "INDISPONIBLE", "raison": reason, "validation_reelle": False, "commandes_machine": False}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
