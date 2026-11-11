"""Construit et fige les fiches de préparation dans un projet BENCHAB privé."""

import argparse
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF
from benchab.donnees.base import Base
from benchab.fabrication.resistance import auditer_projet
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.mesure.environnement import collecter, verifier_rapport
from benchab.projets.preparations import enregistrer, valider_nom
from benchab.qualite.ecritures import PublicationNeuve, renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.validation import statuts

OBJECTIFS = ("qualite", "equilibre", "rapide", "resistance")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


def identifiant(valeur: str) -> str:
    """Valide un identifiant de préparation ; retourne sa forme canonique ou lève `ValueError`.

    L’entrée est un nom sans unité ; la validation suit les règles des fiches
    privées et ne crée ni ne modifie de projet. Voir le [guide de préparation](../../docs/guides/preparer-une-piece.md).
    """
    return valider_nom(valeur)


def _sans_lien(chemin: str | Path) -> Path:
    """Valide un chemin existant sous sa racine de confiance ; aucune source ni réglage modifié."""
    from benchab.chemins import sans_lien

    return sans_lien(chemin, strict=True)


def _fichier_prive(base: Base, chemin: str | Path, projet: Path | None = None) -> Path:
    brut = Path(chemin).expanduser()
    candidats = (
        [brut]
        if brut.is_absolute()
        else [projet / brut, base.prive / brut]
        if projet is not None
        else [base.prive / brut]
    )
    erreur = None
    for candidat in candidats:
        try:
            fichier = _sans_lien(candidat)
        except (OSError, ValueError) as exc:
            erreur = exc
            continue
        if fichier.is_relative_to(base.prive.resolve()) and fichier.is_file():
            return fichier
    if erreur is not None:
        raise ValueError("Source absente ou liée dans l’espace privé.") from erreur
    raise ValueError("Les sources de préparation doivent rester dans base.prive.")


def chemin_relatif(base: Base, chemin: str | Path) -> str:
    """Renvoie le chemin POSIX d’un fichier privé existant, après contrôle des liens.

    Reçoit la base BENCHAB et un chemin absolu ou relatif ; retourne un chemin
    relatif à `base.prive`, ou lève `ValueError`. Aucun fichier n’est lu ni écrit.
    Voir le guide [préparer une pièce](../../docs/guides/preparer-une-piece.md).
    """
    fichier = _fichier_prive(base, chemin)
    return fichier.relative_to(base.prive.resolve()).as_posix()


def commandes(contenu: bytes) -> str:
    """Hache les commandes G-code en SHA-256 après retrait des commentaires.

    Reçoit des octets décodés en UTF-8 avec remplacement des caractères invalides ;
    retourne 64 caractères hexadécimaux. La normalisation sert à comparer les
    commandes entre préparations, sans modifier ni valider physiquement le G-code.
    Voir le [guide de préparation](../../docs/guides/preparer-une-piece.md).
    """
    lignes = contenu.decode("utf-8", "ignore").splitlines()
    code = "\n".join(ligne.split(";")[0].strip() for ligne in lignes if ligne.split(";")[0].strip())
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def gcodes(archive: str | Path) -> dict:
    """Hache les plaques par numéro ; index/CRC bornés, aucune lecture ni mutation hors archive."""
    fichier = Path(archive)
    with Archive3MF(fichier) as paquet:
        noms = paquet.noms()
        if not noms:
            raise ValueError("Archive ZIP vide.")
        # Le rejet des alias est numérique ; l'ordre lexical conserve les fiches historiques.
        plaques = sorted(plaques_gcode_archive(paquet), key=lambda item: item[1])
        noms_plaques = {membre for _, membre in plaques}
        paquet.verifier(membres=(nom for nom in noms if nom not in noms_plaques))
        resultat = {}
        for _, membre in plaques:
            contenu = paquet.lire(membre)
            numero = re.fullmatch(r"Metadata/plate_(\d+)\.gcode", membre).group(1)
            resultat[numero] = {
                "sha256": hashlib.sha256(contenu).hexdigest(),
                "commandes_sha256": commandes(contenu),
            }
    return resultat


