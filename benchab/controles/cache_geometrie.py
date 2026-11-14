"""Cache local et privé des rapports géométriques réutilisables."""

from __future__ import annotations

import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path

from benchab.qualite.empreintes import sha256_fichier


def sha(chemin):
    """Renvoie le SHA-256 hexadécimal du fichier, sans l’écrire."""
    return sha256_fichier(chemin)


class Projet:
    """Adaptateur du cache JSON local de géométrie, sans accès machine."""

    def __init__(self, racine):
        """Prépare le dossier de cache déclaré en mode privé ; racine liée refusée."""
        self.racine = Path(racine).expanduser().absolute()
        if self.racine.is_symlink():
            raise ValueError("Racine de cache liée refusée")
        self.racine.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.racine.chmod(0o700)
        self.d = self.racine

    def charger(self, nom, defaut):
        """Lit l’entrée JSON nommée ou renvoie le défaut si elle est absente."""
        chemin = self.d / nom
        if chemin.is_symlink():
            raise ValueError("Fichier de cache lié refusé")
        if not chemin.exists():
            return defaut
        return json.loads(chemin.read_text())

    def ecrire(self, nom, valeur):
        """Remplace atomiquement l’entrée de cache par le JSON fini donné ; renvoie None."""
        chemin = self.d / nom
        if chemin.is_symlink():
            raise ValueError("Fichier de cache lié refusé")
        contenu = json.dumps(valeur, ensure_ascii=False, allow_nan=False).encode("utf-8")
        descripteur, temporaire = tempfile.mkstemp(prefix=".cache-", dir=self.d)
        try:
            with os.fdopen(descripteur, "wb") as flux:
                flux.write(contenu)
                flux.flush()
                os.fsync(flux.fileno())
            os.replace(temporaire, chemin)
        finally:
            if os.path.exists(temporaire):
                os.unlink(temporaire)

    def journal(self, **_):
        """Accepte les appels de journal de l’adaptateur et renvoie None sans écrire."""
        return None


def _empreintes(racine, chemins):
    resultats = {}
    for chemin in chemins:
        fichier = Path(chemin)
        cible = fichier if fichier.is_absolute() else racine / fichier
        resultats[str(chemin)] = sha(cible)
    return resultats


def cache_poser(projet, cle, objet, entrees, conclusion, preuve, *, parametres=None, outils=(), dependances=(),
                hypotheses=(), invalide_si=()):
    """Enregistre les SHA et dépendances d'un résultat privé, unités natives ; ne prouve aucune validation physique."""
    if not isinstance(dependances, (list, tuple)) or any(not isinstance(d, str) or not d for d in dependances):
        raise ValueError("Dépendances du cache : liste de clés non vides exigée")
    grille = json.loads(files("benchab").joinpath("reference/organisation/grille.json").read_text())
    niveaux = grille["niveaux_de_preuve"]
    if preuve not in niveaux:
        raise ValueError("Niveau de preuve du cache inconnu : " + str(preuve))
    cache = projet.charger("cache.json", {})
    cache[cle] = {
        "objet": objet,
        "entrees": _empreintes(projet.racine, entrees),
        "conclusion": conclusion,
        "niveau_preuve": preuve,
        "dependances": list(dependances),
        "hypotheses": list(hypotheses),
        "invalide_si": list(invalide_si),
        "parametres": parametres,
        "outils": _empreintes(projet.racine, outils),
    }
    projet.ecrire("cache.json", cache)
    return cache[cle]


def cache_lire(projet, cle, *, parametres=None, outils=None, _vus=None):
    """Relit SHA et dépendances ; VALIDE/INVALIDE/ABSENT, sans modifier le cache ni certifier le résultat."""
    vus = set(_vus or ())
    if cle in vus or len(vus) >= 100:
        return {"statut": "INVALIDE", "detail": "cycle ou profondeur excessive des dépendances du cache", "cle": cle}
    vus.add(cle)
    cache = projet.charger("cache.json", {})
    entree = cache.get(cle)
    if not entree:
        return {"statut": "ABSENT"}
    changements = []
    for categorie in ("entrees", "outils"):
        for chemin, empreinte in entree.get(categorie, {}).items():
            fichier = Path(chemin)
            cible = fichier if fichier.is_absolute() else projet.racine / fichier
            try:
                valide = cible.is_file() and sha(cible) == empreinte
            except (OSError, ValueError):
                valide = False
            if not valide:
                changements.append(chemin)
    if parametres is not None and entree.get("parametres") != parametres:
        changements.append("paramètres")
    if outils is not None and set(map(str, outils)) != set(entree.get("outils", {})):
        changements.append("outils déclarés")
    # U64 : un résultat dérivé n'est réutilisable que si ses propres preuves le
    # sont encore (docs/reference/controles.md), même si son fichier direct n'a pas changé.
    dependances = entree.get("dependances", [])
    if not isinstance(dependances, list) or any(not isinstance(d, str) or not d for d in dependances):
        return {**entree, "statut": "INVALIDE", "detail": "dépendances du cache mal formées"}
    invalides = [d for d in dependances if cache_lire(projet, d, _vus=vus)["statut"] != "VALIDE"]
    return {**entree, "statut": "VALIDE" if not changements and not invalides else "INVALIDE",
            "entrees_modifiees": changements, "dependances_invalides": invalides}
