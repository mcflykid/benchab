"""Les comparaisons de profils restent typées et attribuées aux sources fournies."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from benchab.cli import main as cli_main
from benchab.controles import ecarts_profils_3mf
from benchab.donnees.base import Base


def _archive(chemin, projet=None, metadonnees=(), parties=()):
    """Fabrique un petit 3MF synthétique avec clés projet et objet."""
    from xml.etree import ElementTree as ET

    config = ET.Element("config")
    obj = ET.SubElement(config, "object", id="1")
    ET.SubElement(obj, "metadata", key="name", value="Piece")
    for cle, valeur in metadonnees:
        ET.SubElement(obj, "metadata", key=cle, value=valeur)
    if parties:
        partie = ET.SubElement(obj, "part", id="1")
        for cle, valeur in parties:
            ET.SubElement(partie, "metadata", key=cle, value=valeur)
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(projet or {}))
        archive.writestr("Metadata/model_settings.config", ET.tostring(config))


def _profil(path, categorie, nom, reglages, *, inherits=None):
    """Écrit une fixture locale de preset sans valeur issue d'un profil réel."""
    contenu = {"type": categorie, "name": nom, **reglages}
    if inherits:
        contenu["inherits"] = inherits
    path.write_text(json.dumps(contenu), encoding="utf-8")
    return path


def test_compare_reglages_typages_portees_origines_et_sha(tmp_path):
    """Compare des réglages projet/objet, résout le parent et préserve les deux sources."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"}, [("outer_wall_speed", "120")])
    parent = _profil(
        tmp_path / "Processus-parent.json", "process", "Processus-parent",
        {"top_shell_layers": "4", "outer_wall_speed": "100"},
    )
    preset = _profil(
        tmp_path / "Processus.json", "process", "Processus", {"outer_wall_speed": "120"},
        inherits="Processus-parent",
    )
    sha_modele = hashlib.sha256(modele.read_bytes()).hexdigest()
    sha_profils = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (parent, preset)}

    rapport = ecarts_profils_3mf.comparer(modele, [preset])

    assert rapport["sha256_3mf_avant"] == rapport["sha256_3mf_apres"] == sha_modele
    assert rapport["profils_sha256_avant"] == rapport["profils_sha256_apres"] == sha_profils
    parent_resolu = next(h for p in rapport["profils"] for h in p["heritages"]
                         if h["name"] == "Processus-parent")
    assert parent_resolu["empreinte_type"] == "JSON canonique en mémoire"
    assert parent_resolu["sha256_fichier"] == sha_profils[str(parent.resolve())]
    assert {(d["cle"], d["portee"], d["profil"]["source"]) for d in rapport["differences"]} == {
        ("top_shell_layers", "projet", str(parent)),
    }
    assert rapport["differences"][0]["sensible"] is False
    assert rapport["differences"][0]["modification_manuelle"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "WARN"


def test_familles_sensibles_et_type_inconnu_non_comparables(tmp_path):
    """Les différences sensibles sont signalées ; clé/type inconnus restent NON VÉRIFIÉS."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {
        "outer_wall_speed": "120", "default_acceleration": "1500",
        "bridge_flow": "1.1", "cle_fixture_inconnue": "x",
    })
    profil = _profil(tmp_path / "P.json", "process", "P", {
        "outer_wall_speed": "100", "default_acceleration": "1000", "bridge_flow": "1",
    })
    rapport = ecarts_profils_3mf.comparer(modele, [profil])
    differences = {d["cle"]: d for d in rapport["differences"]}
    diff = differences["outer_wall_speed"]
    inconnu = next(d for d in rapport["non_verifies"] if d["cle"] == "cle_fixture_inconnue")
    assert diff["sensible"] is True
    assert "vitesse" in diff["domaines_sensibles"]
    assert "acceleration" in differences["default_acceleration"]["domaines_sensibles"]
    assert "debit" in differences["bridge_flow"]["domaines_sensibles"]
    assert diff["modification_manuelle"] == "NON VÉRIFIÉ"
    assert inconnu["statut"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_multi_filament_apparie_seulement_par_identite(tmp_path):
    """Les profils filament se relient aux slots via les identités typées, jamais par longueur seule."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {
        "filament_settings_id": ["PLA-A", "PETG-B"],
        "filament_type": ["PLA", "PETG"],
        "nozzle_temperature": ["210", "240"],
    })
    pla = _profil(
        tmp_path / "pla.json", "filament", "PLA-A",
        {"filament_type": ["PLA"], "nozzle_temperature": ["210"]},
    )
    petg = _profil(
        tmp_path / "petg.json", "filament", "PETG-B",
        {"filament_type": ["PETG"], "nozzle_temperature": ["230"]},
    )
    rapport = ecarts_profils_3mf.comparer(modele, [pla, petg])
    differences = {d["cle"]: d for d in rapport["differences"]}
    assert set(differences) == {"nozzle_temperature"}
    assert differences["nozzle_temperature"]["slot_id"] == "PETG-B"
    assert differences["nozzle_temperature"]["sensible"] is True
    identique = next(d for d in rapport["comparaisons"] if d["cle"] == "filament_type")
    assert identique["statut"] == "IDENTIQUE"
    assert "materiel" in identique["domaines_sensibles"]

    mauvais = _profil(
        tmp_path / "mauvais.json", "filament", "Autre",
        {"filament_type": ["PETG"], "nozzle_temperature": ["240"]},
    )
    nv = ecarts_profils_3mf.comparer(modele, [pla, mauvais])
    assert any(d["cle"] == "nozzle_temperature" for d in nv["non_verifies"])


def test_famille_machine_compare_le_vecteur_natif_exact(tmp_path):
    """Compare sans repli les listes machine qualifiées, en unités natives."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"machine_max_speed_x": ["500", "600"]})
    profil = _profil(tmp_path / "M.json", "machine", "M", {"machine_max_speed_x": ["500", "600"]})
    rapport = ecarts_profils_3mf.comparer(modele, [profil])
    assert rapport["statut"] == "PASS"
    assert rapport["comparaisons"][0]["statut"] == "IDENTIQUE"
    assert rapport["comparaisons"][0]["valeur_3mf"] == ["500", "600"]


def test_profils_modifies_pendant_comparaison_refuses_le_rapport(tmp_path, monkeypatch):
    """Une mutation après résolution change le SHA et annule le rapport."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "4"})
    comparer_initial = ecarts_profils_3mf._comparaison

    def modifier(*args, **kwargs):
        ligne = comparer_initial(*args, **kwargs)
        profil.write_text(profil.read_text() + " ", encoding="utf-8")
        return ligne

    monkeypatch.setattr(ecarts_profils_3mf, "_comparaison", modifier)
    with pytest.raises(ValueError, match="profil modifié"):
        ecarts_profils_3mf.comparer(modele, [profil])


def test_nom_heritage_hors_dossier_refuse_avant_lecture(tmp_path, monkeypatch):
    """Un héritage traversant est refusé avant toute tentative de lecture hors racine."""
    repertoire = tmp_path / "profils"
    repertoire.mkdir()
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"})
    profil = _profil(repertoire / "P.json", "process", "P", {"top_shell_layers": "5"}, inherits="../secret")
    (tmp_path / "secret.json").write_text(
        json.dumps({"type": "process", "name": "secret", "top_shell_layers": "5"}), encoding="utf-8"
    )
    lire_initial = ecarts_profils_3mf._lire_profil
    lus = []

    def tracer(chemin):
        lus.append(Path(chemin).resolve())
        return lire_initial(chemin)

    monkeypatch.setattr(ecarts_profils_3mf, "_lire_profil", tracer)
    with pytest.raises(ValueError, match="hors périmètre"):
        ecarts_profils_3mf.comparer(modele, [profil])
    assert lus == [profil.resolve()]


def test_profil_symlink_refuse_avant_resolution(tmp_path):
    """Une cible de profil liée n’est pas légitimée par resolve()."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"})
    cible = _profil(tmp_path / "cible.json", "process", "P", {"top_shell_layers": "5"})
    lien = tmp_path / "P.json"
    lien.symlink_to(cible)
    with pytest.raises(ValueError, match="Lien symbolique"):
        ecarts_profils_3mf.comparer(modele, [lien])


