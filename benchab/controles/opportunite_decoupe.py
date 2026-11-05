"""Évalue les déclencheurs et l'obligation de comparer une découpe déclarée."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from benchab.controles.rapports import RapportCourant
from benchab.qualite.statuts import code_statut

AXES = (
    "supports",
    "surplombs",
    "couture",
    "temps",
    "aspect",
    "solidite_assemblage",
    "estimation_reussite",
)
INDICATEURS = {
    "forme": ("retrecissement_naturel", "volumes_fusionnes"),
    "imprimabilite": ("supports", "surplombs", "couture_visible_ou_fonctionnelle", "depasse_plateau"),
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TAILLE_MAX = 16 * 1024 * 1024


def _chemin_prive(racine: Path, relatif: str | Path) -> Path:
    brut = Path(relatif).expanduser()
    if brut.is_absolute():
        raise ValueError("Un chemin relatif à base.prive est requis.")
    racine = racine.resolve(strict=True)
    chemin = racine / brut
    for parent in (chemin, *chemin.parents):
        if parent == racine.parent:
            break
        if parent.is_symlink():
            raise ValueError("Lien symbolique dans une source privée refusé.")
        if parent == racine:
            break
    chemin = chemin.resolve(strict=True)
    if not chemin.is_relative_to(racine):
        raise ValueError("La source doit rester dans base.prive.")
    return chemin


def _empreinte_prive(racine: Path, relatif: str | Path) -> tuple[Path, str]:
    """Empreinte un fichier privé par blocs, sans charger un maillage en mémoire."""
    chemin = _chemin_prive(racine, relatif)
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("Source non régulière.")
        empreinte = hashlib.sha256()
        while bloc := flux.read(1024 * 1024):
            empreinte.update(bloc)
        apres = os.fstat(flux.fileno())
    signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
    signature_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
    if signature_avant != signature_apres:
        raise ValueError("Source modifiée pendant sa lecture.")
    return chemin, empreinte.hexdigest()


def _lire_prive(racine: Path, relatif: str | Path) -> tuple[Path, bytes, str]:
    """Lit un JSON privé borné, sans lien symbolique ni mutation concurrente."""
    chemin = _chemin_prive(racine, relatif)
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > _TAILLE_MAX:
            raise ValueError("Source non régulière ou supérieure à 16 Mio.")
        contenu = flux.read(_TAILLE_MAX + 1)
        apres = os.fstat(flux.fileno())
    signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
    signature_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
    if len(contenu) > _TAILLE_MAX or signature_avant != signature_apres:
        raise ValueError("Source modifiée pendant sa lecture.")
    return chemin, contenu, hashlib.sha256(contenu).hexdigest()


def _texte(valeur) -> bool:
    return isinstance(valeur, str) and bool(valeur.strip())


def _rapport(racine: Path, declaration, famille: str, sha_piece: str, manquants: list[str]):
    if not isinstance(declaration, dict):
        manquants.append(f"rapport {famille} absent ou non interprétable")
        return None
    fichier, empreinte = declaration.get("fichier"), declaration.get("sha256")
    if not _texte(fichier) or Path(fichier).is_absolute():
        manquants.append(f"rapport {famille} : chemin privé relatif requis")
        return None
    if not isinstance(empreinte, str) or not _SHA256.fullmatch(empreinte):
        manquants.append(f"rapport {famille} : SHA-256 manquant ou invalide")
        return None
    try:
        chemin, contenu, sha = _lire_prive(racine, fichier)
        if sha != empreinte:
            manquants.append(f"rapport {famille} : empreinte périmée")
            return None
        donnees = json.loads(contenu)
    except (OSError, ValueError, UnicodeError) as erreur:
        manquants.append(f"rapport {famille} illisible : {erreur}")
        return None
    if not isinstance(donnees, dict) or donnees.get("statut") != "PASS":
        manquants.append(f"rapport {famille} sans statut PASS vérifié")
        return None
    metadata = donnees.get("_rapport")
    if not isinstance(metadata, dict) or metadata.get("source_sha256") != sha_piece:
        manquants.append(f"rapport {famille} non lié à l'empreinte de la pièce courante")
        return None
    declencheurs = donnees.get("declencheurs")
    if not isinstance(declencheurs, dict):
        manquants.append(f"rapport {famille} : déclencheurs structurés absents")
        return None
    return chemin.relative_to(racine).as_posix(), sha, declencheurs


def _axes_complets(valeur) -> bool:
    if not isinstance(valeur, dict) or set(valeur) != set(AXES):
        return False
    if any(not _texte(valeur[axe]) for axe in AXES if axe != "estimation_reussite"):
        return False
    estimation = valeur["estimation_reussite"]
    return (
        isinstance(estimation, dict)
        and set(estimation) == {"evaluation", "justification", "mesuree"}
        and _texte(estimation["evaluation"])
        and _texte(estimation["justification"])
        and estimation["mesuree"] is False
    )


def evaluer(racine_privee: str | Path, donnees) -> dict:
    """Contrôle les indicateurs sourcés et la comparaison, sans choisir ni générer une découpe."""
    racine = Path(racine_privee).resolve(strict=True)
    manquants: list[str] = []
    declencheurs_actifs = []
    rapports_valides = {}
    if not isinstance(donnees, dict) or donnees.get("schema") != 1:
        return _rapport_final(
            statut="NON VÉRIFIÉ",
            declencheurs=[],
            rapports={},
            comparaison=None,
            manquants=["Entrée JSON schema 1 absente ou mal formée."],
        )

    piece = donnees.get("piece")
    piece_valide = False
    fichier_piece = None
    if not isinstance(piece, dict) or not _texte(piece.get("fichier")):
        manquants.append("Source de la pièce absente.")
    else:
        try:
            chemin_piece, sha_piece = _empreinte_prive(racine, piece["fichier"])
            if piece.get("sha256") != sha_piece:
                manquants.append("Source de la pièce : empreinte périmée ou absente.")
            else:
                piece_valide = True
                fichier_piece = chemin_piece.relative_to(racine).as_posix()
        except (OSError, ValueError) as erreur:
            manquants.append(f"Source de la pièce illisible : {erreur}")

    specifications = donnees.get("rapports")
    if not isinstance(specifications, dict):
        specifications = {}
    for famille, champs in INDICATEURS.items():
        source = _rapport(racine, specifications.get(famille), famille, sha_piece if piece_valide else "", manquants)
        if source is None:
            continue
        chemin, sha, declencheurs = source
        rapports_valides[famille] = {"fichier": chemin, "sha256": sha}
        inconnus = set(declencheurs) - set(champs)
        if inconnus:
            manquants.append(f"indicateurs {famille} non pris en charge : {', '.join(sorted(inconnus))}")
        for champ in champs:
            present = declencheurs.get(champ)
            if type(present) is not bool:
                manquants.append(f"indicateur {famille}.{champ} absent ou non booléen")
                continue
            if present and piece_valide:
                declencheurs_actifs.append({"famille": famille, "id": champ, "rapport": chemin})

    comparaison = _comparer(
        racine,
        donnees.get("comparaison"),
        piece.get("sha256") if piece_valide else None,
        fichier_piece,
        manquants,
    )
    if declencheurs_actifs and not comparaison["complete"]:
        statut = "FAIL"
        manquants.append("Déclencheur présent : comparaison complète avec une variante découpée obligatoire.")
    elif manquants:
        statut = "NON VÉRIFIÉ"
    else:
        statut = "PASS"
    return _rapport_final(
        statut=statut,
        declencheurs=declencheurs_actifs,
        rapports=rapports_valides,
        comparaison=comparaison,
        manquants=manquants,
    )


def _comparer(racine, comparaison, sha_piece, fichier_piece, manquants) -> dict:
    if not isinstance(comparaison, dict):
        return {"complete": False, "axes_requis": list(AXES), "monobloc": None, "variantes_decoupees": []}
    monobloc_axes = comparaison.get("monobloc")
    monobloc = _axes_complets(monobloc_axes)
    variantes = comparaison.get("variantes_decoupees")
    if not isinstance(variantes, list):
        variantes = []
    ids = []
    variantes_resultats = []
    for variante in variantes:
        ident = variante.get("id") if isinstance(variante, dict) else None
        axes = variante.get("axes") if isinstance(variante, dict) else None
        source_sha = variante.get("source_sha256") if isinstance(variante, dict) else None
        pieces = variante.get("pieces") if isinstance(variante, dict) else None
        pieces_valides = []
        chemins_pieces = set()
        axes_valides = _axes_complets(axes)
        if not isinstance(pieces, list) or len(pieces) < 2:
            manquants.append(
                f"variante {ident or '(sans identifiant)'} : au moins deux pièces découpées référencées sont requises"
            )
        else:
            for piece_decoupee in pieces:
                if not isinstance(piece_decoupee, dict):
                    manquants.append(f"variante {ident or '(sans identifiant)'} : référence de pièce invalide")
                    continue
                fichier = piece_decoupee.get("fichier")
                empreinte = piece_decoupee.get("sha256")
                try:
                    chemin, sha = _empreinte_prive(racine, fichier)
                except (OSError, ValueError, TypeError) as erreur:
                    manquants.append(f"variante {ident or '(sans identifiant)'} : pièce illisible ({erreur})")
                    continue
                relatif = chemin.relative_to(racine).as_posix()
                if relatif == fichier_piece or relatif in chemins_pieces:
                    manquants.append(
                        f"variante {ident or '(sans identifiant)'} : pièces répétées ou identiques au monobloc"
                    )
                    continue
                chemins_pieces.add(relatif)
                if not isinstance(empreinte, str) or not _SHA256.fullmatch(empreinte) or sha != empreinte:
                    manquants.append(
                        f"variante {ident or '(sans identifiant)'} : empreinte de pièce absente ou périmée"
                    )
                    continue
                pieces_valides.append({"fichier": relatif, "sha256": sha})
        liaison_valide = sha_piece is not None and source_sha == sha_piece
        if not liaison_valide:
            manquants.append(f"variante {ident or '(sans identifiant)'} : liaison à la pièce source absente ou périmée")
        complete = _texte(ident) and axes_valides and len(pieces_valides) >= 2 and liaison_valide
        if _texte(ident):
            ids.append(ident)
        variantes_resultats.append(
            {
                "id": ident if _texte(ident) else None,
                "axes_complets": bool(axes_valides),
                "complete": bool(complete),
                "axes": axes if axes_valides else None,
                "pieces": pieces_valides,
                "source_sha256": source_sha if liaison_valide else None,
            }
        )
    sans_doublons = len(ids) == len(set(ids))
    complete = bool(variantes_resultats) and all(item["complete"] for item in variantes_resultats)
    complete = monobloc and complete and sans_doublons and len(ids) == len(variantes_resultats)
    return {
        "complete": bool(complete),
        "axes_requis": list(AXES),
        "monobloc_complet": monobloc,
        "monobloc": monobloc_axes if monobloc else None,
        "variantes_decoupees": variantes_resultats,
    }


def _entrees_manifeste(racine: Path, source: Path, donnees) -> list[Path]:
    """Retourne les fichiers existants déclarés, à surveiller jusqu'à publication."""
    references = [source]
    if not isinstance(donnees, dict):
        return references
    piece = donnees.get("piece")
    if isinstance(piece, dict):
        references.append(piece.get("fichier"))
    rapports = donnees.get("rapports")
    if isinstance(rapports, dict):
        references.extend(
            declaration.get("fichier") for declaration in rapports.values() if isinstance(declaration, dict)
        )
    comparaison = donnees.get("comparaison")
    variantes = comparaison.get("variantes_decoupees") if isinstance(comparaison, dict) else None
    if isinstance(variantes, list):
        for variante in variantes:
            pieces = variante.get("pieces") if isinstance(variante, dict) else None
            if isinstance(pieces, list):
                references.extend(piece.get("fichier") for piece in pieces if isinstance(piece, dict))

    entrees = [source]
    vues = {source.resolve()}
    for reference in references[1:]:
        if not _texte(reference):
            continue
        try:
            chemin = _chemin_prive(racine, reference)
        except (OSError, ValueError, TypeError):
            continue
        if chemin.is_file() and chemin not in vues:
            entrees.append(chemin)
            vues.add(chemin)
    return entrees


