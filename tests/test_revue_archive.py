"""Le parcours public revoit une archive figée sans lancer de trancheuse."""

import hashlib
import json
from pathlib import Path

import pytest

from benchab.cli import parser
from benchab.controles import revue_locale
from benchab.fabrication import revue_archive


def _rapport_initial(archive, geometrie_sha=None):
    return {
        "statut": "NON VÉRIFIÉ",
        "code": 2,
        "zones_locales": {
            "tranche_sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            "geometrie_sha256": geometrie_sha,
            "zones": [],
        },
    }


def test_cli_expose_le_parcours_archive_deja_tranche():
    """La commande publique distingue la revue d’archive de la chaîne qui tranche."""
    arguments = parser().parse_args([
        "fabrication", "revue-archive", "tranche.3mf", "--declaration", "DECLARATION.json",
        "--sortie", "prive/rapports/envoi.json",
    ])
    assert arguments.commande == "fabrication"
    assert arguments.action == "revue-archive"
    assert arguments.archive == Path("tranche.3mf")
    assert arguments.declaration == Path("DECLARATION.json")


def test_parcours_revue_archive_reutilise_controles_et_preserve_source(tmp_path, monkeypatch):
    """Le parcours orchestre les contrôles sur les octets figés et ne lance aucun slicer."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive tranchee intacte")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    preuve = tmp_path / "preuve-humaine.json"
    preuve.write_text("source revue", encoding="utf-8")
    revue_source = tmp_path / "REVUE.json"
    revue_source.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "geometrie_sha256": None,
        "auteur": "relectrice",
        "zones": {"zone-1": {
            "decision": "justifie", "justification": "inspection humaine", "derniere_couche_verifiee": True,
            "auteur": "humain", "preuves": [{
                "fichier": preuve.name, "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest(),
            }],
        }},
    }), encoding="utf-8")
    revue_octets = revue_source.read_bytes()
    sortie = tmp_path / "final.json"
    appels = []
    controle_initial = True

    def controler(*args, **kwargs):
        nonlocal controle_initial
        if controle_initial:
            controle_initial = False
            appels.append(("initial", args, kwargs))
            return _rapport_initial(archive)
        appels.append(("final", args, kwargs))
        return {"statut": "NON VÉRIFIÉ", "code": 2, "resultats": []}

    def revoir(tranche, envoi, revue, faits):
        appels.append(("revoir", tranche, envoi, revue, faits))
        decisions_humaines = json.loads(Path(revue).read_text())["zones"]
        return {
            "env": json.loads(Path(envoi).read_text()),
            "faits": {}, "auto": {}, "gardees": decisions_humaines,
            "reste": ["zone-1"], "plaques": {},
        }

    def ecrire(res, revue, faits, **options):
        appels.append(("ecrire", revue, faits))
        Path(faits).write_text("{}", encoding="utf-8")
        Path(revue).write_text(json.dumps({
            "tranche_sha256": res["env"]["zones_locales"]["tranche_sha256"],
            "geometrie_sha256": None,
            "zones": res["gardees"],
        }), encoding="utf-8")

    monkeypatch.setattr(revue_archive.envoi, "controler", controler)
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", revoir)
    monkeypatch.setattr(revue_archive.revue_locale, "ecrire", ecrire)
    monkeypatch.setattr(
        "benchab.processus.executer",
        lambda *a, **k: pytest.fail("le parcours d’archive ne doit pas lancer de trancheuse"),
    )

    resultat = revue_archive.executer(
        archive, declaration, sortie=sortie, revue_source=revue_source, prive=tmp_path
    )

    assert [appel[0] for appel in appels] == ["initial", "revoir", "ecrire", "final"]
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert appels[-1][2]["revue_locale"] == Path(resultat["revue"])
    assert resultat["resume_revue"]["a_revoir"] == 1
    assert Path(resultat["rapport"]).is_file()
    assert Path(resultat["rapport_initial"]).is_file()
    revue_finale = json.loads(Path(resultat["revue"]).read_text())
    assert revue_finale["zones"]["zone-1"]["decision"] == "justifie"
    assert revue_finale["zones"]["zone-1"]["preuves"][0]["sha256"] == hashlib.sha256(preuve.read_bytes()).hexdigest()
    assert resultat["sources_sha256"][str(preuve)] == hashlib.sha256(preuve.read_bytes()).hexdigest()
    assert archive.read_bytes() == b"archive tranchee intacte"
    assert revue_source.read_bytes() == revue_octets


def test_parcours_refuse_revue_humaine_perimee_sans_ecrire(tmp_path, monkeypatch):
    """Une revue liée à d’autres empreintes est refusée sans réécrire la source."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive nouvelle")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    revue = tmp_path / "REVUE.json"
    revue.write_text(json.dumps({
        "tranche_sha256": "0" * 64,
        "geometrie_sha256": None,
        "zones": {"zone-1": {"decision": "à revoir", "auteur": "humain"}},
    }), encoding="utf-8")
    revue_avant = revue.read_bytes()
    ecritures = []
    monkeypatch.setattr(revue_archive.envoi, "controler", lambda *a, **k: _rapport_initial(archive))
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", lambda *a, **k: pytest.fail("revue périmée relue"))
    monkeypatch.setattr(revue_archive.revue_locale, "ecrire", lambda *a, **k: ecritures.append(a))

    with pytest.raises(ValueError, match="empreintes de l’archive ou de la géométrie"):
        revue_archive.executer(
            archive, declaration, sortie=tmp_path / "sortie.json", revue_source=revue, prive=tmp_path
        )

    assert revue.read_bytes() == revue_avant
    assert not ecritures


