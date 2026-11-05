"""Conteneur protocolaire synthétique, sans dépendance à un projet personnel."""

import json
import zipfile


def creer_3mf_test(p):
    """Petit conteneur de test du protocole ; pas une sortie de slicer validée."""
    ps = {
        "curr_bed_type": "Textured PEI Plate",
        "nozzle_temperature_initial_layer": ["220"],
        "nozzle_temperature": ["240"],
        "filament_max_volumetric_speed": ["32"],
    }
    xml = (
        '<config><object id="1"><metadata key="name" value="Piece"/>'
        '<metadata key="extruder" value="1"/></object><plate><metadata key="plater_id" value="1"/>'
        '<model_instance><metadata key="object_id" value="1"/></model_instance></plate></config>'
    )
    g = (
        "G9111 extruderTemp=220 bedTemp=55\nT0\nM83\n;LAYER_CHANGE\n;TYPE:Outer wall\n"
        "G1 X0 Y0 F1200\nG1 X10 Y0 E0.2\n;LAYER_CHANGE\nM104 S240\nG1 X10 Y10 E0.2\n"
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Metadata/project_settings.config", json.dumps(ps))
        z.writestr("Metadata/model_settings.config", xml)
        z.writestr("Metadata/plate_1.gcode", g)
    return p


DECL = {
    "plateau": "Textured PEI Plate",
    "filaments": {
        "1": {
            "matiere": "SUNLU",
            "fiche": "f",
            "emplacement_ace": 1,
            "temp_1re": 220,
            "temp": 240,
            "plateau_c": 55,
            "debit_max": 32,
        },
        "2": {
            "matiere": "eSUN",
            "fiche": "f",
            "emplacement_ace": 2,
            "temp_1re": 220,
            "temp": 220,
            "plateau_c": 55,
            "debit_max": 16,
        },
        "3": {
            "matiere": "Marble",
            "fiche": "f",
            "emplacement_ace": 3,
            "temp_1re": 210,
            "temp": 210,
            "plateau_c": 55,
            "debit_max": 12,
        },
    },
}
