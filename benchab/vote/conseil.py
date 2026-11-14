"""Grille automatique sourcée ; les validateurs existants ne produisent pas de vote en base."""

import hashlib
import json
import stat
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from benchab.projets.preparations import enregistrer as inscrire
from benchab.qualite.ecritures import (
    PublicationNeuve,
    _sans_lien,
    publier_texte_repris,
    remplacer_texte,
    verrou_publication,
)
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.validation import agregation_statut
from benchab.vote.permissions import permissions
from benchab.vote.preuves import statut_rapport
from benchab.vote.preuves import verifier as verifier_dossier

AUTOMATIQUES = ("A2", "A3", "A4", "A6")
AVIS_IA = ("A1", "A5", "A7")


def _json(valeur):
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _lire(base, relatif):
    chemin = Path(relatif)
    chemin = _sans_lien(chemin if chemin.is_absolute() else base.prive / chemin)
    if not chemin.is_relative_to(base.prive.resolve()):
        raise ValueError("Le dossier du vote et ses preuves doivent rester privés.")
    with chemin.open("rb") as flux:
        avant = chemin.stat()
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > 16 * 1024 * 1024:
            raise ValueError("JSON régulier de moins de 16 Mio requis.")
        contenu = flux.read(16 * 1024 * 1024 + 1)
        apres = chemin.stat()

    def signature(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    if signature(avant) != signature(apres) or len(contenu) > 16 * 1024 * 1024:
        raise ValueError("Fichier modifié pendant sa lecture.")
    return chemin, json.loads(contenu), hashlib.sha256(contenu).hexdigest()


def charger_conseil():
    """Lit la grille embarquée du conseil et vérifie ses sept membres et leurs questions."""
    conseil = json.loads(files("benchab").joinpath("reference/organisation/conseil.json").read_text())
    membres = conseil["administrateurs"]
    if sorted(m["id"] for m in membres) != sorted((*AUTOMATIQUES, *AVIS_IA)):
        raise ValueError("Exactement les sept administrateurs sont requis.")
    if any(not isinstance(m.get("question"), str) or not m["question"].strip() for m in membres):
        raise ValueError("Chaque administrateur doit avoir sa question.")
    return conseil


def _sources_preparation(base, fiche, empreintes):
    cibles = set()
    for champ in ("tranche_3mf", "plaques_3mf"):
        source = fiche.get(champ)
        if not isinstance(source, dict) or not isinstance(source.get("fichier"), str):
            if champ == "tranche_3mf":
                raise ValueError("Le 3MF tranché doit être identifié dans la préparation.")
            continue
        brut = Path(source["fichier"])
        chemin = _sans_lien(brut if brut.is_absolute() else base.prive / brut)
        if not chemin.is_relative_to(base.prive.resolve()) or not chemin.is_file():
            raise ValueError("Source de préparation absente ou extérieure au privé.")
        empreinte = sha256_fichier(chemin)
        if empreinte != source.get("sha256"):
            raise ValueError("Source de préparation altérée : " + champ)
        empreintes[chemin] = empreinte
        cibles.add(empreinte)
    return cibles


def _points_a_revoir(rapport):
    points = []

    def ajouter(point):
        if point not in points:
            points.append(point)

    def parcourir(objet):
        if isinstance(objet, dict):
            for zone in objet.get("non_revues", []) if isinstance(objet.get("non_revues"), list) else []:
                ajouter("Zone sans revue valide : " + str(zone))
            statut = str(objet.get("statut", objet.get("status", ""))).upper()
            if statut and statut not in {"PASS", "OK", "CONFORME"} and objet.get("controle"):
                ajouter(str(objet["controle"]) + " : " + statut + " — " + str(objet.get("detail", ""))[:500])
            for valeur in objet.values():
                parcourir(valeur)
        elif isinstance(objet, list):
            for valeur in objet:
                parcourir(valeur)

    parcourir(rapport)
    return points[:100], max(0, len(points) - 100)


def _controle(base, specification, empreintes, cibles):
    if (
        not isinstance(specification, dict)
        or not isinstance(specification.get("id"), str)
        or not specification["id"].strip()
    ):
        raise ValueError("Contrôle identifié requis.")
    roles = specification.get("administrateurs", list(AUTOMATIQUES))
    obligatoire = specification.get("obligatoire", True)
    if not isinstance(roles, list) or not roles or set(roles) - set(AUTOMATIQUES):
        raise ValueError("Un contrôle doit couvrir au moins un administrateur automatique.")
    if type(obligatoire) is not bool:
        raise ValueError("Le caractère obligatoire d'un contrôle doit être explicite.")
    resultat = {
        "id": specification["id"],
        "administrateurs": roles,
        "obligatoire": obligatoire,
        "fichier": specification.get("fichier"),
        "sha256": specification.get("sha256"),
    }
    try:
        chemin, rapport, empreinte = _lire(base, specification["fichier"])
        if empreinte != specification.get("sha256"):
            raise ValueError("Empreinte du rapport absente ou périmée.")
        empreintes[chemin] = empreinte
        resultat["fichier"] = chemin.relative_to(base.prive.resolve()).as_posix()
        if not isinstance(rapport, dict):
            raise ValueError("Un rapport structuré identifiant son entrée est requis.")
        liaison = rapport.get("entree_sha256") or rapport.get("source_sha256")
        if isinstance(rapport.get("_rapport"), dict):
            liaison = rapport["_rapport"].get("source_sha256") or liaison
        if liaison not in cibles:
            raise ValueError("Rapport non lié à une entrée courante de la préparation.")
        resultat["statut"] = agregation_statut(rapport)
        if statut_rapport(rapport) == "non conforme" and resultat["statut"] == "PASS":
            resultat["statut"] = "FAIL"
        resultat["motif"] = "Rapport numérique lu ; aucune validation physique déduite."
        resultat["points_a_revoir"], resultat["points_non_affiches"] = _points_a_revoir(rapport)
    except (OSError, ValueError, TypeError, KeyError) as erreur:
        resultat["statut"] = "NON VÉRIFIÉ"
        resultat["motif"] = str(erreur)
    return resultat


def _voix(controles, risques, avis):
    if not isinstance(avis, dict) or set(avis) - set(AVIS_IA):
        raise ValueError("Seuls A1, A5 et A7 reçoivent un avis IA ; les quatre autres voix sont automatiques.")
    votes = {}
    for role in AUTOMATIQUES:
        preuves = controles if role == "A4" else [c for c in controles if role in c["administrateurs"]]
        etats = [c["statut"] for c in preuves]
        reserve = role == "A4" and risques
        voix = (
            "contre"
            if "FAIL" in etats
            else "pour"
            if etats and set(etats) == {"PASS"} and not reserve
            else "abstention"
        )
        votes[role] = {
            "vote": voix,
            "origine": "contrôles déterministes",
            "motif": "; ".join(c["id"] + " : " + c["statut"] for c in preuves) or "Aucun contrôle fourni pour ce rôle.",
            "preuves": [c["fichier"] for c in preuves],
        }
        if role == "A4":
            votes[role]["points_a_revoir"] = [
                point
                for c in controles
                for point in (
                    c.get("points_a_revoir") or ([c["id"] + " : " + c["motif"]] if c["statut"] != "PASS" else [])
                )
            ] + risques
    for role in AVIS_IA:
        proposition = avis.get(role, {"vote": "abstention", "motif": "Avis IA absent."})
        if not isinstance(proposition, dict) or proposition.get("vote") not in {"pour", "contre", "abstention"}:
            raise ValueError("Avis IA invalide : " + role)
        if not isinstance(proposition.get("motif"), str) or not proposition["motif"].strip():
            raise ValueError("Chaque avis IA exige un motif explicite.")
        votes[role] = {**proposition, "origine": "avis IA fourni ; aucune indépendance présumée"}
    absents = [role for role in ("A2", "A3", "A6") if not any(role in c["administrateurs"] for c in controles)]
    if absents:
        votes["A4"]["points_a_revoir"].extend(role + " : aucun contrôle fourni." for role in absents)
        if votes["A4"]["vote"] == "pour":
            votes["A4"]["vote"] = "abstention"
            votes["A4"]["motif"] += " ; couverture des contrôles incomplète."
    return votes


def _abstentions_relecture(preavis):
    return {
        role: {
            "vote": "abstention",
            "origine": "aucun vote formalisé : relecture critique requise",
            "motif": "Préavis calculé conservé séparément ; aucun quorum actif avant validation critique.",
            "preavis": preavis[role]["vote"],
            "preuves": list(preavis[role].get("preuves", [])),
            **({"points_a_revoir": list(preavis[role].get("points_a_revoir", []))} if role == "A4" else {}),
        }
        for role in (*AUTOMATIQUES, *AVIS_IA)
    }


def _entrees_relecture(base, empreintes, dossier):
    racine = base.prive.resolve()
    return {
        chemin.relative_to(racine).as_posix(): empreinte
        for chemin, empreinte in sorted(empreintes.items(), key=lambda item: str(item[0]))
        if chemin != dossier
    }


def _markdown_surveillance(vote):
    mention = (
        "NON PRÊT — points hypothétiques issus d'un prémortem ; aucune observation ni impression n'est confirmée."
        if vote["statut"] != "APPROUVÉ"
        else "Points hypothétiques issus d'un prémortem ; aucune observation ni impression n'est confirmée."
    )
    lignes = [
        "# Points de surveillance de l'envoi",
        "",
        mention,
        "",
        f"Préparation : `{vote['preparation_id']}`",
        f"Vote : `{vote['id']}` — {vote['statut']}",
        "",
    ]
    for index, scenario in enumerate(vote["dixieme_homme"]["scenarios"], 1):
        signe = scenario["signe_precoce"]
        preparation = scenario["preparation"]
        lignes.extend(
            [
                f"## Scénario hypothétique {index} : {scenario['cause_plausible']}",
                "",
                f"- Couverture déclarée : {scenario['couverture']['type']} — {scenario['couverture']['reference']}",
                f"- Hauteur ou couche à surveiller : {signe['hauteur_ou_couche']}",
                f"- Zone : {signe['zone']}",
                f"- Signe précoce : {signe['a_surveiller']}",
                f"- Réglages de secours : {preparation['reglages_secours']}",
                f"- Plan de réimpression : {preparation['plan_reimpression']}",
                "",
            ]
        )
    lignes.extend(["Ces scénarios ne sont pas des risques observés et ne prouvent aucune validation physique.", ""])
    return "\n".join(lignes)


def _chemin_envoi(base, fiche, preparation_id, identifiant_vote):
    relatif = fiche.relative_to(base.prive.resolve())
    morceaux = relatif.parts
    if "preparations" in morceaux:
        racine_projet = base.prive.joinpath(*morceaux[: morceaux.index("preparations")])
    else:
        racine_projet = fiche.parent
    return racine_projet / "envois" / f"{preparation_id}-{identifiant_vote}.md"


def produire(base, dossier):
    """Calcule et publie un vote sourcé ; une relance après panne reprend son identifiant sans ajouter de voix."""
    chemin_dossier, donnees, sha_dossier = _lire(base, dossier)
    if not isinstance(donnees, dict) or donnees.get("schema") != 1:
        raise ValueError("Dossier de vote schema 1 requis.")
    if "votes" in donnees:
        raise ValueError("Les voix sont calculées ; fournir seulement les avis IA A1, A5 et A7.")
    if not isinstance(donnees.get("objet"), str) or not donnees["objet"].strip():
        raise ValueError("Objet exact du vote requis.")
    if not isinstance(donnees.get("preparation"), str):
        raise ValueError("Fiche de préparation privée requise.")
    fiche, contenu_fiche, sha_fiche = _lire(base, donnees["preparation"])
    reference = inscrire(base, fiche)
    empreintes = {chemin_dossier: sha_dossier, fiche: sha_fiche}
    cibles = _sources_preparation(base, contenu_fiche, empreintes)
    specifications = donnees.get("controles", [])
    if not isinstance(specifications, list):
        raise ValueError("Liste de contrôles requise.")
    controles = [_controle(base, s, empreintes, cibles) for s in specifications]
    if len({c["id"] for c in controles}) != len(controles):
        raise ValueError("Identifiants de contrôles dupliqués.")
    risques = []
    for champ in ("risques", "inconnues"):
        valeurs = donnees.get(champ, [])
        if not isinstance(valeurs, list) or any(not isinstance(r, str) or not r.strip() for r in valeurs):
            raise ValueError("Les risques non traités doivent être une liste explicite de descriptions.")
        risques.extend(valeurs)
    preavis = _voix(controles, risques, donnees.get("avis_ia", {}))
    conseil = charger_conseil()
    for membre in conseil["administrateurs"]:
        preavis[membre["id"]]["question"] = membre["question"]
    permission = permissions(donnees)
    from benchab.vote.permissions import G

    qualite = verifier_dossier(
        base.prive,
        {**donnees, "votes": preavis},
        permission["categorie_risque"],
        G["niveaux_de_preuve"],
        preavis,
        empreintes=empreintes,
    )
    dixieme_requis = all(vote["vote"] == "pour" for vote in preavis.values()) or (
        bool(controles) and all(controle["statut"] == "PASS" for controle in controles)
    )
    from benchab.vote import relecture

    critique = relecture.verifier(
        base,
        donnees.get("relecture_critique"),
        sha_fiche,
        _entrees_relecture(base, empreintes, chemin_dossier),
        controles=controles,
        dixieme_requis=dixieme_requis,
        scenario_test=donnees.get("scenario_test") is True,
    )
    if critique["fichier"] and critique["sha256"]:
        empreintes[base.prive / critique["fichier"]] = critique["sha256"]

    risques_critique = (
        [
            f"NON VÉRIFIÉ — {risque['description']} (constat : {risque['constat']} ; "
            f"source : {risque['source']['fichier']})"
            for risque in critique["risques_nouveaux"]
        ]
        if critique["valide"]
        else []
    )
    risques_vote = risques + risques_critique
    votes = (
        _voix(controles, risques_vote, donnees.get("avis_ia", {}))
        if critique["valide"]
        else _abstentions_relecture(preavis)
    )
    for membre in conseil["administrateurs"]:
        votes[membre["id"]]["question"] = membre["question"]
    if critique["valide"]:
        qualite = verifier_dossier(
            base.prive,
            {**donnees, "votes": votes},
            permission["categorie_risque"],
            G["niveaux_de_preuve"],
            votes,
            empreintes=empreintes,
        )
    manquants = [c["id"] for c in controles if c["obligatoire"] and c["statut"] != "PASS"]
    if not controles:
        manquants.append("aucun contrôle fourni")
    if qualite:
        manquants.extend(qualite["manquants"])
    pour = sum(v["vote"] == "pour" for v in votes.values())
    if not critique["valide"]:
        manquants.extend("Relecture critique : " + manque for manque in critique["manquants"])
        statut = "EN ATTENTE DE RELECTURE CRITIQUE"
    elif manquants:
        statut = "BLOQUÉ (Vérification nécessaire)"
    elif pour != 7:
        statut = "EN ATTENTE D'ARBITRAGE DE L'UTILISATEUR"
    elif permission["autorisation_requise"]:
        statut = "EN ATTENTE D'AUTORISATION (vote favorable 7/7, action soumise à l'accord de l'utilisateur)"
    else:
        statut = "APPROUVÉ"
    resultat = {
        "objet": donnees["objet"],
        "preparation_id": reference["preparation_id"],
        "projet_id": reference["projet_id"],
        "dossier": chemin_dossier.relative_to(base.prive.resolve()).as_posix(),
        "dossier_sha256": sha_dossier,
        "fiche_sha256": sha_fiche,
        "votes": votes,
        "preavis": preavis,
        "preavis_pour": sum(vote["vote"] == "pour" for vote in preavis.values()),
        "pour": pour,
        "pourcentage": round(100 * pour / 7, 1),
        "statut": statut,
        "controles": controles,
        "risques_non_traites": risques_vote,
        "risques_nouveaux_non_verifies": critique["risques_nouveaux"] if critique["valide"] else [],
        "relecture_critique": {
            "statut": "PASS" if critique["valide"] else "EN ATTENTE",
            "fichier": critique["fichier"],
            "sha256": critique["sha256"],
            "provenance": critique["provenance"],
            "manquants": critique["manquants"],
        },
        "dixieme_homme": critique["dixieme_homme"] if critique["valide"] else None,
        "points_surveillance": critique["points_surveillance"] if critique["valide"] else [],
        "verifications_manquantes": manquants,
        "permissions": permission,
        "qualite_decision": qualite,
        "mode_execution": (
            "Préavis calculés séparément ; aucune voix formelle avant relecture critique GROS sans historique. "
            "Aucune exécution ni validation physique."
        ),
        "grille_version": "1.0.0",
        "conseil_sha256": hashlib.sha256(_json(conseil).encode()).hexdigest(),
        "outil_sha256": {p.name: sha256_fichier(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "action_autorisee_par_vote": False,
        "recommandation": donnees.get("recommandation", ""),
        "scenario_test": bool(donnees.get("scenario_test", False)),
        "entrees": {p.relative_to(base.prive.resolve()).as_posix(): h for p, h in empreintes.items()},
    }
    if "dossier_preuves" in donnees:
        resultat["dossier_preuves"] = donnees["dossier_preuves"]
    if len(_json(resultat).encode()) > 1_000_000:
        raise ValueError("Vote trop volumineux : conserver les rapports dans leurs fichiers et résumer les preuves.")
    resultat["empreinte_vote"] = hashlib.sha256(_json(resultat).encode()).hexdigest()
    return _publier_vote(base, fiche, resultat, empreintes, pour, votes)


def _publier_vote(base, fiche, resultat, empreintes, pour, votes):
    empreinte = resultat["empreinte_vote"]
    cache = base.prive / "cache"
    with verrou_publication(cache, f"vote-{empreinte}.lock"):
        # Un reçu privé survit au commit et à l’export. Il disparaît après
        # publication pour préserver les votes distincts voulus (référence données).
        recu = _sans_lien(cache / f"vote-{empreinte}.en-cours")
        if recu.exists():
            identifiant = recu.read_text(encoding="utf-8").strip()
            if uuid.UUID(identifiant).hex != identifiant:
                raise ValueError("Reçu de vote invalide")
        else:
            identifiant = uuid.uuid4().hex
            remplacer_texte(recu, identifiant + "\n")
        existant = base.connexion.execute(
            "SELECT donnees_json FROM votes WHERE id=?", (identifiant,),
        ).fetchone()
        preparation_id = resultat["preparation_id"]
        destination = _chemin_envoi(base, fiche, preparation_id, identifiant)
        if existant:
            resultat = json.loads(existant[0])
            if resultat["empreinte_vote"] != empreinte:
                raise ValueError("Reçu de vote incompatible avec le dossier")
            base.exporter()
        else:
            resultat["id"] = identifiant
            resultat["date"] = datetime.now(timezone.utc).isoformat()
            if resultat["dixieme_homme"]:
                resultat["envoi_markdown"] = destination.relative_to(base.prive.resolve()).as_posix()
                if _sans_lien(destination).exists():
                    raise ValueError("Le dossier d’envoi existe déjà")
            verdict = "pour" if pour == 7 else (
                "contre" if any(v["vote"] == "contre" for v in votes.values()) else "abstention"
            )
            with base.transaction() as connexion:
                connexion.execute("BEGIN IMMEDIATE")
                if any(sha256_fichier(p) != h for p, h in empreintes.items()):
                    raise ValueError("Dossier, préparation, critique ou preuve modifiés pendant le vote.")
                connexion.execute("INSERT INTO votes VALUES(?,?,?,?,?,?)", (
                    identifiant, preparation_id, resultat["date"], resultat["grille_version"], verdict, _json(resultat),
                ))
        if resultat["dixieme_homme"]:
            if any(sha256_fichier(p) != h for p, h in empreintes.items()):
                raise ValueError("Dossier, préparation, critique ou preuve modifiés avant publication.")
            publier_texte_repris(destination, _markdown_surveillance(resultat), entrees=list(empreintes))
        recu.unlink()
    return resultat


def historique(base):
    """Les anciens avis restent consultables ; un rejeu ne remplace aucune ligne."""
    return [json.loads(ligne[0]) for ligne in base.connexion.execute("SELECT donnees_json FROM votes ORDER BY date,id")]


def etat(base):
    """Retourne les derniers votes par projet et objet, avec les décisions non approuvées."""
    derniers = {}
    for vote in historique(base):
        derniers[vote["projet_id"], vote["objet"]] = vote
    return {
        "decisions_en_attente": [v["objet"] for v in derniers.values() if v["statut"] != "APPROUVÉ"],
        "derniers_votes": list(derniers.values()),
    }


def rendre(vote):
    """Rend en Markdown le statut, les voix sur sept, le pourcentage et les preuves du vote."""
    from benchab.vote.preuves import rendre as rendre_preuves

    lignes = [
        "# " + vote["objet"],
        "",
        vote["statut"],
        "",
        f"Conseil : {vote['pour']}/7 ({vote['pourcentage']} %).",
        f"Préavis non formels : {vote.get('preavis_pour', 0)}/7.",
        "Relecture critique : " + vote.get("relecture_critique", {}).get("statut", "absente"),
        "Permission : "
        + (
            "accord personnel requis"
            if vote["permissions"]["autorisation_requise"]
            else "le vote ne donne aucun droit d’exécution"
        ),
        "",
    ]
    lignes.extend(
        "- Relecture nécessaire : " + item for item in vote.get("relecture_critique", {}).get("manquants", [])
    )
    if vote.get("points_surveillance"):
        lignes.extend(["", "## Points hypothétiques de surveillance", ""])
        for index, point in enumerate(vote["points_surveillance"], 1):
            lignes.append(
                f"- Scénario {index} — couche/hauteur : {point['hauteur_ou_couche']} ; zone : {point['zone']} ; "
                f"à surveiller : {point['a_surveiller']}"
            )
    for role, avis in sorted(vote["votes"].items()):
        lignes.extend([f"- {role} : {avis['vote']} — {avis['motif']}", "  " + avis["question"]])
    lignes.extend(["", *rendre_preuves(vote), "", "Aucune validation physique ni action exécutée.", ""])
    return "\n".join(lignes)


def preparer(base, fiche, sortie, objet, *, controles=(), contribution_id=None):
    """Prépare un dossier privé à compléter, chemins sans unité ; aucune voix ni preuve physique inventée.

    Une contribution choisie ajoute des risques NON VÉRIFIÉS, jamais une relecture GROS ni un vote.
    """
    chemin, _, _ = _lire(base, fiche)
    cible = _sans_lien(sortie)
    if not cible.is_relative_to(base.prive.resolve()):
        raise ValueError("Le dossier de vote reste dans l'espace privé.")
    if not isinstance(objet, str) or not objet.strip():
        raise ValueError("Objet exact du vote requis.")
    preuves = []
    for controle in controles:
        fichier, _, empreinte = _lire(base, controle)
        preuves.append(
            {
                "id": fichier.stem,
                "fichier": fichier.relative_to(base.prive.resolve()).as_posix(),
                "sha256": empreinte,
                "obligatoire": True,
            }
        )
    donnees = {
        "schema": 1,
        "objet": objet,
        "type_action": "verification",
        "preparation": chemin.relative_to(base.prive.resolve()).as_posix(),
        "controles": preuves,
        "risques": [],
        "inconnues": [],
        "avis_ia": {
            role: {"vote": "abstention", "motif": "Avis IA à rédiger ; aucune indépendance présumée."}
            for role in AVIS_IA
        },
        "a_completer": [
            "Avis A1, A5, A7",
            "Risques et inconnues explicites",
            "Dossier de preuves à compléter pour une décision significative B/C",
            "Relecture critique externe GROS sans historique, liée aux SHA de la fiche et des entrées courantes",
        ],
    }
    if contribution_id is not None:
        from benchab.vote.contributions import derniere

        synthese = derniere(base, objet, identifiant=contribution_id)
        if synthese is None:
            raise ValueError("Contribution non simulée introuvable pour cet objet et cet identifiant.")
        # Le collecteur rapporte des hypothèses, sans prouver les objections
        # résolues ni remplacer la relecture GROS (docs/reference/vote.md).
        donnees["contribution_prealable"] = {
            "id": synthese["id"], "etat": synthese["etat"],
            "independance": synthese["independance"], "autorisation": False,
        }
        donnees["inconnues"] = list(dict.fromkeys("NON VÉRIFIÉ : " + x for x in synthese["inconnues"]))
        risques = [p["versions"][-1]["risques"] for p in synthese["solutions"]]
        risques.extend("Preuve de contribution à vérifier : " + str(p.get("source", "non déclarée"))
                       for p in synthese["verifications_manquantes"])
        donnees["risques"] = list(dict.fromkeys("NON VÉRIFIÉ : " + x for x in risques))
    with PublicationNeuve([chemin], [cible]) as publication:
        publication.temporaire(cible).write_text(_json(donnees) + "\n", encoding="utf-8")
        publication.publier()
    return cible


def arbitrer(base, objet, choix):
    """Consigne un choix explicite (textes sans unité) ; DÉCLARÉ ou FAIL, jamais un vote ni une autorisation."""
    if any(not isinstance(texte, str) or not texte.strip() or len(texte) > 2000 for texte in (objet, choix)):
        raise ValueError("Objet et choix explicites, non vides et bornés à 2 000 caractères requis.")
    resultat = {"type": "arbitrage_declare", "objet": objet.strip(), "choix": choix.strip(),
                "statut": "DÉCLARÉ", "autorisation": False, "vote": None, "validation_physique": False}
    base.ecrire("INSERT INTO decisions VALUES (?,NULL,?,?,?,?)", (
        uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), resultat["objet"],
        "choix d'opérateur déclaré à BENCHAB", _json(resultat),
    ))
    return resultat


def comparer(base, dossier):
    """Compare les approbations des options ; le choix reste une décision de l'utilisateur."""
    from benchab.vote.permissions import G

    chemin, donnees, empreinte = _lire(base, dossier)
    options = donnees.get("options") if isinstance(donnees, dict) else None
    if not isinstance(options, dict) or len(options) < 2:
        raise ValueError("Au moins deux options nommées et distinctes sont requises.")
    if any(not isinstance(label, str) or not label.strip() for label in options):
        raise ValueError("Chaque option doit avoir un nom explicite.")
    if not isinstance(donnees.get("question"), str) or not donnees["question"].strip():
        raise ValueError("Question exacte requise.")
    # Prévalider les documents avant de calculer et d'enregistrer les avis.
    for fichier in options.values():
        _lire(base, fichier)
    votes = {label: produire(base, fichier) for label, fichier in options.items()}
    projets = {vote["projet_id"] for vote in votes.values()}
    if len(projets) != 1:
        raise ValueError("Les options d'une question doivent concerner le même projet.")
    contexte = {**donnees, "objet": donnees["question"], "options": list(options)}
    permission = permissions(contexte)
    empreintes_question = {chemin: empreinte}
    qualite = verifier_dossier(
        base.prive,
        contexte,
        permission["categorie_risque"],
        G["niveaux_de_preuve"],
        {a["id"]: a for a in charger_conseil()["administrateurs"]},
        empreintes=empreintes_question,
    )
    attente_critique = [label for label, vote in votes.items() if vote["relecture_critique"]["statut"] != "PASS"]
    admissibles = [
        label
        for label, vote in votes.items()
        if vote["relecture_critique"]["statut"] == "PASS" and not vote["statut"].startswith("BLOQUÉ")
    ]
    unanimes = [label for label in admissibles if votes[label]["pour"] == 7]
    if attente_critique:
        statut = "EN ATTENTE DE RELECTURE CRITIQUE"
    elif qualite["manquants"] or not admissibles:
        statut = "BLOQUÉ (Vérification nécessaire)"
    elif len(unanimes) == 1:
        statut = "EN ATTENTE DE L'UTILISATEUR (recommandation unanime : " + unanimes[0] + ")"
    else:
        statut = "EN ATTENTE D'ARBITRAGE DE L'UTILISATEUR (options à départager)"
    resultat = {
        "type": "comparaison_votes",
        "objet": donnees["question"],
        "statut": statut,
        "options": {
            label: {
                "voix": vote["pour"],
                "pourcentage": vote["pourcentage"],
                "preavis": vote["preavis_pour"],
                "vote_formalise": vote["relecture_critique"]["statut"] == "PASS",
                "statut": vote["statut"],
                "vote_id": vote["id"],
            }
            for label, vote in votes.items()
        },
        "recommandation": unanimes[0] if len(unanimes) == 1 and not attente_critique else "",
        "resume": "Approbations par option : "
        + " · ".join(
            (
                f"{label} préavis {vote['preavis_pour']}/7 ; attente relecture critique"
                if vote["relecture_critique"]["statut"] != "PASS"
                else f"{label} {vote['pourcentage']:g} % ({vote['pour']}/7)"
            )
            for label, vote in votes.items()
        ),
        "limite": "Approbations non exclusives par option ; aucune autorisation d'exécution.",
        "qualite_decision": qualite,
        "dossier": chemin.relative_to(base.prive.resolve()).as_posix(),
        "dossier_sha256": empreinte,
        "projet_id": next(iter(projets)),
        "entrees": {p.relative_to(base.prive.resolve()).as_posix(): h for p, h in empreintes_question.items()},
    }
    if "dossier_preuves" in donnees:
        resultat["dossier_preuves"] = donnees["dossier_preuves"]
    if len(_json(resultat).encode()) > 1_000_000:
        raise ValueError("Comparaison trop volumineuse : résumer les preuves et conserver les rapports à part.")
    identifiant = uuid.uuid4().hex
    with base.transaction() as connexion:
        if any(sha256_fichier(p) != h for p, h in empreintes_question.items()):
            raise ValueError("Question ou preuve D2 modifiée pendant la comparaison.")
        if any(
            sha256_fichier(base.prive / relatif) != h
            for vote in votes.values()
            for relatif, h in vote["entrees"].items()
        ):
            raise ValueError("Une entrée des options a changé pendant la comparaison.")
        connexion.execute(
            "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
            (
                identifiant,
                resultat["projet_id"],
                datetime.now(timezone.utc).isoformat(),
                resultat["objet"],
                resultat["dossier"],
                _json(resultat),
            ),
        )
    return resultat