def test_parcours_refuse_preuve_humaine_perimee(tmp_path, monkeypatch):
    """Une décision dont la preuve a changé est refusée avant toute nouvelle revue."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive stable")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    preuve = tmp_path / "preuve.json"
    preuve.write_text("preuve initiale", encoding="utf-8")
    revue = tmp_path / "REVUE.json"
    revue.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "geometrie_sha256": None,
        "zones": {"zone-1": {
            "decision": "justifie", "justification": "examen conclu", "derniere_couche_verifiee": True,
            "preuves": [{"fichier": "preuve.json", "sha256": hashlib.sha256(b"preuve initiale").hexdigest()}],
        }},
    }), encoding="utf-8")
    preuve.write_text("preuve remplacée", encoding="utf-8")
    revue_avant = revue.read_bytes()
    monkeypatch.setattr(revue_archive.envoi, "controler", lambda *a, **k: _rapport_initial(archive))
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", lambda *a, **k: pytest.fail("preuve périmée relue"))

    with pytest.raises(ValueError, match="Preuve de revue source absente ou périmée"):
        revue_archive.executer(
            archive, declaration, sortie=tmp_path / "sortie.json", revue_source=revue, prive=tmp_path
        )

    assert revue.read_bytes() == revue_avant


def test_collision_apparue_apres_validation_refuse_le_parcours_sans_ecraser(tmp_path, monkeypatch):
    """Une preuve apparue après la garde reste intacte ; la tranche et la revue source aussi."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive temoin stable")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    revue_source = tmp_path / "REVUE-HUMAINE.json"
    revue_octets = json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "geometrie_sha256": None,
        "zones": {},
    }).encode("utf-8")
    revue_source.write_bytes(revue_octets)
    sortie = tmp_path / "final.json"
    collision = tmp_path / "final-faits.json"
    collision_octets = b"preuve concurrente conservee"
    controle_initial = True

    def controler(*args, **kwargs):
        nonlocal controle_initial
        rapport = _rapport_initial(archive)
        if controle_initial:
            controle_initial = False
            collision.write_bytes(collision_octets)
            return rapport
        return {"statut": "NON VÉRIFIÉ", "code": 2, "resultats": []}

    def revoir(tranche, envoi, revue, faits):
        assert Path(revue) == revue_source
        return {
            "env": json.loads(Path(envoi).read_text())["zones_locales"],
            "faits": {}, "auto": {}, "gardees": {}, "reste": [], "plaques": {},
        }

    monkeypatch.setattr(revue_archive.envoi, "controler", controler)
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", revoir)

    with pytest.raises(ValueError, match="Destination existante"):
        revue_archive.executer(
            archive, declaration, sortie=sortie, revue_source=revue_source, prive=tmp_path
        )

    assert collision.read_bytes() == collision_octets
    assert archive.read_bytes() == b"archive temoin stable"
    assert revue_source.read_bytes() == revue_octets
    assert not sortie.exists()


def test_ecriture_revue_publie_sha_relatif_et_decision_humaine(tmp_path):
    """L’écriture neuve conserve le lien relatif final et la décision humaine telle quelle."""
    faits = tmp_path / "preuves/faits.json"
    revue = tmp_path / "REVUE.json"
    decision = {
        "decision": "justifie", "justification": "examen humain conservé", "auteur": "atelier",
        "derniere_couche_verifiee": True, "preuves": [{"fichier": "photo.png", "sha256": "a" * 64}],
    }
    revue_locale.ecrire(
        {
            "env": {"tranche_sha256": "b" * 64, "geometrie_sha256": None},
            "faits": {}, "plaques": {}, "gardees": {"zone-1": decision}, "auto": {"zone-2": "critère déclaré"},
        },
        revue,
        faits,
        sorties_neuves=True,
    )

    contenu = json.loads(revue.read_text(encoding="utf-8"))
    assert contenu["zones"]["zone-1"] == decision
    preuve_auto = contenu["zones"]["zone-2"]["preuves"][0]
    assert preuve_auto["fichier"] == "preuves/faits.json"
    assert hashlib.sha256((tmp_path / preuve_auto["fichier"]).read_bytes()).hexdigest() == preuve_auto["sha256"]


