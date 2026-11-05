"""Contrats d'extraction historiques : faits vérifiables, fidélité originale toujours distincte."""

import hashlib
import json

import pytest

from benchab.connaissances import memoire, provenance
from benchab.connaissances import sqlite as adaptation
from benchab.donnees.base import Base


def _fait(source="suivi/journal/ancien.md", *, sha_original="a" * 64, revision=None, identifiant="fait-1"):
    src = {"chemin": source, "sha256": sha_original}
    if revision:
        src["revision_originale"] = revision
    return {
        "id": identifiant,
        "titre": "Jeu observé",
        "description": "Le jeu déclaré est 0,3 mm dans ce scénario.",
        "domaine": "assemblage",
        "niveau_preuve": "OBSERVATION_UTILISATEUR",
        "conditions": "Axe vertical, pièce témoin.",
        "exclusions": "Ne généralise pas à une autre matière.",
        "unites": {"jeu": "mm"},
        "faits": {"jeu": 0.3},
        "sources": [src],
        "validite": "contextuelle",
    }


def _inscrire(base, item, copies=None):
    base.ecrire(
        "INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)",
        (
            item["id"],
            item.get("description", item["titre"]),
            "observation",
            item["sources"][0]["chemin"],
            None,
            0,
            json.dumps({"registre_original": item, "sources_copies": copies or []}, ensure_ascii=False),
        ),
    )


def _contrat(base, item, *, source=None, fait_id=None, digest=None, receipt=True):
    root = base.prive
    receipt_path = root / "chantier/recu-conversion.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text('{"lot":5,"conversion":"extraction factuelle"}\n')
    source = source or item["sources"][0]
    if digest is None:
        projection = adaptation.lire_connexion(base.connexion)
        item_projete = next(f for f in projection["connaissances"] if f["id"] == item["id"])
        ligne = base.connexion.execute(
            "SELECT id, texte, source, categorie, date_source, donnees_json FROM connaissances WHERE id=?",
            (item["id"],),
        ).fetchone()
        donnees = json.loads(ligne["donnees_json"])
        item_projete.update(
            id_canonique=ligne["id"], texte_canonique=ligne["texte"], source_canonique=ligne["source"],
            categorie_canonique=ligne["categorie"], date_source_canonique=ligne["date_source"],
            preuve_canonique=donnees.get("preuve", item_projete.get("preuve")),
        )
        digest = provenance.empreinte_fait(item_projete)
    entree = {
        "source_originale": dict(source),
        "motif": "Le journal brut a été résumé en faits structurés ; l'original reste distinct.",
        "recu_conversion": {
            "chemin": "chantier/recu-conversion.json",
            "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest() if receipt else "0" * 64,
        },
        "faits": [{"id": fait_id or item["id"], "sha256_representation": digest}],
    }
    payload = {"schema": 1, "type": "source_convertie_en_faits", "conversions": [entree]}
    base.ecrire(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
        (adaptation.CONTRATS_REPRISE, None, None, "Contrat versionné de conversion", "lot5", json.dumps(payload)),
    )
    return payload


def _contrat_index_vide(base):
    chemin = "suivi/journal/2026-10/2026-10-13-decision-organisation.md"
    digest = "0acb252b49fb99eb6503484acf74563d8a4c21313db8b051b6442c6e921295f2"
    recu_path = base.prive / "chantier/recu-ecartement.json"
    recu_path.parent.mkdir(parents=True, exist_ok=True)
    recu_path.write_text('{"motif":"trace d’initialisation sans fait mécanique"}\n')
    index = {
        "type": "index_journaux",
        "journaux": {chemin: {"sha256": digest, "statut": "HISTORIQUE", "connaissances": []}},
    }
    contrat = {
        "schema": 1,
        "type": "source_convertie_en_faits",
        "conversions": [],
        "index_ecartes": [{
            "source_originale": {"chemin": chemin, "sha256": digest},
            "motif": (
                "Trace d’initialisation technique, sans impression ni calibration; "
                "aucune connaissance mécanique à créer."
            ),
            "recu_conversion": {
                "chemin": "chantier/recu-ecartement.json",
                "sha256": hashlib.sha256(recu_path.read_bytes()).hexdigest(),
            },
        }],
    }
    base.ecrire(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
        (adaptation.INDEX, None, None, "Index historique", "memoire", json.dumps(index)),
    )
    base.ecrire(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
        (adaptation.CONTRATS_REPRISE, None, None, "Contrat de disposition", "lot5", json.dumps(contrat)),
    )
    return contrat, chemin


