"""Validation stricte des plaques d’un résultat 3MF de trancheur."""

import struct
import zipfile

import pytest

from benchab.fabrication.anycubic import _valider_tranchage


def _xml_resultats(index=1):
    """Construit un résultat XML minimal ; les durées sont en s et les masses en g."""
    return (
        '<config><plate><metadata key="index" value="' + str(index) + '"/>'
        '<metadata key="prediction" value="60"/><metadata key="weight" value="1"/>'
        '</plate></config>'
    )


def _tranchee(path, plaques, *, index=1, supplement=None):
    """Écrit un ZIP synthétique avec résultat de plaque et G-code nommé explicitement."""
    with zipfile.ZipFile(path, "w") as archive:
        if supplement is not None:
            archive.writestr("Metadata/ignore.bin", supplement)
        archive.writestr("Metadata/slice_info.config", _xml_resultats(index))
        for nom, contenu in plaques.items():
            archive.writestr(nom, contenu)


def _corrompre_crc_membre(path, cible):
    """Altère le CRC central du membre nommé sans toucher aux autres membres ZIP."""
    donnees = bytearray(path.read_bytes())
    position = 0
    while (position := donnees.find(b"PK\x01\x02", position)) >= 0:
        longueur_nom, longueur_extra, longueur_commentaire = struct.unpack_from("<HHH", donnees, position + 28)
        debut_nom = position + 46
        nom = bytes(donnees[debut_nom:debut_nom + longueur_nom]).decode("ascii")
        if nom == cible:
            crc = struct.unpack_from("<I", donnees, position + 16)[0]
            struct.pack_into("<I", donnees, position + 16, crc ^ 1)
            path.write_bytes(donnees)
            return
        position = debut_nom + longueur_nom + longueur_extra + longueur_commentaire
    raise AssertionError(f"membre ZIP absent : {cible}")


def test_ambiguous_numeric_gcode_alias_is_rejected(tmp_path):
    """Deux noms différents pour la même plaque ne doivent pas choisir un G-code arbitraire."""
    tranche = tmp_path / "ambigue.3mf"
    _tranchee(
        tranche,
        {"Metadata/plate_01.gcode": "G90\n", "Metadata/plate_1.gcode": "G91\n"},
    )

    with pytest.raises(ValueError, match="numéro de plaque dupliqué"):
        _valider_tranchage(tranche, ["Plaque 1"])


def test_unique_zero_padded_gcode_keeps_numeric_plate_identity(tmp_path):
    """Un nom unique rembourré reste relié à l’index numérique annoncé."""
    tranche = tmp_path / "unique-rembourree.3mf"
    _tranchee(tranche, {"Metadata/plate_01.gcode": "G90\nG1 X1\n"})

    mesures = _valider_tranchage(tranche, ["Plaque 1"])

    assert mesures[0]["index"] == 1


@pytest.mark.parametrize(
    ("commentaire", "attendu", "valide"),
    [("; top_shell_layers = 5\n", {"top_shell_layers": "5"}, True),
     ("; top_shell_layers = 4\n", {"top_shell_layers": "5"}, False)],
)
def test_expected_settings_keep_existing_check(tmp_path, commentaire, attendu, valide):
    """La migration garde la comparaison des réglages embarqués, y compris son refus."""
    tranche = tmp_path / "reglages.3mf"
    _tranchee(tranche, {"Metadata/plate_1.gcode": commentaire + "G90\nG1 X1\n"})

    if valide:
        assert _valider_tranchage(tranche, ["Plaque 1"], attendu)[0]["index"] == 1
    else:
        with pytest.raises(ValueError, match="réglages embarqués absents ou différents"):
            _valider_tranchage(tranche, ["Plaque 1"], attendu)


def test_ignored_member_crc_is_checked(tmp_path):
    """Un membre non consommé par le diagnostic reste couvert par le contrôle d’intégrité."""
    tranche = tmp_path / "crc-ignore.3mf"
    _tranchee(tranche, {"Metadata/plate_1.gcode": "G90\nG1 X1\n"}, supplement=b"opaque")
    _corrompre_crc_membre(tranche, "Metadata/ignore.bin")

    with pytest.raises((ValueError, zipfile.BadZipFile)):
        _valider_tranchage(tranche, ["Plaque 1"])


def test_gcode_crc_is_checked_after_full_read(tmp_path):
    """Le contrôle de commandes atteint EOF et valide aussi le CRC de chaque plaque."""
    tranche = tmp_path / "crc-gcode.3mf"
    _tranchee(tranche, {"Metadata/plate_1.gcode": "G90\nG1 X1\n"})
    _corrompre_crc_membre(tranche, "Metadata/plate_1.gcode")

    with pytest.raises((ValueError, zipfile.BadZipFile)):
        _valider_tranchage(tranche, ["Plaque 1"])


def test_gcode_utf8_invalide_est_refuse(tmp_path):
    """Le G-code de plaque est décodé en UTF-8 strict avant le contrôle de commandes."""
    tranche = tmp_path / "utf8-invalide.3mf"
    _tranchee(tranche, {"Metadata/plate_1.gcode": b"G90\n\xff"})

    with pytest.raises(UnicodeDecodeError):
        _valider_tranchage(tranche, ["Plaque 1"])
