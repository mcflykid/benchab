"""Les dimensions d'étude sont une configuration distincte du volume machine."""

from unittest.mock import patch

import pytest

from benchab.controles import orientations_supports


def test_zone_etude_reprend_les_limites_historiques():
    assert orientations_supports.zone_etude() == (230.0, 230.0, 240.0)


@pytest.mark.parametrize("dimensions", [None, [230, 230], [230, 230, float("inf")], [230, 0, 240]])
def test_zone_etude_refuse_une_configuration_invalide(dimensions):
    with patch.object(
        orientations_supports,
        "charger_configuration",
        return_value={"placement": {"zone_etude_mm": dimensions}},
    ):
        with pytest.raises(ValueError, match="zone_etude_mm"):
            orientations_supports.zone_etude()
