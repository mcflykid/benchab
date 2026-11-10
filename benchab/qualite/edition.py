"""Contrôle bloquant du dépôt partageable : licences et documentation.

La frontière et la provenance des géométries restent dans leurs contrôles communs.
Les URL sont lues seulement ; aucun résultat réseau inconnu n'est accepté.
"""

import hashlib
import json
import os
import re
import tomllib
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from benchab.qualite.frontiere import EXCLUS
from benchab.qualite.frontiere import verifier as verifier_frontiere

COMPATIBLES = {
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "ISC",
    "Zlib",
    "CC0-1.0",
    "PSF-2.0",
    "MPL-2.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LicenseRef-Matplotlib",
}
LIEN = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")
REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*(<[^>\n]+>|[^\s]+)")
AUTOLIEN = re.compile(r"<((?:https?://|mailto:)[^<>\s]+)>")
MOT_COLLE = re.compile(r"(?<![\w/-])([A-Za-zÀ-ÿ]*[a-zà-ÿ][A-Za-zÀ-ÿ]*\d[\w.,%-]*)")
UNITE_COLLEE = re.compile(r"\d(?:mm|cm|kg|g|m|s|°C|%)(?![\w])")
NOMS_TECHNIQUES = {"build123d", "lib3mf", "py-lib3mf", "manifold3d", "urllib3", "GPLv3", "sha256", "sha1", "md5"}