def test_index_vide_ecarte_par_recu_reste_explicitement_non_verifie(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        _contrat, chemin = _contrat_index_vide(base)
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "PASS"
        assert rapport["statut"] == "NON VÉRIFIÉ"
        assert rapport["fidelite_historique"]["index_ecartes"][0]["conservation_originale"] == (
            "ÉCARTÉ PAR DISPOSITION EXPLICITE"
        )
        assert rapport["fidelite_historique"]["index_ecartes"][0]["fidelite_historique"] == "NON VÉRIFIÉ"
        assert any(chemin in note and "écarté" in note for note in rapport["sources"]["non_verifies"])
    finally:
        base.fermer()


@pytest.mark.parametrize("cas", ["id_index", "fait_lie", "chemin", "sha", "motif", "recu"])
def test_disposition_index_vide_refuse_ecarts_et_absences(tmp_path, cas):
    base = Base(tmp_path / "atelier")
    try:
        contrat, chemin = _contrat_index_vide(base)
        if cas == "id_index":
            entree = json.loads(base.connexion.execute(
                "SELECT donnees_json FROM decisions WHERE id=?", (adaptation.INDEX,)
            ).fetchone()[0])
            entree["journaux"][chemin]["connaissances"] = ["fait-1"]
            base.ecrire(
                "UPDATE decisions SET donnees_json=? WHERE id=?", (json.dumps(entree), adaptation.INDEX)
            )
        elif cas == "fait_lie":
            item = _fait(source=chemin, sha_original=contrat["index_ecartes"][0]["source_originale"]["sha256"])
            _inscrire(base, item)
        else:
            disposition = contrat["index_ecartes"][0]
            if cas == "chemin":
                disposition["source_originale"]["chemin"] = "suivi/journal/autre.md"
            elif cas == "sha":
                disposition["source_originale"]["sha256"] = "f" * 64
            elif cas == "motif":
                disposition["motif"] = " "
            elif cas == "recu":
                disposition["recu_conversion"]["sha256"] = "0" * 64
            base.ecrire(
                "UPDATE decisions SET donnees_json=? WHERE id=?",
                (json.dumps(contrat), adaptation.CONTRATS_REPRISE),
            )
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert rapport["statut"] == "FAIL"
    finally:
        base.fermer()


def test_extraits_pass_original_absent_reste_non_verifie_et_index_intact(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        contrat = _contrat(base, item)
        assert adaptation.lire_connexion(base.connexion)["contrat_sources_reprises"] == contrat
        index = {"type": "index_journaux", "journaux": {
            item["sources"][0]["chemin"]: {
                "sha256": item["sources"][0]["sha256"], "statut": "CAPITALISÉ", "connaissances": [item["id"]]
            }
        }}
        base.ecrire(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
            (adaptation.INDEX, None, None, "Index préservé", "memoire", json.dumps(index)),
        )
        avant = tuple(
            base.connexion.execute("SELECT donnees_json FROM decisions WHERE id=?", (adaptation.INDEX,)).fetchone()
        )

        rapport = memoire.controler(base.prive)

        assert rapport["integrite_extraits"]["statut"] == "PASS"
        assert rapport["fidelite_historique"]["statut"] == "NON VÉRIFIÉ"
        assert (
            rapport["fidelite_historique"]["sources"][0]["conservation_originale"]
            == "NON CONSERVÉ PAR CETTE CONVERSION"
        )
        assert rapport["statut"] == "NON VÉRIFIÉ"
        assert not (base.prive / item["sources"][0]["chemin"]).exists()
        apres = tuple(
            base.connexion.execute("SELECT donnees_json FROM decisions WHERE id=?", (adaptation.INDEX,)).fetchone()
        )
        assert apres == avant
    finally:
        base.fermer()


def test_un_fait_peut_etre_rattache_a_deux_sources_converties(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        seconde = {"chemin": "suivi/journal/autre.md", "sha256": "b" * 64}
        item["sources"].append(seconde)
        _inscrire(base, item)
        payload = _contrat(base, item)
        deuxieme_ligne = dict(payload["conversions"][0])
        deuxieme_ligne["source_originale"] = seconde
        payload["conversions"].append(deuxieme_ligne)
        base.ecrire(
            "UPDATE decisions SET donnees_json=? WHERE id=?",
            (json.dumps(payload), adaptation.CONTRATS_REPRISE),
        )

        rapport = memoire.controler(base.prive)

        assert rapport["integrite_extraits"]["statut"] == "PASS"
        assert rapport["fidelite_historique"]["statut"] == "NON VÉRIFIÉ"
        assert rapport["statut"] == "NON VÉRIFIÉ"
    finally:
        base.fermer()


def test_source_absente_sans_contrat_est_fail(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        _inscrire(base, _fait())
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert rapport["sources"]["statut"] == "FAIL"
        assert any("source absente" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()


@pytest.mark.parametrize("champ", ["description", "niveau_preuve", "conditions", "exclusions", "unites"])
def test_modification_du_contenu_canonique_invalide_contrat(tmp_path, champ):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item)
        modifie = dict(item)
        modifie[champ] = {"jeu": "pouce"} if champ == "unites" else "Valeur modifiée après l'extraction."
        base.ecrire(
            "UPDATE connaissances SET donnees_json=? WHERE id=?",
            (json.dumps({"registre_original": modifie, "sources_copies": []}), item["id"]),
        )
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert rapport["statut"] == "FAIL"
    finally:
        base.fermer()


@pytest.mark.parametrize("fait_id", ["absent", "fait-1"])
def test_id_absent_ou_duplique_refuse(tmp_path, fait_id):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item, fait_id=fait_id)
        if fait_id == item["id"]:
            row = base.connexion.execute(
                "SELECT donnees_json FROM decisions WHERE id=?", (adaptation.CONTRATS_REPRISE,)
            ).fetchone()
            payload = json.loads(row[0])
            payload["conversions"][0]["faits"].append(dict(payload["conversions"][0]["faits"][0]))
            base.ecrire(
                "UPDATE decisions SET donnees_json=? WHERE id=?",
                (json.dumps(payload), adaptation.CONTRATS_REPRISE),
            )
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
    finally:
        base.fermer()


def test_sha_original_faux_et_rattachement_autre_source_refuses(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        mauvaise_source = {"chemin": "autre.md", "sha256": "b" * 64}
        _contrat(base, item, source=mauvaise_source)
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
    finally:
        base.fermer()


def test_sha_original_faux_sur_meme_chemin_refuse(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        source_fausse = {**item["sources"][0], "sha256": "b" * 64}
        _contrat(base, item, source=source_fausse)
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
    finally:
        base.fermer()


def test_texte_canonique_sqlite_modifie_invalide_contrat(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item)
        base.ecrire("UPDATE connaissances SET texte=? WHERE id=?", ("Texte SQL altéré", item["id"]))
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
    finally:
        base.fermer()


@pytest.mark.parametrize(("colonne", "valeur"), [("categorie", "physique"), ("date_source", "2026-10-26")])
def test_categorie_ou_date_sqlite_modifiee_invalide_contrat(tmp_path, colonne, valeur):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item)
        base.ecrire(f"UPDATE connaissances SET {colonne}=? WHERE id=?", (valeur, item["id"]))
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert any("empreinte de représentation différente" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()


def test_contrat_incomplet_recu_faux_ou_fait_retire_fail(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        payload = _contrat(base, item, receipt=False)
        payload["conversions"][0]["faits"] = []
        base.ecrire(
            "UPDATE decisions SET donnees_json=? WHERE id=?",
            (json.dumps(payload), adaptation.CONTRATS_REPRISE),
        )
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert rapport["statut"] == "FAIL"
    finally:
        base.fermer()


def test_recu_absent_ou_symbolique_hors_prive_fail(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        payload = _contrat(base, item)
        receipt = base.prive / "chantier/recu-conversion.json"
        receipt.unlink()
        outside = tmp_path / "recu-externe.json"
        outside.write_text("recu externe")
        receipt.symlink_to(outside)
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert any("reçu invalide" in e for e in rapport["erreurs"])
        assert payload["type"] == "source_convertie_en_faits"
    finally:
        base.fermer()


def test_fait_reellement_retire_du_registre_contrat_fail(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item)
        base.ecrire("DELETE FROM connaissances WHERE id=?", (item["id"],))
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert any("fait absent" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def test_source_convertie_dupliquee_dans_contrat_refusee(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        payload = _contrat(base, item)
        payload["conversions"].append(dict(payload["conversions"][0]))
        base.ecrire(
            "UPDATE decisions SET donnees_json=? WHERE id=?",
            (json.dumps(payload), adaptation.CONTRATS_REPRISE),
        )
        rapport = memoire.controler(base.prive)
        assert rapport["integrite_extraits"]["statut"] == "FAIL"
        assert any("source dupliquée" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def test_source_ajoutee_apres_contrat_reste_fail(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        _inscrire(base, item)
        _contrat(base, item)
        item["sources"].append({"chemin": "nouvelle-source.md", "sha256": "c" * 64})
        base.ecrire(
            "UPDATE connaissances SET donnees_json=? WHERE id=?",
            (json.dumps({"registre_original": item, "sources_copies": []}), item["id"]),
        )
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("incomplète" in e or "source absente" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def test_source_declaree_deux_fois_refusee(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait()
        item["sources"].append(dict(item["sources"][0]))
        _inscrire(base, item)
        _contrat(base, item)
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any(
            "source déclarée en double" in erreur or "source dupliquée" in erreur for erreur in rapport["erreurs"]
        )
    finally:
        base.fermer()


def test_copies_meme_chemin_deux_revisions_sont_resolues_exactement(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        dossiers = base.prive / "projets/copies"
        dossiers.mkdir(parents=True)
        items = []
        for index, revision in enumerate(("a" * 40, "b" * 40), start=1):
            octets = f"revision-{index}".encode()
            digest = hashlib.sha256(octets).hexdigest()
            item = _fait(sha_original=digest, revision=revision, identifiant=f"fait-{index}")
            dest = f"projets/copies/rev-{index}.md"
            (base.prive / dest).write_bytes(octets)
            copie = {
                "source_originale": "suivi/journal/ancien.md",
                "revision_originale": revision,
                "sha256_originale": digest,
                "destination": dest,
                "sha256_actuel": digest,
            }
            _inscrire(base, item, [copie])
            items.append((item, dest))
        projection = adaptation.lire_connexion(base.connexion)
        assert {i["sources"][0]["chemin"] for i in projection["connaissances"]} == {d for _, d in items}
        assert memoire.controler(base.prive)["statut"] == "PASS"
    finally:
        base.fermer()


def _inscrire_sans_registre_original(base, copies):
    source = "suivi/journal/2026-10/2026-10-26-impression-d4-retours-decisions.md"
    chemin_copie = "projets/archive/journal-d4.md"
    octets = b"copie privee capturee"
    digest = hashlib.sha256(octets).hexdigest()
    destination = base.prive / chemin_copie
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(octets)
    base.ecrire(
        "INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)",
        (
            "fait-sans-registre", "Retour historique extrait", "observation", source, "2026-10-26", 0,
            json.dumps({
                "domaine": "retour", "niveau_preuve": "OBSERVATION_UTILISATEUR",
                "conditions": "Contexte déclaré.", "exclusions": "Aucune généralisation.",
                "unites": {}, "faits": {"retour": "texte"}, "validite": "historique",
                "sources": [source],
                "sources_copies": [
                    {
                        "source_originale": source,
                        "destination": chemin_copie,
                        "sha256_actuel": digest,
                        "sha256_source_attendu": None,
                        "sha_concorde": None,
                    }
                    for _ in range(copies)
                ],
            }),
        ),
    )
    return source, chemin_copie, digest


def test_fait_sans_registre_original_preserve_source_capturee_et_reste_non_verifie(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        source, copie, digest = _inscrire_sans_registre_original(base, copies=1)
        donnees = adaptation.lire_connexion(base.connexion)
        item = donnees["connaissances"][0]
        declaration = item["sources_originales"][0]
        assert declaration["chemin"] == source
        assert declaration.get("sha256") is None
        assert declaration["origine_sha"] == "copie_source_capturee_import"
        assert declaration["sha256_capturee_import"] == digest
        assert provenance._source(declaration) == (source, None, digest)
        assert item["sources"][0]["chemin"] == copie
        assert item["source_canonique"] == source
        assert item["sources_copies"][0]["sha256_source_attendu"] is None

        rapport = memoire.controler(base.prive)

        assert rapport["sources"]["statut"] == "NON VÉRIFIÉ"
        assert rapport["statut"] == "NON VÉRIFIÉ"
        assert any("capturée à l'import" in note for note in rapport["sources"]["non_verifies"])
    finally:
        base.fermer()


def _fait_avec_copie_archive(*, statut="convertie_en_faits", source_chemin="suivi/journal/ancien.md"):
    item = _fait(source=source_chemin)
    copy = {
        "source_originale": source_chemin,
        "sha256_source_attendu": item["sources"][0]["sha256"],
        "sha256_actuel": "f" * 64,
        "sha_concorde": False,
        "destination": "projets/archive/ancien.md",
        "statut": statut,
    }
    return item, copy


def test_copie_journal_convertie_ne_requiert_plus_les_fichiers_bruts(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item, copie = _fait_avec_copie_archive()
        _inscrire(base, item, [copie])
        _contrat(base, item)

        rapport = memoire.controler(base.prive)

        assert rapport["integrite_extraits"]["statut"] == "PASS"
        assert rapport["sources"]["statut"] == "NON VÉRIFIÉ"
        assert rapport["statut"] == "NON VÉRIFIÉ"
        assert rapport["sources"]["copies_converties"][0]["statut"] == "convertie_en_faits"
        assert any("copie d'archive différente" in note for note in rapport["sources"]["non_verifies"])
        assert not (base.prive / copie["destination"]).exists()
        assert not (base.prive / item["sources"][0]["chemin"]).exists()
    finally:
        base.fermer()


def test_marque_convertie_seule_ou_sur_autre_source_refusee(tmp_path):
    base = Base(tmp_path / "sans-contrat")
    try:
        item, copie = _fait_avec_copie_archive()
        _inscrire(base, item, [copie])
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("sans contrat valide" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()

    base = Base(tmp_path / "sha-change")
    try:
        item, copie = _fait_avec_copie_archive()
        _inscrire(base, item, [copie])
        _contrat(base, item)
        modifie = dict(item)
        modifie["sources"] = [{**item["sources"][0], "sha256": "c" * 64}]
        base.ecrire(
            "UPDATE connaissances SET donnees_json=? WHERE id=?",
            (json.dumps({"registre_original": modifie, "sources_copies": [copie]}), item["id"]),
        )
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert rapport["sources"]["copies_converties"] == []
    finally:
        base.fermer()

    base = Base(tmp_path / "autre-source")
    try:
        item, copie = _fait_avec_copie_archive(source_chemin="suivi/journal/source-originale.md")
        _inscrire(base, item, [copie])
        _contrat(base, item, source={"chemin": "suivi/journal/autre.md", "sha256": "b" * 64})
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("marque convertie_en_faits sans contrat valide" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()


def test_copie_sans_registre_original_mutation_ou_double_version_refusee(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        _source, copie, _digest = _inscrire_sans_registre_original(base, copies=1)
        (base.prive / copie).write_bytes(b"contenu modifie")
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("copie invalide" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()

    base = Base(tmp_path / "atelier-double")
    try:
        _inscrire_sans_registre_original(base, copies=2)
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("plusieurs versions" in erreur or "version unique" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()


def test_copie_identifiee_par_chemin_seul_refusee(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait(sha_original="d" * 64, revision="c" * 40)
        destination = base.prive / "projets/copies/rev.md"
        destination.parent.mkdir(parents=True)
        destination.write_text("copie")
        copie = {
            "source_originale": item["sources"][0]["chemin"],
            "destination": "projets/copies/rev.md",
            "sha256_actuel": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
        _inscrire(base, item, [copie])
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("version unique" in erreur or "source absente" in erreur for erreur in rapport["erreurs"])
    finally:
        base.fermer()


def test_copie_hors_prive_ou_symbolique_refusee(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        item = _fait(sha_original="d" * 64, revision="c" * 40)
        outside = tmp_path / "outside.md"
        outside.write_text("hors privé")
        cible = base.prive / "projets/copied.md"
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.symlink_to(outside)
        copie = {
            "source_originale": item["sources"][0]["chemin"],
            "revision_originale": item["sources"][0]["revision_originale"],
            "sha256_originale": item["sources"][0]["sha256"],
            "destination": "projets/copied.md",
            "sha256_actuel": hashlib.sha256(outside.read_bytes()).hexdigest(),
        }
        _inscrire(base, item, [copie])
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert any("symbolique" in e or "copie invalide" in e for e in rapport["erreurs"])
    finally:
        base.fermer()


def test_verification_ne_migre_pas_registre_lorsque_index_absent(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        registre = base.prive / "donnees/connaissances/registre.json"
        registre.parent.mkdir(parents=True)
        registre.write_text(json.dumps({"connaissances": [_fait()], "journaux": {}}))
        avant = registre.read_bytes()
        rapport = memoire.controler(base.prive)
        assert rapport["statut"] == "FAIL"
        assert "Registre historique présent mais non repris" in " ".join(rapport["erreurs"])
        assert registre.read_bytes() == avant
        assert base.connexion.execute("SELECT count(*) FROM connaissances").fetchone()[0] == 0
        nb_index = base.connexion.execute(
            "SELECT count(*) FROM decisions WHERE id=?", (adaptation.INDEX,)
        ).fetchone()[0]
        assert nb_index == 0
    finally:
        base.fermer()
def test_configuration_archivee_garde_origine_distincte_du_chemin_copie(tmp_path):
    """Une configuration archivée relie origine et copie exactes ; une origine contradictoire donne FAIL."""
    root=tmp_path/'prive'
    base=Base(root)
    try:
        fichier=root/'archive/configuration.json'
        fichier.parent.mkdir()
        fichier.write_text('{"limite":600}')
        copie={'source_originale':'donnees/configuration.json','destination':'archive/configuration.json',
               'sha256_actuel':hashlib.sha256(fichier.read_bytes()).hexdigest()}
        d={'type':'configuration_archivee','source_originale':'donnees/configuration.json',
           'sources_copies':[copie]}
        base.ecrire('INSERT INTO connaissances VALUES(?,?,?,?,?,?,?)',
            ('archive-test','Configuration historique','numerique','archive/configuration.json',None,0,json.dumps(d)))
        assert memoire.controler(root)['erreurs']==[]
        d['source_originale']='donnees/autre-source.json'
        base.ecrire('UPDATE connaissances SET donnees_json=? WHERE id=?',(json.dumps(d),'archive-test'))
        assert any('copie' in e for e in memoire.controler(root)['erreurs'])
    finally:
        base.fermer()


def test_conversion_membre_archive_sans_redistribution(tmp_path):
    """Un membre au SHA déclaré lie les faits sans livrer l'archive; fidélité NON VÉRIFIÉE."""
    base = Base(tmp_path / "prive")
    try:
        item = _fait(source="sources/archive-tierce.tar.gz", sha_original=None)
        item["sources"][0].update(membre="core/contrat.py", sha256_membre="b" * 64)
        _inscrire(base, item)
        _contrat(base, item)
        resultat = memoire.controler(base.prive)
        assert not resultat["erreurs"], resultat["erreurs"]
        assert resultat["statut"] == "NON VÉRIFIÉ"
        assert resultat["integrite_extraits"]["statut"] == "PASS"
        assert not (base.prive / "sources/archive-tierce.tar.gz").exists()
        row = base.connexion.execute("SELECT donnees_json FROM connaissances WHERE id=?", (item["id"],)).fetchone()
        donnees = json.loads(row[0])
        donnees["registre_original"]["sources"][0]["sha256_membre"] = "c" * 64
        base.ecrire("UPDATE connaissances SET donnees_json=? WHERE id=?", (json.dumps(donnees), item["id"]))
        assert memoire.controler(base.prive)["erreurs"]
    finally:
        base.fermer()


@pytest.mark.parametrize("membre", ["../hors.py", "/tmp/hors.py", ""])
def test_identite_membre_refuse_traversee(membre):
    """Refuse un membre ambigu; ne lit jamais l'archive ni le système de fichiers."""
    with pytest.raises(ValueError, match="membre"):
        provenance._source({"chemin": "sources/a.tar.gz", "membre": membre, "sha256_membre": "a" * 64})


def test_empreintes_archive_et_membre_distinctes():
    """Deux empreintes identiques ne confondent pas archive entière et membre."""
    assert provenance._source({"chemin": "a.tar.gz", "sha256": "a" * 64}) != provenance._source(
        {"chemin": "a.tar.gz", "membre": "core.py", "sha256_membre": "a" * 64}
    )
