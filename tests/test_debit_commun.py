"""Contrat U25 strict sur déclarations explicites ; fixtures synthétiques, aucune machine."""

import copy
import json
from zipfile import ZipFile

import pytest
from fixtures_communes import DECL, creer_3mf_test

from benchab.controles import capacites_machine, controle_envoi, debit_gcode


def _archive(tmp_path, mouvements, *, diametre=1.75, plafond=12):
    source = creer_3mf_test(tmp_path / "source.3mf")
    with ZipFile(source) as z:
        membres = {n: z.read(n) for n in z.namelist()}
    st = json.loads(membres["Metadata/project_settings.config"])
    st.update(filament_type=["PLA"], filament_settings_id=["Fixture"], filament_max_volumetric_speed=[str(plafond)],
              nozzle_diameter=["0.4"], nozzle_type="brass")
    if diametre is not None:
        st["filament_diameter"] = [str(diametre)]
    else:
        st.pop("filament_diameter", None)
    membres["Metadata/project_settings.config"] = json.dumps(st).encode()
    membres["Metadata/slice_info.config"] = (
        b'<config><plate><metadata key="index" value="1"/><filament id="1"/>'
        b'<object name="Piece" skipped="false"/></plate></config>'
    )
    membres["Metadata/plate_1.gcode"] = (
        "G90\nM83\nT0\nG9111 extruderTemp=220 bedTemp=55\n"
        "EXCLUDE_OBJECT_DEFINE NAME=Piece\nEXCLUDE_OBJECT_START NAME=Piece\n"
        ";LAYER_CHANGE\n;TYPE:Outer wall\nG0 X0 Y0 Z0.2 F600\n"
        + mouvements + "\nEXCLUDE_OBJECT_END NAME=Piece\n"
    ).encode()
    with ZipFile(source, "w") as z:
        for n, contenu in membres.items():
            z.writestr(n, contenu)
    declaration = copy.deepcopy(DECL)
    declaration["filaments"]["1"]["debit_max"] = plafond
    chemin = tmp_path / "declaration.json"
    chemin.write_text(json.dumps(declaration))
    return source, chemin


def _verdicts(source, declaration):
    avant = source.read_bytes()
    debit = debit_gcode.analyser(source)["statut"]
    envoi = controle_envoi.controler(source, declaration)
    note = next(n for n in envoi["resultats"] if n["controle"] == "débit commandé (filament 1)")
    capacites = capacites_machine.analyser(source, maitre={"vitesse_xy_mm_s": 600, "buse_mm": 0.4})
    notes = [n["statut"] for n in capacites["contraintes"] if n["controle"] == "débit strict"]
    strict = "FAIL" if "FAIL" in notes else "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in notes or not notes else "PASS"
    assert source.read_bytes() == avant
    return debit, note["statut"], strict


def test_pic_bref_est_refuse_meme_sous_percentile_99(tmp_path):
    """Un pic minoritaire au-dessus du plafond donne FAIL dans les trois parcours U25."""
    source, declaration = _archive(tmp_path, "G1 X100 E1\nG1 X100.01 E0.1")
    assert _verdicts(source, declaration) == ("FAIL", "FAIL", "FAIL")


def test_diametre_declare_est_utilise_par_les_trois_parcours(tmp_path):
    """Le diamètre de 3 mm change le volume calculé ; aucune constante implicite à 1,75 mm."""
    source, declaration = _archive(tmp_path, "G1 X1 E1", diametre=3.0, plafond=30)
    assert _verdicts(source, declaration) == ("FAIL", "FAIL", "FAIL")


def test_diametre_absent_ne_devient_pas_une_mesure_complete(tmp_path):
    """Sans diamètre, aucune valeur implicite ne transforme U25 en PASS."""
    source, declaration = _archive(tmp_path, "G1 X1 E0.01", diametre=None)
    assert _verdicts(source, declaration) == ("NON VÉRIFIÉ",) * 3