def anomalies_typographiques(texte):
    erreurs = []
    if "\ufffd" in texte or "\x00" in texte or unicodedata.normalize("NFC", texte) != texte:
        erreurs.append("encodage ou normalisation typographique invalide")
    if any(ligne.rstrip() != ligne for ligne in texte.splitlines()):
        erreurs.append("espaces en fin de ligne")
    prose = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`", "", texte)
    prose = re.sub(r"https?://\S+|!?\[[^\]]*\]\([^)]*\)", "", prose)
    prose = re.sub(r"\b(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\b", "", prose)
    prose = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "", prose)
    prose = re.sub(r"\b\d+(?:\.\d+)+(?:\.?(?:a|b|rc|post)\d+)?\b", "", prose)
    prose = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>|<[^>]+>", " ", prose, flags=re.I)
    # Les chemins et noms de fichiers sont des identifiants, pas des phrases.
    prose = " ".join(mot for mot in prose.split() if "/" not in mot and "_" not in mot)
    if any(m.group(1).rstrip(".,") not in NOMS_TECHNIQUES for m in MOT_COLLE.finditer(prose)):
        erreurs.append("mot et nombre collés : séparer le texte de la mesure ou de l’identifiant")
    if UNITE_COLLEE.search(prose):
        erreurs.append("nombre et unité collés : insérer une espace")
    return erreurs


def typographie(chemins):
    erreurs = []
    for chemin in chemins:
        p = Path(chemin)
        fichiers = list(documents(p, extensions={".md", ".json", ".txt", ".html"})) if p.is_dir() else [p]
        for fichier in fichiers:
            try:
                texte = fichier.read_text(encoding="utf-8")
                if fichier.suffix.lower() == ".json":

                    def phrases(objet):
                        if isinstance(objet, str):
                            if objet.strip():
                                yield objet.strip()
                        elif isinstance(objet, dict):
                            for valeur in objet.values():
                                yield from phrases(valeur)
                        elif isinstance(objet, list):
                            for valeur in objet:
                                yield from phrases(valeur)

                    texte = "\n".join(phrases(json.loads(texte)))
                erreurs.extend(f"{fichier.name} : {e}" for e in anomalies_typographiques(texte))
            except (OSError, UnicodeError, ValueError, RecursionError):
                erreurs.append(f"{fichier.name} : texte à contrôler illisible")
    return erreurs


class LiensHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cibles = []

    def handle_starttag(self, _tag, attributs):
        self.cibles.extend(v for k, v in attributs if k.lower() in {"href", "src"} and v)


def cibles_markdown(texte):
    """Inclut les définitions de référence, autoliens et balises HTML Markdown."""
    navigation = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", texte)
    html = LiensHTML()
    html.feed(navigation)
    return [*LIEN.findall(navigation), *REFERENCE.findall(navigation), *AUTOLIEN.findall(navigation), *html.cibles]


def empreinte_dependances(projet):
    declarations = {
        "runtime": projet["project"].get("dependencies", []),
        "optionnelles": projet["project"].get("optional-dependencies", {}),
        "groupes": projet.get("dependency-groups", {}),
        "construction": projet.get("build-system", {}),
        "sources": projet.get("tool", {}).get("uv", {}).get("sources", {}),
    }
    return hashlib.sha256(json.dumps(declarations, sort_keys=True).encode()).hexdigest()


def compatible(expression):
    """Vocabulaire fermé : un inconnu ou l'AGPL ne reçoit jamais de PASS."""
    if not isinstance(expression, str):
        return False
    termes = expression.split()
    return (
        bool(termes)
        and all(
            (terme in COMPATIBLES if indice % 2 == 0 else terme in {"AND", "OR"}) for indice, terme in enumerate(termes)
        )
        and len(termes) % 2 == 1
    )


def licences(racine):
    racine = Path(racine)
    erreurs = []
    try:
        verrou = tomllib.loads((racine / "uv.lock").read_text())
        manifeste = json.loads((racine / "benchab/reference/licences.json").read_text())
        projet = tomllib.loads((racine / "pyproject.toml").read_text())
        if projet["project"]["license"] != "GPL-3.0-or-later":
            erreurs.append("Licence du projet différente de GPL-3.0-or-later")
        texte = (racine / "LICENSE").read_text()
        if "GNU GENERAL PUBLIC LICENSE" not in texte or "Version 3, 29 June 2007" not in texte or len(texte) < 30000:
            erreurs.append("Texte GPL-3 complet absent")
        if hashlib.sha256((racine / "uv.lock").read_bytes()).hexdigest() != manifeste["lock_sha256"]:
            erreurs.append("Licences : verrou modifié depuis la revue")
        if empreinte_dependances(projet) != manifeste["declarations_sha256"]:
            erreurs.append("Licences : déclarations de dépendances modifiées depuis la revue")
        # uv ne numérote pas le paquet local éditable à version dynamique ;
        # toutes les dépendances tierces restent épinglées (docs/reference/donnees.md).
        attendus = {
            (x["name"], "dynamique" if (
                x["name"] == projet["project"]["name"] and "version" in projet["project"].get("dynamic", [])
                and x.get("source", {}).get("editable") == "." and "version" not in x
            ) else x["version"])
            for x in verrou["package"]
        }
        lignes = manifeste["python"]
        presents = {(x["nom"], x["version"]) for x in lignes}
        if presents != attendus or len(lignes) != len(attendus):
            erreurs.append("Licences : chaque distribution verrouillée doit être recensée une seule fois")
        tiers = (racine / "TIERS.md").read_text()
        for ligne in lignes:
            if not compatible(ligne["licence"]) or not ligne.get("source"):
                erreurs.append(f"Licence incompatible ou non vérifiée : {ligne['nom']}")
            if ligne["licence"] == "LicenseRef-Matplotlib" and not ligne.get("preuves"):
                erreurs.append("Matplotlib : notice examinée absente")
            if f"| {ligne['nom']} | {ligne['version']} | {ligne['licence']} |" not in tiers:
                erreurs.append(f"TIERS.md incomplet : {ligne['nom']}")
        integres = manifeste["integres"]
        obligatoires = {
            "benchab/visualisation/vendor/three-0.160.0/LICENSE",
            "benchab/tiers/text-to-cad-LICENSE.txt",
            "benchab/tiers/modeling-LICENSE.txt",
        }
        if {x["fichier"] for x in integres} != obligatoires or len(integres) != len(obligatoires):
            erreurs.append("Licences des copies intégrées incomplètes")
        for ligne in integres:
            p = Path(ligne["fichier"])
            if p.is_absolute() or ".." in p.parts or not compatible(ligne["licence"]):
                erreurs.append("Licence intégrée invalide")
                continue
            if hashlib.sha256((racine / p).read_bytes()).hexdigest() != ligne["sha256"]:
                erreurs.append(f"Notice intégrée modifiée : {p}")
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        erreurs.append(f"Inventaire de licences illisible : {type(erreur).__name__}")
    return erreurs


def documents(racine, *, extensions=frozenset({".md"})):
    racine = Path(racine).resolve()
    for dossier, enfants, noms in os.walk(racine, followlinks=False):
        enfants[:] = [n for n in enfants if n not in EXCLUS and not n.endswith(".egg-info")]
        for nom in sorted(noms):
            p = Path(dossier) / nom
            if p.suffix.lower() in extensions and not p.is_symlink():
                yield p


def lire_url(url):
    requete = Request(url, headers={"User-Agent": "BENCHAB-publication/1.0", "Range": "bytes=0-1023"})
    with urlopen(requete, timeout=10) as reponse:
        if not 200 <= reponse.status < 400:
            raise ValueError(f"HTTP {reponse.status}")
        reponse.read(1024)


def documentation(racine, *, lecteur_url=lire_url):
    racine = Path(racine).resolve()
    erreurs, urls = [], {}
    for p in documents(racine):
        rel = p.relative_to(racine).as_posix()
        try:
            texte = p.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            erreurs.append(f"{rel} : document UTF-8 illisible")
            continue
        erreurs.extend(f"{rel} : {e}" for e in anomalies_typographiques(texte))
        # Les liens dans les blocs de code sont des exemples, pas de la navigation.
        for brut in cibles_markdown(texte):
            cible = unquote(brut.strip("<>"))
            url = urlsplit(cible)
            if url.scheme in {"http", "https"}:
                urls.setdefault(cible, rel)
            elif url.scheme or url.netloc:
                erreurs.append(f"{rel} : protocole de lien non vérifié")
            else:
                destination = (p.parent / url.path).resolve() if url.path else p
                if not destination.is_relative_to(racine) or destination.is_relative_to(racine / "prive"):
                    erreurs.append(f"{rel} : lien hors partie publique")
                elif not destination.exists():
                    erreurs.append(f"{rel} : lien mort {brut}")
                elif url.fragment and destination.suffix.lower() == ".md":
                    titres = re.findall(r"^#{1,6}\s+(.+)$", destination.read_text(), re.M)
                    ancres = {re.sub(r"[^\w -]", "", t.lower()).replace(" ", "-") for t in titres}
                    if url.fragment not in ancres:
                        erreurs.append(f"{rel} : ancre absente {brut}")
    for url, rel in sorted(urls.items()):
        try:
            lecteur_url(url)
        except (OSError, ValueError):
            # Le message ne restitue ni réponse distante ni éventuels identifiants.
            erreurs.append(f"{rel} : lien HTTP inaccessible {url}")
    return erreurs


def _checkout_benchab(racine):
    """Reconnaît le checkout par son paquet ou son nom de projet, sans lire de données privées."""
    if (racine / "benchab/__init__.py").is_file():
        return True
    try:
        projet = tomllib.loads((racine / "pyproject.toml").read_text(encoding="utf-8"))
        return projet.get("project", {}).get("name") == "benchab"
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def verifier(racine, *, lecteur_url=lire_url, prive=None):
    """Contrôle publication et structure du checkout BENCHAB ; renvoie erreurs, sans écrire ni lire le privé."""
    racine = Path(racine).resolve()
    erreurs_structure = []
    if _checkout_benchab(racine):
        from benchab.qualite.structure import verifier as verifier_structure

        erreurs_structure = verifier_structure(racine)
    return [
        *verifier_frontiere(racine, prive=prive),
        *licences(racine),
        *documentation(racine, lecteur_url=lecteur_url),
        *erreurs_structure,
    ]
