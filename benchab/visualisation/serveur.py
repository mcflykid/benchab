"""Sert les pages locales de visualisation 3D et enregistre les commentaires.

Le serveur expose les scripts locaux nécessaires à ces pages. Mise en place le 23/10/2026.

usage : <venv>/bin/python -m benchab.visualisation.serveur [--port 8770]
        <venv>/bin/python -m benchab.visualisation.serveur --ouvrir <page.html | />
Navigateur (27/10/2026) : Safari sur macOS s'il est installé (rendu Retina, trackpad) ;
VISIONNEUSE_NAVIGATEUR=defaut pour le navigateur
par défaut ; ailleurs, module webbrowser. GET / : accueil listant les pages 3D de la base
(projets/*/preuves/visualisation, exemples).
Commentaires : GET /__commentaires?page=<chemin de la page> lit, POST /__commentaires {page, commentaires}
écrit le fichier
<dossier de la page>/commentaires.json (chemin vérifié : page .html sous la racine ; écriture atomique ;
réponses des assistants
conservées par fusion). Écoute seulement 127.0.0.1. Réponses de l'assistant : ``python -m
benchab.visualisation.commentaires``.
"""

import hashlib
import http.server
import io
import json
import math
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.machine.configuration import charger as configuration_machine
from benchab.visualisation.catalogue_visuel import racine_privee
from benchab.visualisation.hors_ligne import VENDOR
from benchab.visualisation.stockage_commentaires import atomique, lire, ouvrir_dossier, sans_liens, transaction, valider

RACINE = racine_privee()
_IMPORTS_ACTIFS = {}
_VERROU_IMPORTS = threading.Lock()


def fichier_commentaires(page, racine=None):
    """Résout le fichier de commentaires d’une page HTML privée existante.

    Renvoie un chemin sous l’atelier. Une page hors des dossiers de
    visualisation ou un chemin à lien est refusé. Aucune écriture.
    """
    racine = atelier_prive(racine or RACINE)
    rel = Path(urllib.parse.unquote(page).lstrip("/"))
    parts = rel.parts
    permitted = len(parts) == 5 and parts[0] == "projets" and parts[2:4] == ("preuves", "visualisation")
    if not permitted or ".." in parts or rel.suffix != ".html":
        raise ValueError("page hors des emplacements autorisés")
    p = sans_liens(racine / rel)
    if not p.is_file():
        raise ValueError("page HTML absente")
    return sans_liens(p.parent / "commentaires.json")


def fusionner(anciens, nouveaux, supprimes=()):
    """Fusionne les commentaires de la page avec les réponses déjà enregistrées.

    Les réponses sont réunies sans doublon et le statut le plus récent prévaut. Une absence conserve
    le commentaire. Les identifiants explicitement listés dans supprimes sont retirés.
    """
    par_id = {c.get("id"): c for c in anciens}
    for c in nouveaux:
        a = par_id.get(c.get("id"))
        if not a:
            continue
        vus = {(r.get("auteur"), r.get("date"), r.get("texte")) for r in c.get("reponses", [])}
        c["reponses"] = (c.get("reponses") or []) + [
            r for r in a.get("reponses", []) if (r.get("auteur"), r.get("date"), r.get("texte")) not in vus
        ]
        c["reponses"].sort(key=lambda r: r.get("date") or "")
        if (a.get("statut_date") or "") > (c.get("statut_date") or ""):
            c["statut"], c["statut_date"] = (a.get("statut"), a.get("statut_date"))
    ids = {c.get("id") for c in nouveaux}
    return [c for c in nouveaux + [c for c in anciens if c.get("id") not in ids] if c.get("id") not in supprimes]


_VERROU_PREPARATION = threading.Lock()


def _decoder_json_post(brut, cles_permises):
    """Refuse doublons, valeurs non finies et clés étrangères avant les handlers locaux."""
    def objet_sans_doublons(paires):
        resultat = {}
        for cle, valeur in paires:
            if cle in resultat:
                raise ValueError("clé JSON dupliquée")
            resultat[cle] = valeur
        return resultat

    def flottant_fini(texte):
        valeur = float(texte)
        if not math.isfinite(valeur):
            raise ValueError("flottant JSON non fini")
        return valeur

    donnees = json.loads(
        brut.decode("utf-8"), object_pairs_hook=objet_sans_doublons,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constante JSON interdite")),
        parse_float=flottant_fini,
    )
    if not isinstance(donnees, dict) or (cles_permises is not None and set(donnees) - set(cles_permises)):
        raise ValueError("objet JSON ou clés invalides")
    return donnees


