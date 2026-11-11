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
    assert rapport["interventions_manuelles"] == []


def test_intervention_declaree_requiert_delta_scalaire_exact_et_sources_sha(tmp_path):
    """Ne déclare un écart manuel que pour un champ explicitement nommé et changé entre deux instantanés."""
    avant = tmp_path / "avant.3mf"
    apres = tmp_path / "apres.3mf"
    _archive(avant, {"top_shell_layers": "4"})
    _archive(apres, {"top_shell_layers": "5"})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "5"})
    avant_octets, apres_octets = avant.read_bytes(), apres.read_bytes()
    sha_avant = hashlib.sha256(avant_octets).hexdigest()
    sha_apres = hashlib.sha256(apres_octets).hexdigest()

    rapport = ecarts_profils_3mf.comparer(
        apres, [profil], avant_intervention=avant, champs_manuels=["projet/top_shell_layers"]
    )

    declaration = rapport["interventions_manuelles"][0]
    assert declaration["statut"] == "DÉCLARÉE — ÉCART VÉRIFIÉ"
    assert declaration["valeur_avant"] == "4" and declaration["valeur_apres"] == "5"
    assert declaration["provenance_avant"] == {
        "source": str(avant), "sha256": sha_avant, "chemin": "projet/top_shell_layers"
    }
    assert declaration["provenance_apres"] == {
        "source": str(apres), "sha256": sha_apres, "chemin": "projet/top_shell_layers"
    }
    assert rapport["comparaisons"][0]["modification_manuelle"] == "DÉCLARÉE — ÉCART VÉRIFIÉ"
    assert avant.read_bytes() == avant_octets and apres.read_bytes() == apres_octets


@pytest.mark.parametrize(
    "avant_valeur,apres_valeur,attendu",
    [("5", "5", "Aucun changement typé"), (None, "5", "absent de l’instantané avant")],
)
def test_intervention_identique_ou_absente_reste_non_verifiee(tmp_path, avant_valeur, apres_valeur, attendu):
    """Un champ identique ou absent ne prouve aucune intervention."""
    avant, apres = tmp_path / "avant.3mf", tmp_path / "apres.3mf"
    _archive(avant, {} if avant_valeur is None else {"top_shell_layers": avant_valeur})
    _archive(apres, {} if apres_valeur is None else {"top_shell_layers": apres_valeur})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "5"})
    rapport = ecarts_profils_3mf.comparer(
        apres, [profil], avant_intervention=avant, champs_manuels=["projet/top_shell_layers"]
    )
    declaration = rapport["interventions_manuelles"][0]
    assert declaration["statut"] == "NON VÉRIFIÉ"
    assert attendu in declaration["raison"]


def test_champ_manuel_sans_archive_avant_reste_non_verifie(tmp_path):
    """Un nom de champ déclaré sans archive de référence ne suffit pas."""
    apres = tmp_path / "apres.3mf"
    _archive(apres, {"top_shell_layers": "5"})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "5"})
    rapport = ecarts_profils_3mf.comparer(
        apres, [profil], champs_manuels=["projet/top_shell_layers"]
    )
    declaration = rapport["interventions_manuelles"][0]
    assert declaration["statut"] == "NON VÉRIFIÉ"
    assert declaration["provenance_avant"] is None


def test_champ_manuel_liste_ou_slot_ambigu_reste_non_verifie(tmp_path):
    """Les listes de profils multi-axes ne deviennent pas des interventions scalaires."""
    avant, apres = tmp_path / "avant.3mf", tmp_path / "apres.3mf"
    _archive(avant, {"machine_max_speed_x": ["500", "600"]})
    _archive(apres, {"machine_max_speed_x": ["500", "700"]})
    profil = _profil(tmp_path / "M.json", "machine", "M", {"machine_max_speed_x": ["500", "700"]})
    rapport = ecarts_profils_3mf.comparer(
        apres, [profil], avant_intervention=avant, champs_manuels=["projet/machine_max_speed_x"]
    )
    assert rapport["interventions_manuelles"][0]["statut"] == "NON VÉRIFIÉ"
    assert "scalaire" in rapport["interventions_manuelles"][0]["raison"]


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