def test_sans_revue_source_le_parcours_donne_un_etat_neutre_explicite(tmp_path, monkeypatch):
    """Une revue voisine apparue après validation ne devient pas une source de décisions."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive temoin")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    revue_par_defaut = tmp_path / "REVUE-ZONES-LOCALES.json"
    revue_par_defaut.write_text('{"zones":{"decision-voisine":{"decision":"justifie"}}}', encoding="utf-8")
    sortie = tmp_path / "final.json"
    voisine = tmp_path / "final-revue.json"
    octets_voisins = b'{"zones":{"zone-voisine":{"decision":"justifie"}}}'
    controle_initial = True

    def controler(*args, **kwargs):
        nonlocal controle_initial
        if controle_initial:
            controle_initial = False
            neutre = kwargs.get("revue_locale")
            assert neutre is not None
            assert Path(neutre) != revue_par_defaut
            assert not Path(neutre).exists()
            voisine.write_bytes(octets_voisins)
            return _rapport_initial(archive)
        return {"statut": "NON VÉRIFIÉ", "code": 2, "resultats": []}

    def revoir(tranche, envoi, revue, faits):
        assert Path(revue) == Path(envoi)
        assert "zones" not in json.loads(Path(revue).read_text())
        return {
            "env": json.loads(Path(envoi).read_text())["zones_locales"],
            "faits": {}, "auto": {}, "gardees": {}, "reste": [], "plaques": {},
        }

    monkeypatch.setattr(revue_archive.envoi, "controler", controler)
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", revoir)

    with pytest.raises(ValueError, match="Destination existante"):
        revue_archive.executer(archive, declaration, sortie=sortie, prive=tmp_path)

    assert voisine.read_bytes() == octets_voisins
    assert archive.read_bytes() == b"archive temoin"


def test_archive_recalcule_une_mesure_humaine_contradictoire(tmp_path):
    """La revue neuve garde la mesure d'origine et son FAIL numérique malgré la décision humaine."""
    from zipfile import ZipFile

    from benchab.controles.zones_locales import analyser_gcode

    gcode = (
        "G90\nM83\n;LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Bridge\n"
        "G1 X0 Y0 F1200\nG1 X10 Y0 E1\n;LAYER_CHANGE\n;WIDTH:0.4\n"
        ";TYPE:Overhang wall\nG1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\n"
    ).encode()
    archive = tmp_path / "tranche.3mf"
    with ZipFile(archive, "w") as z:
        z.writestr("Metadata/project_settings.config", '{"filament_settings_id":["PLA"]}')
        z.writestr("Metadata/model_settings.config", "<config/>")
        z.writestr("Metadata/plate_1.gcode", gcode)
    zone = analyser_gcode(gcode.decode())["zones"][0]["id"]
    preuve = tmp_path / "preuve.txt"
    preuve.write_text("preuve humaine", encoding="utf-8")
    mesure = {
        "zone_id": zone, "plaque": 1, "gcode_sha256": hashlib.sha256(gcode).hexdigest(),
        "ligne_source": 7, "couche_source": 0, "ligne_cible": 12, "couche_cible": 1,
        "axe_local": [0, 1], "valeur_declaree_mm": 0.1, "tolerance_mm": 0.001,
    }
    revue = tmp_path / "humaine.json"
    revue.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "geometrie_sha256": None,
        "zones": {zone: {
            "decision": "justifie", "justification": "historique", "derniere_couche_verifiee": True,
            "preuves": [{"fichier": preuve.name, "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest()}],
        }},
        "mesures_recul_recouvrement": [mesure],
    }), encoding="utf-8")
    original = revue.read_bytes()
    declaration = tmp_path / "envoi.json"
    declaration.write_text('{"filaments": {}}', encoding="utf-8")
    rapport = revue_archive.executer(
        archive, declaration, sortie=tmp_path / "final.json", prive=tmp_path, revue_source=revue,
    )
    derivee = json.loads(Path(rapport["revue"]).read_text(encoding="utf-8"))
    assert derivee["mesures_recul_recouvrement"] == [mesure]
    resultats = rapport["rapport_final"]["zones_locales"]["mesures_recul_recouvrement"]
    assert resultats[0]["statut"] == "FAIL"
    assert revue.read_bytes() == original