def test_json_profil_borne_avant_decodage(tmp_path):
    """Un profil au-delà de 8 Mio est refusé avant décodage et comparaison."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"})
    profil = tmp_path / "P.json"
    profil.write_text('{"type":"process","name":"P","pad":"' + "x" * (8 * 1024 * 1024) + '"}',
                      encoding="utf-8")
    with pytest.raises(ValueError, match="limite 8 Mio"):
        ecarts_profils_3mf.comparer(modele, [profil])


def test_reglages_de_partie_et_comparaisons_identiques_explicites(tmp_path):
    """Les portées de partie et les égalités gardent leur origine dans le rapport."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {}, parties=[("sparse_infill_density", "20%")])
    profil = _profil(tmp_path / "P.json", "process", "P", {"sparse_infill_density": "20%"})
    rapport = ecarts_profils_3mf.comparer(modele, [profil])
    assert rapport["statut"] == "PASS"
    assert rapport["comparaisons"][0]["statut"] == "IDENTIQUE"
    assert rapport["comparaisons"][0]["portee"] == "partie"
    assert rapport["comparaisons"][0]["modification_manuelle"] == "NON VÉRIFIÉ"


def test_cli_ecrit_uniquement_le_rapport_prive(tmp_path):
    """Le parcours CLI rapporte des profils explicitement sélectionnés sans les modifier."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {"top_shell_layers": "5"})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "5"})
    sha = hashlib.sha256(profil.read_bytes()).hexdigest()
    rapport = tmp_path / "rapport.json"
    atelier = Base(tmp_path)
    atelier.fermer()
    code = cli_main(
        ["--prive", str(tmp_path), "controle", "ecarts-profils-3mf", "--",
         str(modele), "--profil", str(profil), "--rapport", str(rapport)]
    )
    assert code == 0
    assert json.loads(rapport.read_text())["statut"] == "PASS"
    assert hashlib.sha256(profil.read_bytes()).hexdigest() == sha
