"""Collecte privée de STL et poses assembly.json ; une seule visionneuse autonome pour grille et assemblage."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

from benchab.chemins import atelier_prive, sans_lien
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.visionneuse import Visionneuse, charger_maillage, translation


def _prive(chemin, racine):
    p = sans_lien(Path(chemin).expanduser().absolute())
    if not p.is_relative_to(racine):
        raise ValueError("La collecte d'assemblage doit rester dans le privé explicite")
    return sans_lien(p, racine=racine, strict=True)


def _matrice(valeur):
    matrice = np.asarray(valeur, dtype=float)
    # Le format historique est col-major ; la visionneuse commune attend une matrice 4×4
    # (docs/reference/commandes.md, assemblage-dossier).
    if matrice.shape != (16,):
        raise ValueError("Pose assembly.json : 16 valeurs col-major exigées")
    matrice = matrice.reshape((4, 4), order="F")
    if not np.isfinite(matrice).all() or not np.allclose(matrice[3], [0, 0, 0, 1], rtol=0, atol=1e-12):
        raise ValueError("Pose affine finie exigée dans assembly.json")
    return matrice


def _declaration_apercu(chemin, racine):
    """Lit la liste des maillages autorisés et leur SHA-256, sans ouvrir de maillage."""
    chemin = _prive(chemin, racine)
    if not chemin.is_file() or chemin.stat().st_size > 64 * 1024:
        raise ValueError("Déclaration d’aperçu régulière de 64 Kio au plus requise.")
    contenu = chemin.read_bytes()
    fiche = json.loads(contenu)
    if not isinstance(fiche, dict) or set(fiche) != {"schema", "candidate", "pieces"} or fiche["schema"] != 1:
        raise ValueError("Schéma de déclaration d’aperçu invalide.")
    pieces = fiche["pieces"]
    if not isinstance(pieces, list) or len(pieces) > 255:
        raise ValueError("La déclaration accepte au plus 255 pièces d’assemblage.")
    autorises = {}
    for element in [fiche["candidate"], *pieces]:
        if not isinstance(element, dict) or set(element) != {"fichier", "sha256"}:
            raise ValueError("Chaque maillage doit déclarer son fichier et son SHA-256.")
        fichier, sha = element["fichier"], element["sha256"]
        if (not isinstance(fichier, str) or Path(fichier).name != fichier or not fichier.endswith(".stl")
                or fichier in autorises or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise ValueError("Nom STL unique et SHA-256 complet exigés.")
        autorises[fichier] = sha
    if chemin.read_bytes() != contenu:
        raise ValueError("Déclaration d’aperçu modifiée pendant la lecture.")
    return chemin, hashlib.sha256(contenu).hexdigest(), autorises


def collecter(dossier=None, *, prive, unites, declaration=None):
    """Collecte des STL en mm déclarés et poses primary/extras ; retourne modèles, poses, instances et empreintes.

    Sans dossier, cherche output/stl puis stl puis la racine privée courante.
    Une déclaration d’aperçu identifie chaque fichier par son SHA-256.
    Ne modifie aucun modèle ni réglage.
    """
    if unites != "mm":
        raise ValueError("Déclarer explicitement les unités mm pour cette collecte de STL")
    racine = atelier_prive(prive)
    courant = sans_lien(Path.cwd())
    point = courant if courant.is_relative_to(racine) else racine
    if dossier is None:
        candidats = [point / "output/stl", point / "stl", point]
        dossier = next((p for p in candidats if p.is_dir() and any(p.glob("*.stl"))), point)
    dossier = _prive(dossier, racine)
    if not dossier.is_dir():
        raise ValueError("Dossier STL attendu")
    fichiers = sorted(dossier.glob("*.stl"))
    if not fichiers or len(fichiers) > 256:
        raise ValueError("Un à 256 STL attendus dans le dossier")
    empreintes = {}
    autorises = None
    if declaration is not None:
        fiche, sha_fiche, autorises = _declaration_apercu(declaration, racine)
        empreintes[fiche] = sha_fiche
        if {p.name for p in fichiers} != set(autorises):
            raise ValueError("Maillage non déclaré présent ou fichier déclaré absent du dossier.")
    modeles = {}
    for fichier in fichiers:
        fichier = _prive(fichier, racine)
        if not fichier.is_file() or fichier.stat().st_size > 256 * 1024**2:
            raise ValueError("STL absent ou supérieur à 256 Mio")
        empreintes[fichier] = sha256_fichier(fichier)
        if autorises is not None and empreintes[fichier] != autorises[fichier.name]:
            raise ValueError(f"Empreinte du maillage {fichier.name} différente de la déclaration.")
        modeles[fichier.stem] = charger_maillage(fichier)
    configurations = [dossier.parent / "assembly.json", point / "assembly.json"]
    configuration = next((p for p in configurations if p.is_file() and p.is_relative_to(racine)), None)
    primary, extras = {}, []
    if configuration is not None:
        configuration = _prive(configuration, racine)
        if configuration.stat().st_size > 8 * 1024**2:
            raise ValueError("assembly.json dépasse 8 Mio")
        empreintes[configuration] = sha256_fichier(configuration)
        donnees = json.loads(configuration.read_text())
        if not isinstance(donnees, dict):
            raise ValueError("assembly.json doit être un objet")
        primary, extras = donnees.get("primary", {}), donnees.get("extras", [])
        if not isinstance(primary, dict) or not isinstance(extras, list) or len(extras) > 1000:
            raise ValueError("primary dictionnaire et extras liste bornée exigés")
        if set(primary) - set(modeles):
            raise ValueError("Pose primary sans modèle STL correspondant")
    poses = {nom: _matrice(matrice) for nom, matrice in primary.items()}
    instances = []
    noms = set(modeles)
    for extra in extras:
        if not isinstance(extra, dict) or extra.get("src") not in modeles or not isinstance(extra.get("name"), str):
            raise ValueError("Instance extra sans nom ou modèle src connu")
        nom = extra["name"]
        if not nom or nom in noms:
            raise ValueError("Nom d'instance extra vide ou dupliqué")
        noms.add(nom)
        instances.append({"nom": nom, "modele": extra["src"], "matrice": _matrice(extra.get("asm"))})
    return modeles, poses, instances, empreintes


def creer(dossier, sortie, *, prive, unites, titre="Pièces et assemblage", declaration=None):
    """Produit un HTML privé neuf en mm et son bilan PASS, à partir des sources déclarées si fournies.

    Les empreintes des sources sont revérifiées avant publication. La géométrie
    et les réglages restent intacts. La vue n’atteste pas le montage physique.
    """
    modeles, poses, extras, empreintes = collecter(dossier, prive=prive, unites=unites, declaration=declaration)
    sources = {p.stem: f"{p.name} · SHA-256 {sha}" for p, sha in empreintes.items() if p.suffix == ".stl"}
    vue = Visionneuse(titre, note="STL en mm déclarés ; inspection visuelle, pas validation physique.")
    pas = max(float(np.max(m.extents[:2])) for m in modeles.values()) + 20
    colonnes = math.ceil(math.sqrt(len(modeles)))
    grille, assemblage = {}, {}
    identifiants = {nom: f"Modele{index}" for index, nom in enumerate(modeles)}
    for index, (nom, mesh) in enumerate(modeles.items()):
        centre = (mesh.bounds[0] + mesh.bounds[1]) / 2
        matrice = translation(
            (index % colonnes) * pas - centre[0], (index // colonnes) * pas - centre[1], -mesh.bounds[0, 2]
        )
        vue.modele(identifiants[nom], mesh)
        vue.piece(nom, identifiants[nom], matrice=matrice, info=sources[nom] if declaration is not None else "")
        grille[nom], assemblage[nom] = matrice, poses.get(nom, np.eye(4))
    for extra in extras:
        vue.piece(
            extra["nom"], identifiants[extra["modele"]], matrice=extra["matrice"], groupe="Instances d'assemblage",
            info=sources[extra["modele"]] if declaration is not None else "",
        )
        grille[extra["nom"]], assemblage[extra["nom"]] = None, extra["matrice"]
    if poses or extras:
        vue.mecanisme(
            "assemblage_dossier",
            "Disposition",
            ["Grille", "Assemblage"],
            lambda etat: grille if etat == "Grille" else assemblage,
        )
        vue.mecanismes[-1]["recadrer"] = True
    for fichier, sha in empreintes.items():
        if sha256_fichier(fichier) != sha:
            raise ValueError("Source changée pendant la collecte de l'assemblage")
    livraison = vue.ecrire(sortie, prive=prive)
    return sans_lien(sortie), {
        "statut": "PASS",
        "livraison": livraison,
        "unites": "mm déclarées",
        "modeles": len(modeles),
        "extras": len(extras),
        "assembly_json": bool(poses or extras),
        "validation_physique": False,
        "empreintes": {str(p): sha for p, sha in empreintes.items()},
    }


def main(argv=None, *, prive=None):
    """Collecte le dossier privé CLI ; code 0/PASS, erreurs explicites ; n'ouvre ni navigateur ni accès machine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path, nargs="?")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--unites", choices=["mm"], required=True)
    parser.add_argument("--declaration", type=Path, help="Sources et empreintes de la candidate et des pièces")
    args = parser.parse_args(argv)
    chemin, rapport = creer(
        args.dossier, args.sortie, prive=atelier_prive(prive), unites=args.unites, declaration=args.declaration
    )
    print(json.dumps({"fichier": str(chemin), **rapport}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