def verifier_gcodes(plaques: dict) -> None:
    """Refuse une fiche sans plaque ou sans commande G-code exploitable.

    Reçoit le dictionnaire de plaques de `gcodes` ; retourne `None` si chaque
    empreinte de commandes est présente, sinon lève `ValueError`. Ne modifie
    aucune archive ni préparation figée. Voir le [guide de préparation](../../docs/guides/preparer-une-piece.md).
    """
    if not plaques:
        raise ValueError("Aucun G-code de plaque dans le fichier.")
    vide = hashlib.sha256(b"").hexdigest()
    for numero, plaque in plaques.items():
        if plaque["commandes_sha256"] == vide:
            raise ValueError(f"Plaque {numero} sans commandes dans le fichier.")


def statuts_rapport(rapport: str | Path) -> dict:
    """Compte récursivement les statuts d’un rapport JSON de contrôle.

    Reçoit le chemin d’un rapport JSON objet ou liste ; retourne un compte par
    statut, avec `NON VÉRIFIÉ` si aucun statut n’est déclaré. Le rapport reste
    inchangé ; voir le parcours de préparation du [guide](../../docs/guides/preparer-une-piece.md).
    """
    contenu = json.loads(Path(rapport).read_text(encoding="utf-8"))
    if not isinstance(contenu, (dict, list)):
        raise ValueError("Rapport attendu : objet ou liste JSON.")
    valeurs = statuts(contenu) or ["NON VÉRIFIÉ"]
    return {statut: valeurs.count(statut) for statut in sorted(set(valeurs))}


def comparer_envoi(envoi: dict, attendu: dict) -> dict:
    """Associe les plaques d’un envoi aux empreintes de commandes préparées.

    Reçoit deux dictionnaires indexés par numéro de plaque ; retourne un libellé
    par plaque indiquant l’unicité, l’ambiguïté ou l’écart des empreintes SHA-256.
    Cette comparaison numérique ne prouve ni l’exécution du fichier ni un résultat
    physique et ne modifie pas les fiches. Voir le [guide de préparation](../../docs/guides/preparer-une-piece.md).
    """
    resultat = {}
    for numero, plaque in envoi.items():
        correspondances = [
            identifiant_plaque
            for identifiant_plaque, source in attendu.items()
            if source.get("commandes_sha256") == plaque.get("commandes_sha256")
        ]
        repetitions = sum(source.get("commandes_sha256") == plaque.get("commandes_sha256") for source in envoi.values())
        if len(correspondances) == 1 and repetitions == 1:
            resultat[numero] = f"commandes identiques à la préparation, plaque {correspondances[0]}"
        elif correspondances:
            resultat[numero] = "AMBIGU : association non unique aux plaques " + ", ".join(correspondances)
        else:
            resultat[numero] = "COMMANDES DIFFÉRENTES de la préparation : à expliquer"
    return resultat


def mesurer_environnement(base: Base, cible: str | Path) -> dict:
    """Écrit un rapport environnement neuf et valide sa cohérence sans lancer de commande externe."""
    destination = Path(cible).expanduser()
    if any(element.is_symlink() for element in (destination, *destination.parents)):
        raise ValueError("Lien symbolique dans le rapport environnement refusé.")
    parent = _sans_lien(destination.parent)
    if not parent.is_relative_to(base.prive.resolve()) or not parent.is_dir():
        raise ValueError("Dossier privé du rapport environnement invalide.")
    destination = parent / destination.name
    if destination.exists():
        raise FileExistsError("Rapport environnement déjà existant.")
    rapport = verifier_rapport(collecter(base.prive))
    with destination.open("x", encoding="utf-8") as flux:
        json.dump(rapport, flux, ensure_ascii=False, indent=2, allow_nan=False)
        flux.write("\n")
    return rapport


