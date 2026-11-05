# Composants tiers

Inventaire des versions verrouillées, vérifié le 05/11/2026. Les dépendances restent
séparées du code BENCHAB et conservent leurs notices lors de l’installation.
Le contrôle de publication compare cet inventaire à `uv.lock` et aux licences
des copies intégrées ; toute licence inconnue ou AGPL est bloquante.

| Composant | Version ou révision | Licence | Utilisation et provenance |
| --- | --- | --- | --- |
| text-to-cad, moteur cadgen 0.8.4 et outil DfAM | `5a5dab9503d140b8cf62a28d21ba2573a722d2d6` | MIT, Thompson Labs LLC (2026) | Moteur déterministe cadgen épinglé, télémétrie désactivée et aucun daemon ; outil DfAM source vérifié puis adapté au paquet ; licence conservée dans `benchab/tiers/text-to-cad-LICENSE.txt`. Source : [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad). |
| PyMeshLab | `2025.7.post1` | GPL-3.0 | Interface CGAL Alpha Wrap, dans le Python unique. [Projet et licence](https://github.com/cnr-isti-vclab/PyMeshLab). |
| meshoptimizer Python | `0.2.30a0` | MIT | Simplification avec erreur absolue et bords conservés. [Distribution](https://pypi.org/project/meshoptimizer/0.2.30a0/). |

meshoptimizer : Copyright (c) 2016-2026, Arseny Kapoulkine. Les fichiers des
distributions conservent leurs licences. Les traitements ne changent pas la
licence des modèles d'entrée.

| 3d-print-modeling, contrôles assemblage/parois/contacts/invariants | Copie figée identifiée par SHA-256 dans `benchab/tiers/modeling-sources.json` | MIT, Mohsen Esmaeili (2026) | Fonctions issues des scripts `assembly_check.py`, `wallcheck.py`, `fitmap.py`, `checks_template.py` ; licence vérifiée avant import et conservée dans `benchab/tiers/modeling-LICENSE.txt`. Aucun programme source exécuté. |
| cryptography | `46.0.7` (contrainte `>=44,<47`) | Apache-2.0 ou BSD-3-Clause | API AES-128-CBC/PKCS7 pour décoder la réponse de handshake MQTT locale; aucune copie de code tiers. Licence duale indiquée par le dépôt officiel [pyca/cryptography](https://github.com/pyca/cryptography/blob/main/LICENSE). |

## Bibliothèques Python, directes et transitives

L’inventaire couvre toutes les variantes de plateforme du verrou, développement compris.
Les empreintes des notices consultées se trouvent dans `benchab/reference/licences.json`.
Les expressions composées gardent les obligations des composants embarqués ; pour
les licences duales cryptography et packaging, la branche Apache-2.0 est retenue.

| Distribution | Version | Licence retenue |
| --- | --- | --- |
| anytree | 2.13.0 | Apache-2.0 |
| asttokens | 3.0.2 | Apache-2.0 |
| bd-materials | 0.2.5 | Apache-2.0 |
| benchab | 1.0.0 | GPL-3.0-or-later |
| build123d | 0.13.0 | Apache-2.0 |
| cadgen | 0.8.4 | MIT |
| cadquery-ocp-novtk | 8.0.1.1.0 | Apache-2.0 |
| cadquery-ocp-proxy | 8.0.1.1.0 | Apache-2.0 |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| cloudpickle | 3.1.2 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| contourpy | 1.4.0 | BSD-3-Clause |
| cryptography | 46.0.7 | Apache-2.0 |
| cycler | 0.12.1 | BSD-3-Clause |
| dataclasses-json | 0.6.7 | MIT |
| deprecated | 3.0.1 | MIT |
| editables | 0.6 | MIT |
| executing | 2.3.0 | MIT |
| ezdxf | 1.4.5 | MIT |
| fonttools | 4.68.0 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| hatchling | 1.33.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| ipython | 9.18.1 | BSD-3-Clause |
| ipython-pygments-lexers | 1.1.1 | BSD-3-Clause |
| jedi | 0.20.1 | MIT |
| joblib | 1.6.0 | BSD-3-Clause |
| kiwisolver | 1.5.1 | BSD-3-Clause |
| lib3mf | 2.5.0 | BSD-3-Clause |
| manifold3d | 3.5.4 | Apache-2.0 |
| mapbox-earcut | 2.1.0 | ISC |
| marshmallow | 3.26.2 | MIT |
| matplotlib | 3.11.2 | LicenseRef-Matplotlib |
| matplotlib-inline | 0.2.2 | BSD-3-Clause |
| meshoptimizer | 0.2.30a0 | MIT |
| mpmath | 1.3.0 | BSD-3-Clause |
| mypy-extensions | 1.2.0 | MIT |
| narwhals | 2.28.0 | MIT |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| ocp-gordon | 0.3.1 | Apache-2.0 |
| ocpsvg | 0.7.0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 |
| parso | 0.8.7 | MIT |
| pathspec | 1.1.1 | MPL-2.0 |
| pexpect | 4.9.0 | ISC |
| pillow | 13.0.0 | MIT-CMU |
| platformdirs | 4.14.5 | MIT |
| playwright | 1.64.1 | Apache-2.0 |
| pluggy | 1.7.0 | MIT |
| prompt-toolkit | 3.0.53 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| ptyprocess | 0.7.0 | ISC |
| pure-eval | 0.2.4 | MIT |
| py-lib3mf | 2.5.0 | Apache-2.0 AND BSD-2-Clause AND MIT AND Zlib |
| pycparser | 3.12 | BSD-3-Clause |
| pyee | 14.0.0 | MIT |
| pygltflib | 1.16.5 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pymeshlab | 2025.7.post1 | GPL-3.0-only |
| pyparsing | 3.3.3 | MIT |
| pytest | 9.2.0 | MIT |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| requests | 2.34.2 | Apache-2.0 |
| rtree | 1.4.1 | MIT |
| ruff | 0.17.3 | MIT |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.18.1 | BSD-3-Clause |
| shapely | 2.2.0 | BSD-3-Clause |
| stack-data | 0.6.3 | MIT |
| svgelements | 1.9.6 | MIT |
| svgpathtools | 1.8.0 | MIT |
| svgwrite | 1.4.3 | MIT |
| sympy | 1.14.0 | BSD-3-Clause |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| threejs-materials | 1.2.5 | Apache-2.0 |
| tomlkit | 0.15.2 | MIT |
| traitlets | 5.16.1 | BSD-3-Clause |
| trianglesolver | 1.2 | MIT |
| trimesh | 5.2.0 | MIT |
| trove-classifiers | 2026.9.21.13 | Apache-2.0 |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspect | 0.9.0 | MIT |
| tzdata | 2026.6 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| wcwidth | 0.9.4 | MIT |
| webcolors | 24.8.0 | BSD-3-Clause |
| wrapt | 2.5.2 | BSD-2-Clause |

## Copies intégrées et composants natifs

Three.js 0.160.0 est sous MIT ; sa notice reste dans
`benchab/visualisation/vendor/three-0.160.0/LICENSE`. Les contrôles MIT copiés sont
identifiés dans `benchab/tiers/modeling-sources.json` ; leurs modifications
d’adaptation sont signalées dans les en-têtes des modules.

Les wrappers CadQuery OCP sont sous Apache-2.0. Leur moteur Open CASCADE
reste sous LGPL-2.1 avec exception OCCT, pas sous la licence du wrapper. Voir
[licence du wrapper](https://raw.githubusercontent.com/CadQuery/OCP/master/LICENSE)
et [licence du moteur](https://occt3d.com/open-cascade-technology/index.html).
PyMeshLab/CGAL sont utilisés par une distribution GPL-3 ; aucune bibliothèque
binaire n’est recopiée dans ce dépôt. Les notices des roues restent conservées.
Matplotlib utilise sa licence propre dérivée de PSF/BSD, identifiée par
LicenseRef-Matplotlib et par SHA de sa notice ; ce n’est pas une licence inconnue acceptée par défaut.

Deux variantes non installées sur ce Mac sont examinées depuis leurs sources :
[colorama 0.4.6](https://raw.githubusercontent.com/tartley/colorama/0.4.6/LICENSE.txt)
(BSD-3-Clause), et
[py-lib3mf](https://raw.githubusercontent.com/jdegenstein/py-lib3mf/main/License.txt)
(Apache-2.0, composants lib3mf BSD, libzip BSD-3, zlib et cpp-base64 Zlib).
L’infrastructure OCP.wasm de ce dernier conserve sa
[notice MIT](https://raw.githubusercontent.com/jdegenstein/py-lib3mf/main/LICENSE-3RD-PARTY.txt).
Le fork py-lib3mf n’est pas confondu avec la distribution lib3mf.

Le logo est fourni par l’atelier pour ce dépôt. Les modèles d’entrée gardent leurs
licences propres et restent privés. Aucun code AGPL n’est repris.
Les licences permissives, MPL-2.0 sans modification et LGPL avec exception du
moteur restent accompagnées de leurs obligations ; la licence du dépôt est
GPL-3.0-or-later et ne remplace pas les licences tierces.