def test_plafond_superieur_a_12_reste_une_declaration_explicite(tmp_path):
    """Un plafond explicite de 25 mm³/s accepte ce trajet à environ 24 mm³/s, sans plafond universel."""
    source, declaration = _archive(tmp_path, "G1 X1 E1", plafond=25)
    assert _verdicts(source, declaration) == ("PASS", "PASS", "PASS")


def test_mesure_commune_et_verdict_strict_sans_marge_physique():
    """La mesure vaut π mm³/s ; seule une tolérance numérique est acceptée."""
    from benchab.gcode import debit

    mesure = debit.mesurer({"deposited": 1.0, "deposited_length": 10.0, "feed": 600.0}, 2.0)
    assert mesure["debit_mm3_s"] == pytest.approx(3.141592653589793)
    assert mesure["volume_mm3"] == pytest.approx(3.141592653589793)
    assert mesure["duree_depot_s"] == 1.0
    assert debit.verdict(10.0 + 5e-10, 10.0) == "PASS"
    assert debit.verdict(10.01, 10.0) == "FAIL"
    assert debit.verdict(None, 10.0) == "NON VÉRIFIÉ"
    assert debit.verdict(1.0, None) == "NON VÉRIFIÉ"


def test_bilan_conserve_le_percentile_sans_masquer_le_maximum():
    """Le p99 vaut 1 mm³/s mais le maximum à 40 impose FAIL sous un plafond de 12."""
    from benchab.gcode import debit

    resultat = debit.bilan([(1.0, 1000.0), (40.0, 0.1)], 12.0)
    assert resultat["p99_mm3_s"] == 1.0
    assert resultat["maximum_mm3_s"] == 40.0
    assert resultat["statut"] == "FAIL"
    assert resultat["depassements"] == 1


@pytest.mark.parametrize("plafond", [None, 0, -1, float("nan"), float("inf"), True])
def test_plafond_invalide_ne_donne_jamais_pass(plafond):
    """Un plafond non positif ou non fini ne définit aucun débit autorisé en mm³/s."""
    from benchab.gcode import debit

    assert debit.bilan([(1.0, 10.0)], plafond)["statut"] == "NON VÉRIFIÉ"


def test_depassement_connu_prime_sur_mesure_incomplete():
    """Un dépôt mesuré au-dessus du plafond reste FAIL si une autre mesure manque."""
    from benchab.gcode import debit

    assert debit.bilan([(None, 1.0), (13.0, 1.0)], 12.0)["statut"] == "FAIL"


def test_aucun_depot_mesurable_reste_non_verifie(tmp_path):
    """L'absence de dépôt ne constitue ni un débit nul mesuré ni un PASS."""
    source, declaration = _archive(tmp_path, "G1 X1 E0")
    assert _verdicts(source, declaration) == ("NON VÉRIFIÉ",) * 3


@pytest.mark.parametrize("alias_unique", [True, False])
def test_u25_lie_le_membre_exact_et_refuse_les_alias(tmp_path, alias_unique):
    """Un zéro initial ne masque ni dépassement, ni identité de plaque ambiguë."""
    source, _ = _archive(tmp_path, "G1 X1 E1")
    attendu = debit_gcode.analyser(source)
    assert attendu["statut"] == "FAIL"
    with ZipFile(source) as archive:
        membres = {nom: archive.read(nom) for nom in archive.namelist()}
    canonique = "Metadata/plate_1.gcode"
    modifie = tmp_path / "alias.3mf"
    with ZipFile(modifie, "w") as archive:
        for nom, donnees in membres.items():
            if nom != canonique or not alias_unique:
                archive.writestr(nom, donnees)
        archive.writestr("Metadata/plate_01.gcode", membres[canonique])
    avant = modifie.read_bytes()
    if alias_unique:
        assert debit_gcode.analyser(modifie) == {**attendu, "fichier": modifie.name}
    else:
        with pytest.raises(ValueError, match="[Aa]mbigu|[Dd]upliqu"):
            debit_gcode.analyser(modifie)
    assert modifie.read_bytes() == avant