class Gestion(http.server.SimpleHTTPRequestHandler):
    """Traite les requêtes locales et statuts HTTP ; ne publie pas les secrets et ne commande jamais l’imprimante."""

    def __init__(self, *a, **k):
        """Fixe le répertoire de service à la racine privée du serveur HTTP."""
        self.racine = atelier_prive(getattr(a[2], "racine", RACINE))
        super().__init__(*a, directory=str(self.racine), **k)

    def log_message(self, *a):
        """Écarte la journalisation HTTP par défaut des requêtes privées."""
        pass

    def repondre(self, code, obj):
        """Émet une réponse JSON UTF-8 avec le code HTTP fourni et sans mise en cache."""
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def hote_valide(self, hote=None):
        """Vérifie un Host local unique ; sans argument, lit et contrôle l'en-tête de la requête."""
        if hote is None:
            valeurs = self.headers.get_all("Host", [])
            if len(valeurs) != 1:
                return False
            hote = valeurs[0]
        return hote in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}

    def lire_json_post(self, maximum, *, cles_permises=None):
        """Valide les barrières POST locales puis lit un objet JSON borné ; renvoie dict ou None après HTTP 4xx.

        `maximum` est en octets ; corps, en-têtes, UTF-8 et clés permis sont contrôlés
        avant l’action métier. Délai de lecture fixe à 5 s ; aucun effet secondaire.
        """
        brut = self.lire_corps_post(maximum, "application/json")
        if brut is None:
            return None
        try:
            return _decoder_json_post(brut, cles_permises)
        except (ValueError, UnicodeError, RecursionError):
            self.repondre(400, {"erreur": "requête invalide"})
            return None

    def lire_corps_post(self, maximum, type_attendu):
        """Lit au plus maximum octets d’une requête de même origine et du type exact demandé."""
        hote = self.headers.get_all("Host", [])
        origine = self.headers.get_all("Origin", [])
        if len(hote) != 1 or len(origine) != 1 or not self.hote_valide(hote[0]) or origine[0] != "http://" + hote[0]:
            self.repondre(403, {"erreur": "origine non autorisée"})
            return None
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != type_attendu:
            self.repondre(415, {"erreur": "Type de données attendu : " + type_attendu})
            return None
        longueurs = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []) or len(longueurs) > 1:
            self.repondre(400, {"erreur": "requête invalide"})
            return None
        if not longueurs:
            self.repondre(413, {"erreur": "taille excessive ou vide"})
            return None
        if not re.fullmatch(r"[0-9]+", longueurs[0]):
            self.repondre(400, {"erreur": "requête invalide"})
            return None
        chiffres = longueurs[0].lstrip("0") or "0"
        if len(chiffres) > len(str(maximum)):
            self.repondre(413, {"erreur": "taille excessive ou vide"})
            return None
        taille = int(chiffres)
        if taille <= 0 or taille > maximum:
            self.repondre(413, {"erreur": "taille excessive ou vide"})
            return None
        try:
            self.connection.settimeout(5)
            brut = self.rfile.read(taille)
            if len(brut) != taille:
                raise ValueError("requête incomplète")

            return brut
        except (OSError, ValueError):
            self.repondre(400, {"erreur": "requête invalide"})
            return None

    def send_head(self):
        """GET et HEAD partagent une liste positive ; aucun listing du dépôt.

        Ouvre les ancêtres sans liens et garde le descripteur épinglé pendant
        l'ouverture finale, pour ne pas suivre un lien remplacé entre les tests.
        """
        try:
            if not self.hote_valide():
                raise ValueError("hôte interdit")
            decoded = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            rel = Path(decoded.lstrip("/"))
            parts = rel.parts
            page = (
                len(parts) == 5
                and parts[0] == "projets"
                and parts[2:4] == ("preuves", "visualisation")
                and rel.suffix == ".html"
            )
            conception = (
                len(parts) == 4 and parts[:2] == ("conceptions", "bibliotheque")
                and re.fullmatch(r"[0-9a-f]{32}", parts[2]) and parts[3] == "atelier.html"
            )
            # Les exports historiques restent intacts : seul cet ancien préfixe
            # connu pointe vers les mêmes modules vérifiés (docs/reference/controles.md).
            script = (
                len(parts) >= 5
                and parts[:3] in {("benchab", "visualisation", "vendor"), ("outils", "visualisation-3d", "vendor")}
                and parts[3] == "three-0.160.0" and rel.suffix == ".js"
            )
            image = rel.as_posix() in {"docs/identite/benchab3d-icone.png"}
            if (
                ".." in parts
                or "\\" in decoded
                or any((ord(c) < 32 for c in decoded))
                or (not (page or conception or script or image))
            ):
                raise ValueError("ressource interdite")
            if script:
                chemin_vendor = Path(*parts[4:])
                fixture_vendor = self.racine / "benchab/visualisation/vendor/three-0.160.0"
                p = sans_liens((fixture_vendor if fixture_vendor.is_dir() else VENDOR) / chemin_vendor)
            else:
                p = sans_liens(self.racine / rel)
                p.relative_to(self.racine)
            directory = ouvrir_dossier(p.parent)
            try:
                fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            finally:
                os.close(directory)
            f = os.fdopen(fd, "rb")
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                f.close()
                raise ValueError("fichier régulier requis")
            if script:
                from benchab.visualisation.hors_ligne import MODULES

                vendor = self.racine / "benchab/visualisation/vendor/three-0.160.0"
                if not vendor.is_dir():
                    vendor = VENDOR
                module = p.relative_to(vendor).as_posix()
                attendu = json.loads((vendor / "PROVENANCE.json").read_text())["fichiers"].get(module)
                if module not in MODULES.values():
                    f.close()
                    raise ValueError("module non déclaré")
                if info.st_size > 8 * 1024 * 1024:
                    f.close()
                    raise ValueError("module volumineux refusé")
                body = f.read()
                f.close()
                if hashlib.sha256(body).hexdigest() != attendu:
                    raise ValueError("module altéré")
                f = io.BytesIO(body)
        except (ValueError, OSError):
            self.repondre(403, {"erreur": "ressource interdite"})
            return None
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(p)))
        self.send_header("Content-Length", str(len(body) if script else info.st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        if script:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        return f

    def do_HEAD(self):
        """Reprend le routage GET en conservant les en-têtes et le statut HTTP."""
        self.do_GET()

    def do_GET(self):
        """Sert une requête locale ; HTTP200/4xx ou503 si dépendance absente, sans lecture Cloud implicite."""
        try:
            return self.consulter()
        except ImportError as error:
            from html import escape

            module = escape(error.name or "dépendance locale")
            body = (
                (
                    '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="'
                    'viewport" content="width=device-width,initial-scale=1"><title>Vue'
                    " indisponible — BENCHAB 3D</title><h1>Vue indisponible</h1><p>Mod"
                    "ule indisponible : <code>"
                )
                + module
                + (
                    "</code>.</p><p>Ouvrir l’atelier avec <code>uv run benchab visualisation "
                    "serveur -- --ouvrir /atelier</c"
                    "ode> pour utiliser son environnement existant. Si le module y man"
                    "que aussi, cette vue reste indisponible ; aucune installation aut"
                    'omatique.</p><p><a href="/atelier">Revenir aux projets</a></p></h'
                    "tml>"
                )
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    def consulter(self):
        """Sert les vues, ressources ou commentaires autorisés après contrôle de l’hôte."""
        if not self.hote_valide():
            return self.repondre(403, {"erreur": "hôte interdit"})
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.accueil()
        if u.path == "/__visionneuse":
            return self.repondre(200, {"serveur": "visionneuses", "racine": self.racine.name,
                                       "racine_sha256": _identite_racine(self.racine)})
        if u.path == "/bibliotheque-atelier":
            from benchab.visualisation.atelier_preparation import bibliotheque

            params = urllib.parse.parse_qs(u.query)
            modele = params.get("modele", [""])[0]
            export = params.get("sortie", [None])[0]
            def produire():
                """Construit les mêmes pièces pour l’aperçu et pour le téléchargement STL."""
                return bibliotheque(
                    modele, float(params.get("cote", [""])[0]), float(params.get("jeu", [""])[0]),
                    format_stl=export == "stl", format_fiche=export == "json",
                )

            if export in ("stl", "json"):
                if not _VERROU_PREPARATION.acquire(blocking=False):
                    return self.repondre(429, {"erreur": "Une analyse est en cours. Réessayez après sa fin."})
                try:
                    body = produire()
                except (ValueError, RuntimeError) as erreur:
                    return self.repondre(400, {"erreur": str(erreur)})
                finally:
                    _VERROU_PREPARATION.release()
                self.send_response(200)
                self.send_header("Content-Type", "model/stl" if export == "stl" else "application/json")
                self.send_header("Content-Disposition", f'attachment; filename="{modele}.{export}"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        return None
                return None
            return self.preparation_html(produire)
        if u.path == "/atelier":
            try:
                from benchab.visualisation.atelier_local import page

                params = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
                return self.html(page(self.racine, params))
            except (OSError, ValueError, TypeError, KeyError) as error:
                from benchab.visualisation.atelier_local import document, texte

                body = document(
                    "Consultation indisponible",
                    '<p class="erreur">' + texte(error) + '</p><p><a href="/atelier">Revenir aux projets</a></p>',
                )
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
        if u.path == "/preparation":
            from benchab.visualisation.atelier_preparation import preparation_projet

            params = urllib.parse.parse_qs(u.query)
            return self.preparation_html(
                lambda: preparation_projet(self.racine, params.get("projet", [""])[0],
                                           params.get("preparation", [None])[0])
            )
        if u.path == "/inspecter":
            try:
                from benchab.visualisation.catalogue_visuel import scene

                nom = urllib.parse.parse_qs(u.query).get("projet", [""])[0]
                return self.html(scene(self.racine, nom, ressources_partagees=True))
            except (OSError, ValueError, KeyError, TypeError) as error:
                return self.repondre(
                    400,
                    {
                        "erreur": str(error),
                        "suite": "Revenir à l’accueil et choisir un projet avec géométrie disponible.",
                    },
                )
        if u.path == "/couches":
            try:
                from benchab.visualisation.catalogue_visuel import couches

                nom = urllib.parse.parse_qs(u.query).get("projet", [""])[0]
                return self.html(couches(self.racine, nom, ressources_partagees=True))
            except (OSError, ValueError, KeyError, TypeError) as error:
                return self.repondre(
                    400, {"erreur": str(error), "suite": "Revenir à l’accueil ; aucune fabrication lancée."}
                )
        if u.path == "/paquet-inspection":
            try:
                from benchab.visualisation.paquet_inspection import paquet

                nom = urllib.parse.parse_qs(u.query).get("projet", [""])[0]
                b = paquet(self.racine, nom)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="inspection-' + nom + '.zip"')
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(b)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except (OSError, ValueError, KeyError, TypeError) as error:
                return self.repondre(400, {"erreur": str(error), "suite": "Aucun fichier source modifié."})
        if u.path != "/__commentaires":
            if self.command == "HEAD":
                f = self.send_head()
                if f:
                    f.close()
                return
            return super().do_GET()
        try:
            f = fichier_commentaires(urllib.parse.parse_qs(u.query).get("page", [""])[0], self.racine)
            self.repondre(200, lire(f) or {"commentaires": []})
        except Exception as e:
            self.repondre(400, {"erreur": str(e)})

    def accueil(self):
        """Émet le catalogue HTML des projets de l’atelier courant."""
        from benchab.visualisation.catalogue_visuel import accueil

        self.html(accueil(self.racine))

    def html(self, body):
        """Émet HTTP 200 avec la longueur du HTML fourni en octets, sans corps pour HEAD."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def preparation_html(self, produire):
        """Limite l’analyse lourde à une demande locale ; HTTP 200/400/429, sources intactes."""
        from zipfile import BadZipFile

        if not _VERROU_PREPARATION.acquire(blocking=False):
            return self.repondre(429, {"erreur": "Une analyse est en cours. Réessayez après sa fin."})
        try:
            body = produire()
        except (OSError, ValueError, BadZipFile, TypeError, KeyError, RuntimeError) as exc:
            return self.repondre(400, {"erreur": str(exc)})
        finally:
            _VERROU_PREPARATION.release()
        if body is not None:
            try:
                return self.html(body)
            except (BrokenPipeError, ConnectionResetError):
                # Un import annulé dans la page peut finir avant la réponse.
                return None

    def preparation_import(self):
        """Construit un aperçu depuis les seuls octets du modèle déposé, avec taille et extension bornées."""
        from benchab.visualisation.atelier_preparation import MAX_SOURCE, import_html

        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        jeton = params.get("analyse", [""])[0]
        if jeton and not re.fullmatch(r"[0-9a-f-]{36}", jeton):
            return self.repondre(400, {"erreur": "Identifiant d’analyse invalide."})

        def produire():
            """Analyse seulement un corps déjà accepté par le serveur."""
            evenement = threading.Event()
            if jeton:
                with _VERROU_IMPORTS:
                    _IMPORTS_ACTIFS[jeton] = (evenement, ("Réception du fichier", 0, 0))

            def progression(etape, finies, total):
                """Mémorise une progression sans conserver la géométrie ni publier de fichier."""
                if jeton:
                    with _VERROU_IMPORTS:
                        _IMPORTS_ACTIFS[jeton] = (evenement, (etape, finies, total))

            try:
                brut = self.lire_corps_post(MAX_SOURCE, "application/octet-stream")
                if brut is not None:
                    if evenement.is_set():
                        raise ValueError("Analyse annulée.")
                    plaque = params.get("plaque", [""])[0]
                    piece = params.get("piece", [""])[0]
                    return import_html(brut, params.get("nom", [""])[0], params.get("unites", [None])[0],
                                       annule=evenement.is_set, progression=progression,
                                       prive=self.racine, comparaison_auto=True,
                                       plaque_cible=int(plaque) if plaque else None,
                                       piece_cible=int(piece) if piece else None)
            finally:
                if jeton:
                    with _VERROU_IMPORTS:
                        _IMPORTS_ACTIFS.pop(jeton, None)

        return self.preparation_html(produire)

    def annuler_import(self):
        """Signale à l’analyse active de s’arrêter au prochain point de contrôle."""
        demande = self.lire_json_post(256, cles_permises={"analyse"})
        if demande is None:
            return None
        identifiant = demande.get("analyse")
        if not isinstance(identifiant, str) or not re.fullmatch(r"[0-9a-f-]{36}", identifiant):
            return self.repondre(400, {"erreur": "Identifiant d’analyse invalide."})
        with _VERROU_IMPORTS:
            etat = _IMPORTS_ACTIFS.get(identifiant)
            if etat is not None:
                etat[0].set()
        return self.repondre(200, {"annulation_demandee": etat is not None})

    def progression_import(self):
        """Retourne l’étape et les compteurs sans rendre d’information sur le fichier importé."""
        demande = self.lire_json_post(256, cles_permises={"analyse"})
        if demande is None:
            return None
        identifiant = demande.get("analyse")
        if not isinstance(identifiant, str) or not re.fullmatch(r"[0-9a-f-]{36}", identifiant):
            return self.repondre(400, {"erreur": "Identifiant d’analyse invalide."})
        with _VERROU_IMPORTS:
            etat = _IMPORTS_ACTIFS.get(identifiant)
        if etat is None:
            return self.repondre(200, {"active": False})
        etape, finies, total = etat[1]
        return self.repondre(200, {"active": True, "etape": etape, "finies": finies, "total": total})

    def comparer_preparation(self):
        """Compare une préparation privée épinglée ; JSON en s et g, sans commande machine."""
        from benchab.visualisation.atelier_preparation import comparaison_projet
        from benchab.visualisation.comparaison_atelier import MODES

        champs = {"projet", "preparation", "sha256", "marge"}
        requete = self.lire_json_post(4096, cles_permises=champs | {"mode", "piece"})
        if requete is None:
            return
        if (
            not champs.issubset(requete) or type(requete["marge"]) is not bool
            or any(not isinstance(requete[k], str) for k in champs - {"marge"})
            or requete.get("mode", "vitesses") not in MODES
            or ("piece" in requete and (not isinstance(requete["piece"], str) or len(requete["piece"]) > 256))
            or not re.fullmatch(r"P[0-9]+", requete["preparation"])
            or not re.fullmatch(r"[0-9a-f]{64}", requete["sha256"])
        ):
            return self.repondre(400, {"erreur": "Préparation ou empreinte invalide."})
        if not _VERROU_PREPARATION.acquire(blocking=False):
            return self.repondre(429, {"erreur": "Une analyse est en cours. Réessayez après sa fin."})
        try:
            resultat = comparaison_projet(
                self.racine, requete["projet"], requete["preparation"], requete["sha256"], requete["marge"],
                mode=requete.get("mode", "vitesses"),
                piece=requete.get("piece"),
            )
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            return self.repondre(400, {"erreur": str(exc)})
        finally:
            _VERROU_PREPARATION.release()
        return self.repondre(200, resultat)

    def enregistrer_observation(self):
        """Archive un constat confirmé dans le projet privé, sans action sur l’impression."""
        from benchab.visualisation.atelier_preparation import enregistrer_observation

        champs = {"projet", "preparation", "sha256", "piece", "defaut", "zone", "photo", "texte"}
        demande = self.lire_json_post(16384, cles_permises=champs)
        if demande is None:
            return
        if set(demande) != champs or any(
            not isinstance(demande[k], str) for k in ("projet", "preparation", "sha256")
        ):
            return self.repondre(400, {"erreur": "Préparation ou observation incomplète."})
        if not _VERROU_PREPARATION.acquire(blocking=False):
            return self.repondre(429, {"erreur": "Une analyse est en cours. Réessayez après sa fin."})
        try:
            observation = enregistrer_observation(
                self.racine, demande["projet"], demande["preparation"], demande["sha256"],
                demande["piece"], demande["defaut"], demande["zone"], demande["photo"], demande["texte"],
            )
        except (ValueError, OSError, KeyError, RuntimeError) as erreur:
            return self.repondre(400, {"erreur": str(erreur)})
        finally:
            _VERROU_PREPARATION.release()
        return self.repondre(201, {"id": observation["id"]})

    def enregistrer_conception(self):
        """Ajoute une version privée paramétrique par action explicite."""
        from benchab.visualisation.atelier_preparation import enregistrer_bibliotheque

        demande = self.lire_json_post(1024, cles_permises={"modele", "cote", "jeu", "parent"})
        if demande is None:
            return None
        if not {"modele", "cote", "jeu"} <= demande.keys() or not isinstance(demande["modele"], str) or any(
            type(demande[k]) not in (int, float) for k in ("cote", "jeu")
        ):
            return self.repondre(400, {"erreur": "Modèle et cotes numériques requis."})
        if not _VERROU_PREPARATION.acquire(blocking=False):
            return self.repondre(429, {"erreur": "Une analyse est en cours."})
        try:
            resultat = enregistrer_bibliotheque(
                self.racine, demande["modele"], demande["cote"], demande["jeu"], demande.get("parent"),
            )
        except (ValueError, OSError, KeyError, TypeError, RuntimeError, OverflowError) as erreur:
            return self.repondre(400, {"erreur": str(erreur)})
        finally:
            _VERROU_PREPARATION.release()
        return self.repondre(200, resultat)

    def cloud_lecture(self):
        """Traite un objet Cloud JSON de 8192 octets max ; lecture seule, aucune commande machine."""
        d = self.lire_json_post(8192, cles_permises={"jeton", "machine", "session_slicer", "compte"})
        if d is None:
            return
        token = None
        try:
            from benchab.machine.cloud_lecture import (
                CloudErreur,
                _id,
                relever_avec_compte,
                relever_avec_jeton,
                token_slicer,
            )

            if "compte" in d and type(d["compte"]) is not bool:
                raise ValueError("choix de compte invalide")
            if d.get("compte") is True and ("jeton" in d or "session_slicer" in d):
                raise ValueError("choisir une seule authentification")
            if "session_slicer" in d and type(d["session_slicer"]) is not bool:
                raise ValueError("choix de session invalide")
            if d.get("session_slicer") is True and "jeton" in d:
                raise ValueError("choisir une seule source de jeton")
            machine = _id(d["machine"]) if d.get("machine") is not None else None
            if d.get("compte") is True:
                try:
                    result = relever_avec_compte(machine)
                except CloudErreur as error:
                    if str(error) == "CONFIGURATION_COMPTE_REQUISE":
                        return self.repondre(
                            409,
                            {
                                "statut": "CONFIGURATION_COMPTE_REQUISE",
                                "erreur": (
                                    "Première configuration à effectuer dans le terminal de ce Mac ; n"
                                    "e pas saisir le mot de passe ici."
                                ),
                                "configuration_command": "uv run benchab machine session-compte -- --configurer",
                                "commandes_machine": False,
                            },
                        )
                    raise
                return self.repondre(200, result)
            token = token_slicer(dechiffrer=True) if d.get("session_slicer") is True else d.get("jeton")
            result = relever_avec_jeton(token, machine)
            return self.repondre(200, result)
        except ImportError:
            return self.repondre(503, {"erreur": "module de lecture locale indisponible"})
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            return self.repondre(
                400,
                {
                    "erreur": (
                        "lecture Cloud refusée ou indisponible ; vérifier la connexion et "
                        "attendre quelques secondes avant une nouvelle lecture, sans publi"
                        "er le jeton"
                    )
                },
            )
        finally:
            token = None
            if isinstance(d, dict):
                d.clear()

    def automatisation(self):
        """Exécute l’action locale bornée d’un objet JSON de 4096 octets max."""
        d = self.lire_json_post(4096, cles_permises={"action", "projet", "preparation"})
        if d is None:
            return
        try:
            if d.get("action") not in ("executer", "ouvrir-slicer"):
                raise ValueError("action non autorisée")
            from benchab.organisation import automatisation as auto

            auto.projet(self.racine, d.get("projet", ""))
            ident = d.get("preparation")
            if ident is not None and (not isinstance(ident, str) or not re.fullmatch("P[0-9]+", ident)):
                raise ValueError("préparation invalide")
            import datetime

            out = Path.home() / "Codex" / datetime.date.today().isoformat() / "benchab3d-executions" / d["projet"]
            if d["action"] == "executer":
                result = auto.executer(self.racine, d["projet"], out, ident)
            else:
                result = auto.ouvrir_slicer(self.racine, d["projet"], out, ident, ouvrir=True)
            summary = {
                k: v
                for k, v in result.items()
                if k
                in ("statut", "preparation", "copie", "ouverture", "erreurs", "avertissements", "blocages", "limite")
            }
            if "etapes" in result:
                summary["etapes"] = {
                    n: {k: v for k, v in e.items() if k in ("statut", "resultat", "erreur", "raisons")}
                    for n, e in result["etapes"].items()
                }
            summary["dossier"] = str(out)
            return self.repondre(200, summary)
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            return self.repondre(400, {"erreur": "demande refusée ; consulter les prérequis et le diagnostic local"})

    def do_POST(self):
        """Route les POST connus vers leur lecteur JSON local ; réponses HTTP 4xx sur requête invalide."""
        if urllib.parse.urlparse(self.path).path == "/__preparation":
            return self.preparation_import()
        if urllib.parse.urlparse(self.path).path == "/__preparation_annuler":
            return self.annuler_import()
        if urllib.parse.urlparse(self.path).path == "/__preparation_progression":
            return self.progression_import()
        if urllib.parse.urlparse(self.path).path == "/__conception_enregistrer":
            return self.enregistrer_conception()
        if urllib.parse.urlparse(self.path).path == "/__preparation_comparer":
            return self.comparer_preparation()
        if urllib.parse.urlparse(self.path).path == "/__preparation_observation":
            return self.enregistrer_observation()
        if urllib.parse.urlparse(self.path).path == "/__decoupe":
            return self.decoupe()
        if urllib.parse.urlparse(self.path).path == "/__cloud_lecture":
            return self.cloud_lecture()
        if urllib.parse.urlparse(self.path).path == "/__automatisation":
            return self.automatisation()
        if urllib.parse.urlparse(self.path).path != "/__commentaires":
            return self.repondre(404, {"erreur": "inconnu"})
        d = self.lire_json_post(1_000_000)
        if d is None:
            return
        try:
            d = valider(d)
            f = fichier_commentaires(d["page"], self.racine)
            with transaction(f):
                old = lire(f)
                deleted = sorted(set(old.get("supprimes", [])) | set(d.get("supprimes", [])))
                d["commentaires"] = fusionner(old.get("commentaires", []), d["commentaires"], deleted)
                d["supprimes"] = deleted
                atomique(f, d)
            self.repondre(200, {"fichier": str(f.relative_to(self.racine)), "nombre": len(d["commentaires"])})
        except Exception as e:
            self.repondre(400, {"erreur": str(e)})

    def decoupe(self):
        """Traite l’action locale de découpe sur un JSON de 1 Mio max, sans chemin de sortie client."""
        requete = self.lire_json_post(1_000_000)
        if requete is None:
            return
        try:
            from benchab.visualisation.decoupe import agir

            with self.server.verrou_decoupe:
                resultat = agir(self.racine, requete)
            self.repondre(200, resultat)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            self.repondre(400, {"erreur": str(exc)[:500]})


class Serveur(http.server.ThreadingHTTPServer):
    """Sert les pages privées à l’adresse fournie par l’appelant.

    Le lanceur CLI utilise 127.0.0.1. Le serveur ne commande pas la machine.
    """

    request_queue_size = 32

    def __init__(self, address, handler=Gestion, racine=None):
        """Ouvre l’adresse HTTP fournie et associe la racine privée et le verrou de découpe."""
        self.racine = atelier_prive(racine or RACINE)
        self.verrou_decoupe = threading.Lock()
        super().__init__(address, handler)


def _identite_racine(racine):
    """Identifie la racine résolue par SHA-256, sans unité ; ne publie pas son chemin personnel."""
    return hashlib.sha256(os.fsencode(str(atelier_prive(racine)))).hexdigest()


def actif(port, racine=None):
    """Sonde le port local ; booléen, avec racine attendue facultative, sans ouvrir de page ni de fichier projet."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__visionneuse", timeout=1) as r:
            identite = json.loads(r.read())
            # Deux bases peuvent partager leur nom de dossier ; réutiliser la mauvaise
            # page tromperait la préparation (docs/guides/cycle-projet.md).
            return identite.get("serveur") == "visionneuses" and (
                racine is None or identite.get("racine_sha256") == _identite_racine(racine)
            )
    except Exception:
        return False


def adresse_ouverture(cible, port, racine=RACINE):
    """Routes utilisateur explicites, ou page appartenant à la base."""
    racine = atelier_prive(racine)
    chemin = (
        cible.lstrip("/")
        if cible in ("/", "", "/atelier")
        else sans_lien(cible, racine=racine).relative_to(racine).as_posix()
    )
    return f"http://127.0.0.1:{port}/" + chemin


def ouvrir(url):
    """Safari sur macOS s'il est installé, sauf VISIONNEUSE_NAVIGATEUR=defaut ; sinon navigateur par défaut."""
    if sys.platform == "darwin":
        safari = (
            os.environ.get("VISIONNEUSE_NAVIGATEUR", "safari") != "defaut" and Path("/Applications/Safari.app").exists()
        )
        return subprocess.run(["open", "-a", "Safari", url] if safari else ["open", url]).returncode
    import webbrowser

    return 0 if webbrowser.open(url) else 1


def main(argv=None, *, prive=None):
    """Sert la base privée demandée ; code0 ou erreur, sans réutiliser une autre racine ni contacter la machine."""
    import argparse

    parser = argparse.ArgumentParser(description="Visionneuse privée locale, sans appel Cloud implicite")
    parser.add_argument("--port", type=int, default=int(configuration_machine()["visionneuse"]["port"]))
    parser.add_argument("--ouvrir")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port attendu entre 1 et 65535")
    racine = atelier_prive(prive or RACINE)
    port = args.port
    if args.ouvrir:
        url = adresse_ouverture(args.ouvrir, port, racine)
        if not actif(port, racine):
            with socket.socket() as sonde:
                if sonde.connect_ex(("127.0.0.1", port)) == 0:
                    raise ValueError(f"Port {port} occupé par un autre service")
            subprocess.Popen(
                [sys.executable, "-B", "-m", "benchab.visualisation.serveur", "--port", str(port)],
                env=dict(os.environ, BENCHAB_PRIVE=str(racine)),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(50):
                if actif(port, racine):
                    break
                time.sleep(0.1)
            if not actif(port, racine):
                raise ValueError("Le serveur local n'a pas confirmé son démarrage")
        code = ouvrir(url)
        print(url)
        return code
    with Serveur(("127.0.0.1", port), Gestion, racine) as serveur:
        print(f"Visionneuses : http://localhost:{port}/ (racine {racine})", flush=True)
        try:
            serveur.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