def test_coherence_distingue_ecart_prouve_et_sources_incompletes(tmp_path):
    """Le même profil peut servir à deux slots ; la matière contradictoire reste visible malgré les inconnus."""
    modele = tmp_path / "scene.3mf"
    _archive(modele, {
        "printer_model": "Machine de test",
        "filament_settings_id": ["PLA-A", "PLA-A"],
        "filament_type": ["PLA", "PETG"],
    })
    machine = _profil(tmp_path / "machine.json", "machine", "Machine", {"printer_model": "Machine de test"})
    pla = _profil(tmp_path / "pla.json", "filament", "PLA-A", {"filament_type": ["PLA"]})
    rapport = ecarts_profils_3mf.comparer(modele, [machine, pla])
    coherence = rapport["coherence"]
    assert coherence["statut"] == "WARN"
    controles = {(r["cle"], r["slot"]): r for r in coherence["resultats"]}
    assert controles["printer_model", None]["statut"] == "PASS"
    assert controles["wall_sequence", None]["statut"] == "NON VÉRIFIÉ"
    assert controles["filament_type", 1]["statut"] == "PASS"
    ecart = controles["filament_type", 2]
    assert ecart["statut"] == "WARN"
    assert (ecart["valeur_3mf"], ecart["valeur_profil"]) == ("PETG", "PLA")
    assert ecart["source_3mf"]["sha256"] == hashlib.sha256(modele.read_bytes()).hexdigest()
    assert ecart["source_profil"]["sha256"] == hashlib.sha256(pla.read_bytes()).hexdigest()


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


def test_cli_declare_intervention_et_refuse_collision_avec_archive_source(tmp_path):
    """Le CLI propage les champs déclarés et protège l’archive antérieure du rapport."""
    avant, apres = tmp_path / "avant.3mf", tmp_path / "apres.3mf"
    _archive(avant, {"top_shell_layers": "4"})
    _archive(apres, {"top_shell_layers": "5"})
    profil = _profil(tmp_path / "P.json", "process", "P", {"top_shell_layers": "5"})
    attendu = avant.read_bytes()
    atelier = Base(tmp_path)
    atelier.fermer()
    code = cli_main(
        ["--prive", str(tmp_path), "controle", "ecarts-profils-3mf", "--",
         str(apres), "--profil", str(profil), "--avant-intervention", str(avant),
         "--champ-manuel", "projet/top_shell_layers", "--rapport", str(tmp_path / "rapport.json")]
    )
    assert code == 0
    rapport = json.loads((tmp_path / "rapport.json").read_text())
    assert rapport["interventions_manuelles"][0]["statut"] == "DÉCLARÉE — ÉCART VÉRIFIÉ"
    code_collision = cli_main(
        ["--prive", str(tmp_path), "controle", "ecarts-profils-3mf", "--",
         str(apres), "--profil", str(profil), "--avant-intervention", str(avant),
         "--champ-manuel", "projet/top_shell_layers", "--rapport", str(avant)]
    )
    assert code_collision == 2
    assert avant.read_bytes() == attendu


def test_archive_liee_refusee_avant_comparaison(tmp_path):
    """Un lien d’archive ne contourne pas les gardes du lecteur commun."""
    archive = tmp_path / "source.3mf"
    _archive(archive, {"top_shell_layers": "5"})
    lien = tmp_path / "lien.3mf"
    lien.symlink_to(archive)
    preset = _profil(tmp_path / "Processus.json", "process", "Processus", {"top_shell_layers": "5"})
    with pytest.raises(ValueError, match="Lien symbolique refusé"):
        ecarts_profils_3mf.comparer(lien, [preset])


def test_meme_racine_presets_ne_cree_pas_de_parent_ambigu(tmp_path):
    """Une racine répétée ne compte pas comme deux sources concurrentes."""
    archive = tmp_path / "source.3mf"
    _archive(archive, {"top_shell_layers": "5"})
    _profil(tmp_path / "Parent.json", "process", "Parent", {"top_shell_layers": "5"})
    preset = _profil(tmp_path / "Processus.json", "process", "Processus", {}, inherits="Parent")
    r = ecarts_profils_3mf.comparer(archive, [preset], dossiers_presets=(tmp_path, tmp_path))
    assert len(r["comparaisons"]) == 1
    assert len(r["profils_sha256_avant"]) == 2


def test_deux_formes_non_qualifiees_ne_donnent_pas_egalite(tmp_path):
    """Deux valeurs brutes identiques d’un type non admis restent inconnues."""
    archive = tmp_path / "source.3mf"
    _archive(archive, {"top_shell_layers": 5})
    preset = _profil(tmp_path / "Processus.json", "process", "Processus", {"top_shell_layers": 5})
    r = ecarts_profils_3mf.comparer(archive, [preset])
    assert not r["comparaisons"]
    assert r["statut"] == "NON VÉRIFIÉ"
