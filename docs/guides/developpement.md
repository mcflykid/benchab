# Développer et vérifier le dépôt

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run --locked --group typage python -m mypy
uv run benchab controle typographie docs
uv run benchab controle publication
```

La CI vérifie les types des frontières communes (chemins, réglages, processus,
interpréteur G-code et base). Elle analyse aussi le corps des fonctions sans
signature complète. Les erreurs des modules importés hors périmètre restent
hors du lot, et les dépendances sans annotations ne sont pas assimilées à une
preuve de typage. Le groupe `typage` est réservé à la vérification et n’est pas
une dépendance d’exécution.

Les rapports d’optimisation et les études de stratégie incluent les empreintes
des modules de préparation de copies et d’entrées qu’ils utilisent. Déplacer
une fonction vers un fichier neuf impose d’ajouter ce fichier à leur contexte.
Une nouvelle empreinte peut nécessiter une nouvelle étude ; les anciens
rapports et préparations restent consultables et ne sont pas écrasés.

Les tests construisent leurs propres fixtures et isolent l’atelier privé.
Ils bloquent aussi par défaut les résolutions DNS et connexions externes avant
tout trafic ; les sockets UNIX et la boucle locale restent accessibles pour les
tests de la visionneuse. Cette garde intercepte les API socket du processus
pytest ; elle ne s’applique pas aux processus enfants lancés par les tests. Les
rapports de test portent le statut d’isolation.
Une recette réseau doit demander explicitement la fixture
`reseau_externe_autorise` ; elle est réservée aux essais manuels prévus à cet
effet et ne doit pas être ajoutée aux tests ordinaires (#65).
Les hooks résolvent le dossier courant à chaque appel, depuis la charge utile
quand elle le fournit. Leur filtre des commandes shell est une aide heuristique,
pas une barrière de sécurité : une écriture détournée peut lui échapper. Les
contrôles explicites des livrables et des empreintes restent nécessaires (#21).
La CI macOS exécute les tests unitaires sans slicer ; les essais natifs et Blender sont
séparés et demandent leurs programmes externes. L’outil d’impact existant choisit
les tests avec les imports, liens documentaires, appels Make et relations JSON
déclarées (`dependances`). Il revient à la suite complète si l’impact est inconnu.
Il n’existe qu’un système d’impact.

`controle publication` bloque les chemins et identifiants personnels, secrets,
bases publiques, liens vers le privé, modèles non autorisés, licences non recensées
ou incompatibles, documents mal encodés et liens morts. Les liens internes sont
vérifiés localement ; les liens HTTP des documents sont interrogés en lecture.
Un échec réseau laisse le contrôle en échec, sans présumer un lien valide.
Les inventaires de licences et de fixtures sont liés aux versions et empreintes.
Une nouvelle dépendance nécessite la lecture de sa licence puis la mise à jour de
[TIERS.md](../../TIERS.md) et du manifeste associé.

Les tests d'acceptation sont nommés par domaine, pour rester sélectionnables
directement par l'outil d'impact sans dépendre des numéros de lots historiques.
Leurs chemins explicites restent acceptés par pytest.

| Domaine | Fichier |
| --- | --- |
| Schéma relationnel | [test_acceptation_schema_relationnel.py](../../tests/acceptation/test_acceptation_schema_relationnel.py) |
| Séances, initialisation et confidentialité | [test_acceptation_seance_initialisation_confidentialite.py](../../tests/acceptation/test_acceptation_seance_initialisation_confidentialite.py) |
| Préparation et envoi | [test_acceptation_preparation_et_envoi.py](../../tests/acceptation/test_acceptation_preparation_et_envoi.py) |
| Conseil et opportunités | [test_acceptation_conseil_et_opportunites.py](../../tests/acceptation/test_acceptation_conseil_et_opportunites.py) |
| Données privées et vues | [test_acceptation_donnees_privees_et_vues.py](../../tests/acceptation/test_acceptation_donnees_privees_et_vues.py) |
| Capacités et création de projets | [test_acceptation_capacites_et_projets.py](../../tests/acceptation/test_acceptation_capacites_et_projets.py) |
| Découpe automatisée | [test_acceptation_decoupe_automatisee.py](../../tests/acceptation/test_acceptation_decoupe_automatisee.py) |
| Qualité et publication | [test_acceptation_qualite_publication.py](../../tests/acceptation/test_acceptation_qualite_publication.py) |
| Chaîne sans slicer | [test_acceptation_chaine_sans_slicer.py](../../tests/acceptation/test_acceptation_chaine_sans_slicer.py) |
| Installation du skill | [test_acceptation_installation_skill.py](../../tests/acceptation/test_acceptation_installation_skill.py) |

Ne jamais ajouter de projet réel pour faire passer un test public. Les témoins et
fixtures 3D autorisés doivent être générés par le code public et enregistrés avec
leurs empreintes. Conserver les licences des bibliothèques intégrées ; les modèles
d’entrée gardent leur propre licence. Les fonctionnalités futures ne sont pas
ajoutées sous forme de modules vides.

Le contrôle `uv run benchab controle typographie CHEMIN` accepte aussi un rapport
JSON généré : il examine les textes des valeurs, sans traiter les clés comme de
la prose. Il détecte les collages mot/nombre et nombre/unité ; blocs de code,
chemins et identifiants techniques explicites sont distingués des phrases.
