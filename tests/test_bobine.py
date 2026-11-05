"""Import uniforme sourcé, possession explicite et conservation des fiches existantes."""

import copy
import json

import pytest

from benchab.connaissances.bobine import importer


@pytest.fixture
def atelier(tmp_path):
    registre = tmp_path / "donnees/filaments/references.json"
    registre.parent.mkdir(parents=True)
    registre.write_text('{"filaments": []}')
    donnees = {
        "id": "nouveau",
        "marque": "Test",
        "nom": "Filament synthétique",
        "possession": "confirmé par utilisateur",
        "sources": {cle: {"statut": "NON VÉRIFIÉ"} for cle in ("fabricant", "orca", "3dfilamentprofiles")},
    }
    return tmp_path, registre, donnees


def test_import_sans_profil_ni_resultat_physique_invente(atelier):
    racine, registre, donnees = atelier
    relatif = importer(racine, donnees)
    fiche = racine / relatif
    assert fiche.is_file()
    entree = json.loads(registre.read_text())["filaments"][0]
    assert entree["profil"] == {}
    assert entree["observation"] == "Aucun retour physique documenté."
    assert entree["sources_recherche"] == donnees["sources"]
    assert "pas une calibration" in fiche.read_text()
    avant = fiche.read_bytes(), registre.read_bytes()
    with pytest.raises(ValueError, match="déjà présente"):
        importer(racine, donnees)
    assert (fiche.read_bytes(), registre.read_bytes()) == avant


@pytest.mark.parametrize(
    "modification",
    [
        {"id": "../hors"},
        {"marque": "../hors"},
        {"possession": ""},
        {"nom": ""},
        {"sources": {}},
        {"sources": {cle: {"statut": "CONSULTÉ"} for cle in ("fabricant", "orca", "3dfilamentprofiles")}},
    ],
)
def test_identite_possession_et_sources_invalides_preservent_registre(atelier, modification):
    racine, registre, donnees = atelier
    avant = registre.read_bytes()
    donnees = copy.deepcopy(donnees)
    donnees.update(modification)
    with pytest.raises(ValueError):
        importer(racine, donnees)
    assert registre.read_bytes() == avant
    assert not (racine / "docs").exists()


def test_fiche_via_parent_symbolique_refusee(atelier):
    racine, registre, donnees = atelier
    (racine / "cible").mkdir()
    (racine / "docs").symlink_to(racine / "cible", target_is_directory=True)
    avant = registre.read_bytes()
    with pytest.raises(ValueError, match="symbolique"):
        importer(racine, donnees)
    assert registre.read_bytes() == avant
    assert list((racine / "cible").iterdir()) == []


def test_fiche_native_disparue_ou_perimee_bloque_coherence(tmp_path):
    """Fiche SQLite absente, éditée ou donnée changée : FAIL ; aucune archive ni réglage adopté."""
    from benchab.donnees.base import Base
    from benchab.qualite.coherence import verifier

    base = Base(tmp_path / "prive")
    try:
        donnees = {
            "id": "fixture-pla", "marque": "Fixture", "nom": "Filament fixture", "matiere": "PLA",
            "possession": "Déclaration synthétique, aucune bobine réelle",
            "sources": {cle: {"statut": "NON VÉRIFIÉ"} for cle in ("fabricant", "orca", "3dfilamentprofiles")},
        }
        relatif = importer(base.prive, donnees)
        fiche = base.prive / relatif
        contenu = fiche.read_bytes()
        assert verifier(base.prive) == []
        fiche.write_bytes(contenu + b"edition non rapprochee\n")
        assert any("fiche filament non conforme" in e for e in verifier(base.prive))
        fiche.write_bytes(contenu)
        entree = json.loads(base.connexion.execute("SELECT donnees_json FROM filaments").fetchone()[0])
        entree["profil"] = {"nozzle_temperature": ["210"]}
        base.ecrire("UPDATE filaments SET donnees_json=? WHERE id=?", (json.dumps(entree), "fixture-pla"))
        assert any("contenu périmé" in e for e in verifier(base.prive))
        fiche.unlink()
        assert any("fiche absente" in e for e in verifier(base.prive))
        assert not fiche.exists()
    finally:
        base.fermer()