def selection_optimisation(
    base: Base,
    rapport: str | Path,
    candidat: str,
    accepter_risques: bool = False,
) -> dict:
    """Valide une décision d’optimisation scellée et ses fichiers sans importer leur code."""
    chemin_rapport = _fichier_prive(base, rapport)
    dossier = chemin_rapport.parent
    contenu = chemin_rapport.read_bytes()
    donnees = json.loads(contenu)
    sceau = dossier / "SCELLE.json"
    if sceau.is_symlink():
        raise ValueError("Sceau d’optimisation lié symboliquement.")
    if sceau.exists():
        sceau = _fichier_prive(base, sceau)
        contenu_sceau = json.loads(sceau.read_bytes())
        if (
            not isinstance(contenu_sceau, dict)
            or contenu_sceau.get("rapport_sha256") != hashlib.sha256(contenu).hexdigest()
        ):
            raise ValueError("Rapport optimisation altéré : sceau non conforme.")
    if not isinstance(donnees, dict) or type(donnees.get("schema")) is not int or donnees.get("schema") != 1:
        raise ValueError("Schéma optimisation invalide.")
    if donnees.get("objectif") not in OBJECTIFS:
        raise ValueError("Objectif optimisation invalide.")
    candidats = donnees.get("candidats")
    if not isinstance(candidats, list) or any(not isinstance(item, dict) for item in candidats):
        raise ValueError("Candidats optimisation invalides.")
    identifiants = [item.get("id") for item in candidats]
    if any(not isinstance(item, str) for item in identifiants) or len(set(identifiants)) != len(identifiants):
        raise ValueError("Identifiants candidats absents ou dupliqués.")
    choix = [item for item in candidats if item.get("id") == candidat]
    if len(choix) != 1:
        raise ValueError("Candidat absent ou ambigu.")
    choix = choix[0]
    decision = choix.get("decision")
    if decision not in ("GO", "À revoir", "A_REVOIR"):
        raise ValueError("Candidat NO GO ou décision inconnue.")
    if decision != "GO" and not accepter_risques:
        raise ValueError("Candidat à revoir : accepter_risques requis.")
    equite = choix.get("comparaison_equitable")
    if not isinstance(equite, dict) or equite.get("conforme") is not True:
        raise ValueError("Comparaison non équitable ou non vérifiée.")

    def verifier_fichier(specification: dict) -> Path:
        if not isinstance(specification, dict) or not isinstance(specification.get("fichier"), str):
            raise ValueError("Artefact optimisation absent.")
        empreinte = specification.get("sha256")
        if not isinstance(empreinte, str) or _SHA256.fullmatch(empreinte) is None:
            raise ValueError("Empreinte optimisation absente ou invalide.")
        chemin = Path(specification["fichier"])
        if not chemin.is_absolute():
            chemin = dossier / chemin
        cible = _fichier_prive(base, chemin)
        if not cible.is_relative_to(dossier.resolve()):
            raise ValueError("Artefact hors du dossier optimisation.")
        if sha256_fichier(cible) != empreinte.lower():
            raise ValueError("Artefact optimisation absent ou altéré.")
        return cible

    machine = donnees.get("machine")
    if not isinstance(machine, dict) or not isinstance(machine.get("id"), str):
        raise ValueError("Profil maître non identifié.")
    profil = verifier_fichier({"fichier": machine.get("profil"), "sha256": machine.get("profil_sha256")})
    source = verifier_fichier(donnees.get("source"))
    plaques_path = verifier_fichier(choix.get("plaques_3mf"))
    tranche_path = verifier_fichier(choix.get("tranche_3mf"))
    controle_path = verifier_fichier(choix["controle_fichier"]) if "controle_fichier" in choix else None
    preuves = machine.get("preuves_sources", [])
    if not isinstance(preuves, list):
        raise ValueError("Preuves sources du profil invalides.")
    preuves_validees = []
    relatifs = set()
    for preuve in preuves:
        if not isinstance(preuve, dict) or not isinstance(preuve.get("relatif"), str):
            raise ValueError("Preuve source sans chemin relatif.")
        relatif = Path(preuve["relatif"])
        cle = relatif.as_posix().casefold()
        if relatif.is_absolute() or ".." in relatif.parts or len(relatif.parts) < 2 or relatif.parts[0] != "references":
            raise ValueError("Chemin de preuve source hors references.")
        if cle in relatifs:
            raise ValueError("Preuve source dupliquée.")
        relatifs.add(cle)
        fichier = verifier_fichier({"fichier": preuve.get("fichier"), "sha256": preuve.get("sha256")})
        preuves_validees.append({"relatif": relatif.as_posix(), "chemin": fichier, "sha256": preuve["sha256"].lower()})
    return {
        "rapport": chemin_rapport,
        "rapport_sha256": hashlib.sha256(contenu).hexdigest(),
        "donnees": donnees,
        "candidat": choix,
        "profil": profil,
        "source": source,
        "plaques": plaques_path,
        "tranche": tranche_path,
        "controle": controle_path,
        "risques_acceptes": bool(accepter_risques and decision != "GO"),
        "preuves_sources": preuves_validees,
    }


