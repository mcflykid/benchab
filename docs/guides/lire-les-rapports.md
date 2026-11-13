# Lire un rapport sans perdre les preuves

Pour vérifier une préparation, commencez par le statut et les points à
examiner. Conservez le JSON complet dans l’atelier : il garde les
détails de chaque plaque, les raisons du verdict et, selon le contrôle,
l’empreinte de la source.

| Contrôle | Affichage court avec rapport complet | Afficher le détail |
| --- | --- | --- |
| Envoi, plages Z | `--sortie RAPPORT.json` | Ajouter `--detail` |
| Continuité, capacités, comparaison 3MF, purge, écarts Z des supports, rejeux G-code | `--resume --sortie RAPPORT.json` | Garder `--sortie` et retirer `--resume` |
| Entretien, consommation d’un retour, plan de réparation | `--resume --sortie RAPPORT.json` | Garder `--sortie` et retirer `--resume` |
| Débit U25, `surplombs-connexes` | `--resume --sortie RAPPORT.json` | Remplacer `--resume` par `--detail` |
| Couches courtes | `--resume --sortie RAPPORT.json` | Garder `--sortie` et retirer `--resume` |
| Paquet 3MF | `--resume --sortie RAPPORT.json` | Lire le JSON écrit ou retirer les deux options |
| Vissage, fentes d’interface | `--resume --json RAPPORT.json` | Garder `--json` et retirer `--resume` |
| Finition des parois | `analyser … --json RAPPORT.json` | Ajouter `--detail` |
| Bilan des dépôts | `--sortie RAPPORT.json` | Ajouter `--detail` |
| Appuis G-code | `--resume --json RAPPORT.json` | Garder `--json` et retirer `--resume` |
| Première couche | `--resume --json RAPPORT.json` | Remplacer `--resume` par `--detail` |
| Coutures | `--sortie DOSSIER_NEUF` | Ajouter `--detail` |
| Contrôle complet | `--resume` | Retirer `--resume` ou lire le rapport déjà écrit |
| Règles et opportunité de découpe | `--resume --sortie RAPPORT.json` | Lire le rapport écrit ou retirer `--resume` |

Choisissez une destination neuve dans `prive/rapports/`. Les options
d’entrée restent propres à chaque commande : voir les
[commandes](../reference/commandes.md) et leur aide. Le résumé ne change ni
le verdict, ni le code de la commande. Un résultat NON VÉRIFIÉ garde sa
raison ; une erreur qui empêche la lecture peut être signalée sur stderr
sans produire de rapport.

Par exemple, pour un fichier déjà tranché :

```sh
uv run benchab controle purge-changements prive/archives/temoin.3mf \
  --resume --sortie prive/rapports/purge-01.json
```

Sur trois témoins S/M/L, cet affichage occupait 297 octets chacun, contre
1 227 octets pour l’affichage complet. Les rapports gardés contenaient les
mêmes données. Sur ces témoins mono, la purge physique est restée NON VÉRIFIÉE.
Sur trois plaques déjà tranchées (support, flexion et ailettes), le
bilan des dépôts a affiché entre 598 et 623 octets en mode court, contre
2 966 à 5 552 octets avec `--detail`. Les deux modes ont donné un JSON
enregistré identique et le même verdict sur chaque plaque.

Pour chercher un détail, lisez directement le JSON avec votre éditeur ou
filtrez les champs utiles avec `jq`. Dans un assistant de code déjà équipé de
[Context Mode](https://github.com/mksglu/context-mode/blob/main/README.md),
ce plugin facultatif peut limiter ce qui est remonté dans le contexte.
Il n’est pas requis par BENCHAB et ne remplace pas le rapport complet.
Pour un rapport privé, transmettez seulement les champs nécessaires au
contexte de travail et gardez le JSON brut dans l’atelier (#35).
