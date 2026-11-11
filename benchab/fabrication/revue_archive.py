"""Parcours public de revue et contrôle final d’une archive déjà tranchée."""

import argparse
import json
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.controles import envoi, revue_locale
from benchab.qualite.ecritures import ecrire_json_neuf, sortie_durable, valider_chemins
from benchab.qualite.empreintes import sha256_fichier


def executer(archive, declaration, *, sortie, prive, revue_source=None, geometrie=None, reference=None):
    """Revoit puis contrôle une archive figée ; chemins privés, sans lancer de trancheuse ni agir sur la machine.

    Les empreintes lient la revue à l’archive et à la géométrie. Retourne le statut
    final PASS/WARN/FAIL/NON VÉRIFIÉ et les chemins des rapports créés. Les décisions
    de la revue source sont conservées si elles sont encore liées aux mêmes SHA.
    """
    archive = sans_lien(Path(archive).expanduser(), strict=True)
    declaration = sans_lien(Path(declaration).expanduser(), strict=True)
    if geometrie is None:
        # Reprendre explicitement le défaut du contrôle historique afin d’en figer le SHA.
        candidate = declaration.parent / "preuves/controles-pieces.json"
        geometrie = candidate if candidate.is_file() else None
    geometrie = sans_lien(Path(geometrie).expanduser(), strict=True) if geometrie else None
    reference = sans_lien(Path(reference).expanduser(), strict=True) if reference else None
    revue_source = sans_lien(Path(revue_source).expanduser(), strict=True) if revue_source else None
    sortie = sortie_durable(sortie, prive=prive)
    if sortie.suffix.lower() != ".json":
        raise ValueError("Le rapport final doit porter l’extension .json.")
    initial = sortie.with_name(sortie.stem + "-initial.json")
    revue = sortie.with_name(sortie.stem + "-revue.json")
    faits = sortie.with_name(sortie.stem + "-faits.json")
    # zones_locales résout les preuves depuis le dossier de revue (docs/reference/fabrication.md).
    if revue_source is not None and revue_source.parent != sortie.parent.resolve():
        raise ValueError("Le rapport doit rester voisin de la revue source pour préserver ses preuves relatives.")

    entrees = [archive, declaration, *([geometrie] if geometrie else []), *([reference] if reference else [])]
    source = None
    if revue_source:
        entrees.append(revue_source)
        source = json.loads(revue_source.read_text(encoding="utf-8"))
        entrees.extend(_verifier_preuves_revue(source, revue_source, prive))
    valider_chemins(entrees, [initial, revue, faits, sortie])
    empreintes = {str(chemin): sha256_fichier(chemin) for chemin in entrees}
    initial_resultat = envoi.controler(
        archive, declaration, reference=reference, geometrie=geometrie
    )
    env = initial_resultat.get("zones_locales")
    if not isinstance(env, dict) or not isinstance(env.get("tranche_sha256"), str):
        raise ValueError("Le contrôle initial ne fournit pas l’empreinte de l’archive tranchée.")
    if env["tranche_sha256"] != empreintes[str(archive)]:
        raise ValueError("L’archive a changé pendant le contrôle initial.")
    if revue_source:
        if (
            source.get("tranche_sha256") != env["tranche_sha256"]
            or source.get("geometrie_sha256") != env.get("geometrie_sha256")
        ):
            raise ValueError("La revue source n’est plus liée aux empreintes de l’archive ou de la géométrie.")
    ecrire_json_neuf(initial, initial_resultat, entrees=entrees)
    resultat_revue = revue_locale.revoir(
        archive, initial, revue_source or revue, faits
    )
    if revue_source and sha256_fichier(revue_source) != empreintes[str(revue_source)]:
        raise ValueError("La revue source a changé pendant la lecture ; aucune nouvelle revue publiée.")
    revue_locale.ecrire(resultat_revue, revue, faits)
    controle_final = envoi.controler(
        archive,
        declaration,
        reference=reference,
        geometrie=geometrie,
        revue_locale=revue,
    )
    if {str(chemin): sha256_fichier(chemin) for chemin in entrees} != empreintes:
        raise ValueError("Une source a changé pendant le parcours ; résultats finaux refusés.")
    rapport = {
        "statut": controle_final["statut"],
        "code": controle_final["code"],
        "validation_physique": False,
        "archive": str(archive),
        "sources_sha256": empreintes,
        "rapport_initial": str(initial),
        "rapports_sha256": {
            "initial": sha256_fichier(initial),
            "revue": sha256_fichier(revue),
            "faits_revue": sha256_fichier(faits),
        },
        "revue": str(revue),
        "faits_revue": str(faits),
        "rapport_final": controle_final,
        "resume_revue": {
            "decisions_conservees": len(resultat_revue["gardees"]),
            "justifiees_automatiquement": len(resultat_revue["auto"]),
            "a_revoir": len(resultat_revue["reste"]),
        },
    }
    ecrire_json_neuf(sortie, rapport, entrees=[*entrees, initial, revue, faits])
    return {**rapport, "rapport": str(sortie)}


def _verifier_preuves_revue(source, revue_source, prive):
    """Refuse une décision déjà relue dont une preuve référencée a disparu ou changé de SHA."""
    zones = source.get("zones") if isinstance(source, dict) else None
    if not isinstance(zones, dict):
        raise ValueError("La revue source doit contenir un dictionnaire de zones.")
    chemins = set()
    for identifiant, avis in zones.items():
        if not isinstance(avis, dict) or avis.get("decision") not in {"corrige", "justifie"}:
            continue
        preuves = avis.get("preuves")
        if not isinstance(preuves, list) or not preuves:
            raise ValueError("Une décision de revue source exige ses preuves empreintées : " + str(identifiant))
        for preuve in preuves:
            if not isinstance(preuve, dict) or not isinstance(preuve.get("fichier"), str):
                raise ValueError("Référence de preuve invalide dans la revue source : " + str(identifiant))
            chemin = sortie_durable(revue_source.parent / preuve["fichier"], prive=prive)
            if sha256_fichier(chemin) != preuve.get("sha256"):
                raise ValueError("Preuve de revue source absente ou périmée : " + str(identifiant))
            chemins.add(chemin)
    return sorted(chemins)


def main(argv=None, *, prive):
    """Exécute le parcours explicite ; codes 0/PASS, 1/WARN, 2/FAIL ou NON VÉRIFIÉ, aucune trancheuse."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Archive 3MF déjà tranchée")
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--revue-source", type=Path, help="Revue humaine liée aux SHA courants")
    parser.add_argument("--geometrie", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--sortie", type=Path, required=True, help="Nouveau rapport JSON privé")
    args = parser.parse_args(argv)
    try:
        rapport = executer(
            args.archive,
            args.declaration,
            sortie=args.sortie,
            prive=prive,
            revue_source=args.revue_source,
            geometrie=args.geometrie,
            reference=args.reference,
        )
        print(json.dumps({cle: rapport[cle] for cle in (
            "statut", "code", "rapport", "rapport_initial", "revue", "faits_revue", "resume_revue"
        )}, ensure_ascii=False, indent=2))
        return rapport["code"]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as erreur:
        parser.error(str(erreur))
