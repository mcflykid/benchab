"""Serveur local des visionneuses 3D (outil commun, Claude, 23/10/2026) : sert uniquement les pages de
visualisation et leurs scripts locaux, et enregistre les commentaires pointés
dans le projet, pour que l'assistant les lise sans copier-coller.
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
benchab.visualisation.commentaires``."""

import hashlib
import http.server
import io
import json
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


def fichier_commentaires(page, racine=None):
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
    """La page envoie sa liste ; les réponses écrites entre-temps par l'outil commentaires sont
    conservées : réponses réunies (sans doublon), statut le plus récent. Une absence conserve le
    commentaire ; seuls les identifiants dans supprimes le retirent."""
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


class Gestion(http.server.SimpleHTTPRequestHandler):
    """Traite les requêtes locales et statuts HTTP ; ne publie pas les secrets et ne commande jamais l’imprimante."""
    def __init__(self, *a, **k):
        self.racine = atelier_prive(getattr(a[2], "racine", RACINE))
        super().__init__(*a, directory=str(self.racine), **k)

    def log_message(self, *a):
        pass

    def repondre(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def hote_valide(self):
        return self.headers.get("Host") in {
            f"localhost:{self.server.server_port}",
            f"127.0.0.1:{self.server.server_port}",
        }

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
                or (not (page or script or image))
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
        if not self.hote_valide():
            return self.repondre(403, {"erreur": "hôte interdit"})
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.accueil()
        if u.path == "/__visionneuse":
            return self.repondre(200, {"serveur": "visionneuses", "racine": self.racine.name,
                                       "racine_sha256": _identite_racine(self.racine)})
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
        from benchab.visualisation.catalogue_visuel import accueil

        self.html(accueil(self.racine))

    def html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def cloud_lecture(self):
        """Traite le POST explicite JSON/8192octets max ; HTTP200/4xx, lecture Cloud seule, aucune commande machine."""
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in allowed or "http://" + self.headers.get("Host", "") not in allowed:
            return self.repondre(403, {"erreur": "origine non autorisée"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.repondre(415, {"erreur": "JSON requis"})
        if self.headers.get("Transfer-Encoding"):
            return self.repondre(400, {"erreur": "encodage non accepté"})
        d = None
        token = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 8192:
                return self.repondre(413, {"erreur": "taille refusée"})
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("requête incomplète")
            from benchab.machine.cloud_lecture import (
                CloudErreur,
                _id,
                _json,
                relever_avec_compte,
                relever_avec_jeton,
                token_slicer,
            )

            d = _json(raw)
            raw = None
            if not isinstance(d, dict) or set(d) - {"jeton", "machine", "session_slicer", "compte"}:
                raise ValueError("demande invalide")
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
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in allowed or "http://" + self.headers.get("Host", "") not in allowed:
            return self.repondre(403, {"erreur": "origine non autorisée"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.repondre(415, {"erreur": "JSON requis"})
        if self.headers.get("Transfer-Encoding"):
            return self.repondre(400, {"erreur": "encodage non accepté"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 4096:
                return self.repondre(413, {"erreur": "requête trop volumineuse ou vide"})
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("requête incomplète")
            d = json.loads(raw)
            if not isinstance(d, dict) or set(d) - {"action", "projet", "preparation"}:
                raise ValueError("champs non autorisés")
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
        if urllib.parse.urlparse(self.path).path == "/__decoupe":
            return self.decoupe()
        if urllib.parse.urlparse(self.path).path == "/__cloud_lecture":
            return self.cloud_lecture()
        if urllib.parse.urlparse(self.path).path == "/__automatisation":
            return self.automatisation()
        if urllib.parse.urlparse(self.path).path != "/__commentaires":
            return self.repondre(404, {"erreur": "inconnu"})
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in allowed or "http://" + self.headers.get("Host", "") not in allowed:
            return self.repondre(403, {"erreur": "origine non autorisée"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.repondre(415, {"erreur": "JSON requis"})
        if self.headers.get("Transfer-Encoding"):
            return self.repondre(400, {"erreur": "encodage non accepté"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1000000:
                return self.repondre(413, {"erreur": "taille excessive ou vide"})
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("requête incomplète")
            d = valider(json.loads(raw))
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
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.hote_valide() or self.headers.get("Origin") not in allowed:
            return self.repondre(403, {"erreur": "origine non autorisée"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.repondre(415, {"erreur": "JSON requis"})
        if self.headers.get("Transfer-Encoding"):
            return self.repondre(400, {"erreur": "encodage non accepté"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1_000_000:
                return self.repondre(413, {"erreur": "taille excessive ou vide"})
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("requête incomplète")
            from benchab.visualisation.decoupe import agir

            with self.server.verrou_decoupe:
                resultat = agir(self.racine, json.loads(raw))
            self.repondre(200, resultat)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            self.repondre(400, {"erreur": str(exc)[:500]})


class Serveur(http.server.ThreadingHTTPServer):
    """Serveur local de pages privées ; statuts HTTP, aucune exposition réseau ni commande machine implicite."""
    request_queue_size = 32

    def __init__(self, address, handler=Gestion, racine=None):
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