def _rapport_final(*, statut, declencheurs, rapports, comparaison, manquants) -> dict:
    return {
        "controle": "opportunite_decoupe",
        "statut": statut,
        "declencheurs": declencheurs,
        "rapports_sources": rapports,
        "comparaison": comparaison,
        "manquants": list(dict.fromkeys(manquants)),
        "choix_final_humain_requis": bool(declencheurs),
        "choix_automatique": False,
        "decoupe_generee": False,
        "limites": [
            "Les indicateurs proviennent de rapports structurés déclarés ; aucune géométrie n'est segmentée ici.",
            "Les fichiers de variante sont présents et empreintés ; leur dérivation géométrique reste non vérifiée.",
            "La génération et la vérification géométrique relèvent de l'outil prévu au lot 6B.",
            "L'estimation de réussite reste qualitative et explicitement non mesurée.",
            "Aucune validation physique n'est déduite.",
        ],
    }


def main(argv=None, *, base=None, prive=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Manifeste JSON privé des indicateurs et comparaisons.")
    parser.add_argument("--sortie", type=Path, help="Rapport JSON, dans l'espace privé de BENCHAB.")
    parser.add_argument("--prive", type=Path, help="Racine privée, requise en exécution autonome.")
    args = parser.parse_args(argv)
    valeur_privee = base.prive if base is not None else prive or args.prive
    if valeur_privee is None:
        parser.error("Fournir base.prive ou --prive.")
    racine = Path(valeur_privee).expanduser().resolve(strict=True)
    sortie = args.sortie or racine / "rapports/opportunite-decoupe.json"
    try:
        source_argument = args.source.expanduser()
        if source_argument.is_absolute():
            source_argument = source_argument.resolve(strict=True).relative_to(racine)
        source, contenu, _ = _lire_prive(racine, source_argument)
        donnees = json.loads(contenu)
        chemin_sortie = sortie.expanduser().resolve()
        if not chemin_sortie.is_relative_to(racine):
            raise ValueError("Le rapport doit rester dans l'espace privé.")
        entrees = _entrees_manifeste(racine, source, donnees)
        rapport = RapportCourant(chemin_sortie, entrees, "opportunite_decoupe")
        resultat = evaluer(racine, donnees)
        resultat["code"] = code_statut(resultat["statut"])
        rapport.publier(resultat)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as erreur:
        print(f"Contrôle refusé : {erreur}", file=sys.stderr)
        return 2
    print(json.dumps(resultat, ensure_ascii=False, indent=1))
    return resultat["code"]


if __name__ == "__main__":
    raise SystemExit(main())