def _charger_materiaux(base: Base) -> dict | None:
    chemin = base.prive / "donnees/materiaux/resistance.json"
    if chemin.is_symlink():
        raise ValueError("Registre matière lié symboliquement refusé.")
    if not chemin.exists():
        return None
    resultat = json.loads(chemin.read_text(encoding="utf-8"))
    if not isinstance(resultat, dict) or not isinstance(resultat.get("materiaux"), dict):
        raise ValueError("Registre matière privé invalide.")
    return resultat


def creer(
    base: Base,
    projet: str | Path,
    ident: str,
    plaques: str | Path | None = None,
    tranche: str | Path | None = None,
    *,
    sources=(),
    modeles=(),
    controles=(),
    precedente: str | None = None,
    note: str = "",
    environnement: dict | None = None,
    optimisation: str | Path | None = None,
    candidat: str | None = None,
    accepter_risques: bool = False,
) -> dict:
    """Crée une préparation non envoyée, la fige puis l’inscrit dans Base."""
    if not isinstance(base, Base):
        raise TypeError("Une instance Base BENCHAB explicite est requise.")
    identifiant(ident)
    racine = base.prive.resolve()
    dossier_projet = _sans_lien(projet)
    if not dossier_projet.is_relative_to(racine) or not dossier_projet.is_dir():
        raise ValueError("Le projet doit être un dossier privé sous base.prive.")
    dossier_preparations = dossier_projet / "preparations"
    if dossier_preparations.is_symlink():
        raise ValueError("Dossier preparations symbolique refusé.")
    final = dossier_preparations / ident
    en_cours = dossier_preparations / f"{ident}.en-cours"
    if final.exists() or final.is_symlink() or en_cours.exists() or en_cours.is_symlink():
        raise FileExistsError("Préparation existante : aucune préparation n’est écrasée.")
    selection = None
    if optimisation is not None:
        if candidat is None or plaques is not None or tranche is not None:
            raise ValueError("Une optimisation exige --candidat et fournit elle-même plaques et tranche.")
        selection = selection_optimisation(base, optimisation, candidat, accepter_risques)
        plaques_source = selection["plaques"]
        tranche_source = selection["tranche"]
    else:
        if candidat is not None or accepter_risques:
            raise ValueError("--candidat et --accepter-risques exigent --optimisation.")
        if plaques is None or tranche is None:
            raise ValueError("Plaques et tranche requis hors sélection d’optimisation.")
        plaques_source = _fichier_prive(base, plaques, dossier_projet)
        tranche_source = _fichier_prive(base, tranche, dossier_projet)
    sources = [_fichier_prive(base, chemin, dossier_projet) for chemin in sources]
    modeles = [_fichier_prive(base, chemin, dossier_projet) for chemin in modeles]
    controles = [_fichier_prive(base, chemin, dossier_projet) for chemin in controles]
    if precedente is not None:
        identifiant(precedente)
        fiche_precedente = dossier_projet / "preparations" / precedente / "PREPARATION.json"
        fiche_precedente = _fichier_prive(base, fiche_precedente)
        contenu_precedent = json.loads(fiche_precedente.read_text(encoding="utf-8"))
        if not isinstance(contenu_precedent, dict) or contenu_precedent.get("id") != precedente:
            raise ValueError("Fiche précédente incohérente.")
        if not isinstance(contenu_precedent.get("gcode"), dict):
            raise ValueError("G-code de la préparation précédente absent.")

    with Archive3MF(plaques_source) as archive:
        if not archive.noms():
            raise ValueError("3MF des plaques vide ou avec des membres dupliqués.")
        archive.verifier()
    gcode = gcodes(tranche_source)
    verifier_gcodes(gcode)
    rapports_controles = {chemin: statuts_rapport(chemin) for chemin in controles}
    registre_materiaux = _charger_materiaux(base)
    bilan_resistance = auditer_projet(dossier_projet, tranche_source, registre_materiaux)
    if bilan_resistance["statut"] == "FAIL":
        raise ValueError("Résistance calculée insuffisante : préparation refusée.")

    options = [dossier_projet / "BESOIN.md", dossier_projet / "src/parametres.py"]
    chemin_registre = base.prive / "donnees/materiaux/resistance.json"
    if chemin_registre.exists():
        options.append(chemin_registre)
    initiales = {
        chemin: sha256_fichier(chemin) for chemin in [plaques_source, tranche_source, *sources, *modeles, *controles]
    }
    if selection is not None:
        fichiers_optimisation = [
            selection["rapport"],
            selection["profil"],
            selection["source"],
            selection["plaques"],
            selection["tranche"],
            *[preuve["chemin"] for preuve in selection["preuves_sources"]],
        ]
        if selection["controle"] is not None:
            fichiers_optimisation.append(selection["controle"])
        initiales.update({chemin: sha256_fichier(chemin) for chemin in fichiers_optimisation})
    initiales.update({chemin: sha256_fichier(chemin) if chemin.is_file() else None for chemin in options})

    def verifier_sources() -> None:
        for chemin, empreinte in initiales.items():
            actuelle = sha256_fichier(chemin) if chemin.is_file() else None
            if actuelle != empreinte:
                raise ValueError("Source modifiée pendant la préparation : " + chemin.relative_to(racine).as_posix())

    en_cours.mkdir(parents=True)
    plaques_nom, tranche_nom = "plaques.3mf", "tranche.3mf"
    shutil.copy2(plaques_source, en_cours / plaques_nom)
    shutil.copy2(tranche_source, en_cours / tranche_nom)
    if (
        sha256_fichier(en_cours / plaques_nom) != initiales[plaques_source]
        or sha256_fichier(en_cours / tranche_nom) != initiales[tranche_source]
    ):
        raise ValueError("Entrée modifiée pendant la copie ; dossier .en-cours conservé.")
    verifier_sources()
    if selection is not None:
        dossier_optimisation = en_cours / "optimisation"
        dossier_optimisation.mkdir()
        noms_optimisation = {
            selection["rapport"]: "rapport.json",
            selection["profil"]: "profil-maitre.json",
            selection["source"]: "source.3mf",
            selection["plaques"]: "plaques.3mf",
            selection["tranche"]: "tranche.3mf",
        }
        if selection["controle"] is not None:
            noms_optimisation[selection["controle"]] = "controle.json"
        for preuve in selection["preuves_sources"]:
            noms_optimisation[preuve["chemin"]] = Path(preuve["relatif"])
        for source, nom in noms_optimisation.items():
            cible = dossier_optimisation / nom
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)
            if sha256_fichier(cible) != initiales[source]:
                raise ValueError("Preuve optimisation modifiée pendant la copie ; dossier .en-cours conservé.")
        verifier_sources()
    rapport_environnement = verifier_rapport(environnement or collecter(base.prive))
    if rapport_environnement["code"] == 2:
        raise ValueError("Collecte environnement en échec ; dossier .en-cours conservé.")
    (en_cours / "environnement.json").write_text(
        json.dumps(rapport_environnement, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    fiche = {
        "id": ident,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "projet": dossier_projet.relative_to(racine).as_posix(),
        "statut": "préparation candidate — NON imprimée",
        "demande": {"BESOIN.md": sha256_fichier(options[0])} if options[0].is_file() else {},
        "sources": {chemin.relative_to(racine).as_posix(): sha256_fichier(chemin) for chemin in sources},
        "modeles": {chemin.relative_to(racine).as_posix(): sha256_fichier(chemin) for chemin in modeles},
        "plaques_3mf": {
            "fichier": (en_cours / plaques_nom).relative_to(racine).as_posix(),
            "sha256": sha256_fichier(en_cours / plaques_nom),
        },
        "tranche_3mf": {
            "fichier": (en_cours / tranche_nom).relative_to(racine).as_posix(),
            "sha256": sha256_fichier(en_cours / tranche_nom),
        },
        "gcode": gcodes(en_cours / tranche_nom),
        "controles": {
            chemin.relative_to(racine).as_posix(): {
                "sha256": sha256_fichier(chemin),
                "statuts": rapports_controles[chemin],
            }
            for chemin in controles
        },
        "environnement": rapport_environnement["mesures"],
        "environnement_code": rapport_environnement["code"],
        "environnement_controles": rapport_environnement["resultats"],
        "note": note,
        "resistance": bilan_resistance,
        "envoi": None,
        "retour_physique": None,
    }
    if selection is not None:
        fiche["optimisation"] = {
            "rapport": "optimisation/rapport.json",
            "rapport_sha256": selection["rapport_sha256"],
            "candidat": selection["candidat"]["id"],
            "objectif": selection["donnees"]["objectif"],
            "risques_acceptes": selection["risques_acceptes"],
            "profil": "optimisation/profil-maitre.json",
            "source": "optimisation/source.3mf",
            "plaques": "optimisation/plaques.3mf",
            "tranche": "optimisation/tranche.3mf",
            "controle": "optimisation/controle.json" if selection["controle"] is not None else None,
            "preuves_sources": [
                {"relatif": preuve["relatif"], "fichier": f"optimisation/{preuve['relatif']}"}
                for preuve in selection["preuves_sources"]
            ],
        }
    if precedente is not None:
        anciennes = contenu_precedent["gcode"]
        fiche["comparaison"] = {
            "precedente": precedente,
            "plaques": {
                numero: (
                    "commandes identiques"
                    if numero in anciennes
                    and numero in gcode
                    and anciennes[numero].get("commandes_sha256") == gcode[numero].get("commandes_sha256")
                    else "COMMANDES DIFFÉRENTES : à expliquer"
                )
                for numero in sorted(set(anciennes) | set(gcode))
            },
        }
    fiche["plaques_3mf"]["fichier"] = fiche["plaques_3mf"]["fichier"].replace(f"{ident}.en-cours", ident)
    fiche["tranche_3mf"]["fichier"] = fiche["tranche_3mf"]["fichier"].replace(f"{ident}.en-cours", ident)
    chemin_fiche = en_cours / "PREPARATION.json"
    with PublicationNeuve([], [chemin_fiche]) as publication:
        temporaire = publication.temporaire(chemin_fiche)
        temporaire.write_text(json.dumps(fiche, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        verifier_sources()
        if (
            sha256_fichier(en_cours / plaques_nom) != initiales[plaques_source]
            or sha256_fichier(en_cours / tranche_nom) != initiales[tranche_source]
        ):
            raise ValueError("Copie modifiée avant publication ; dossier .en-cours conservé.")
        publication.publier()
    renommer_exclusif(en_cours, final)
    fiche_finale = final / "PREPARATION.json"
    resultat = enregistrer(base, fiche_finale)
    return {**resultat, "fiche": fiche_finale, "resistance": bilan_resistance}


def main(argv: list[str] | None = None, *, base: Base | None = None) -> int:
    """Gère les préparations privées (mm, s) ; codes CLI conservés, aucune base ni réglage modifié implicitement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path)
    actions = parser.add_subparsers(dest="commande", required=True)
    creer_parser = actions.add_parser("creer", help="Crée une nouvelle préparation candidate")
    creer_parser.add_argument("--projet", type=Path, required=True)
    creer_parser.add_argument("--id", required=True)
    creer_parser.add_argument("--plaques", type=Path)
    creer_parser.add_argument("--tranche", type=Path)
    creer_parser.add_argument("--optimisation", type=Path)
    creer_parser.add_argument("--candidat")
    creer_parser.add_argument("--accepter-risques", action="store_true")
    creer_parser.add_argument("--sources", nargs="*")
    creer_parser.add_argument("--modeles", nargs="*")
    creer_parser.add_argument("--controles", nargs="*")
    creer_parser.add_argument("--precedente")
    creer_parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    instance = base
    fermer = False
    try:
        if instance is None:
            if args.prive is None:
                parser.error("--prive requis hors appel depuis le CLI BENCHAB.")
            instance = Base(args.prive, creer=False)
            fermer = True
        if args.commande == "creer":
            rapport = creer(
                instance,
                args.projet,
                args.id,
                args.plaques,
                args.tranche,
                sources=args.sources or [],
                modeles=args.modeles or [],
                controles=args.controles or [],
                precedente=args.precedente,
                note=args.note,
                optimisation=args.optimisation,
                candidat=args.candidat,
                accepter_risques=args.accepter_risques,
            )
            print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in rapport.items()}, ensure_ascii=False))
            return 0
    except (OSError, ValueError, TypeError, KeyError, BadZipFile) as erreur:
        parser.error(str(erreur))
    finally:
        if fermer and instance is not None:
            instance.fermer()
    return 2
