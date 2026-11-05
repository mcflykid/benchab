# Du besoin au dossier d’envoi

Commencer avec `benchab --help`, puis `benchab init` si l’atelier n’existe pas
encore. `benchab seance debut` ouvre une séance. Les fichiers et décisions du
projet restent dans `prive/` ; le savoir technique anonymisé peut être publié
selon [la frontière du savoir](../decisions/2026-11-04-frontiere-public-prive.md).

## Définir la pièce

Décrire usage réel, cotes, pièces à assembler, charges et températures attendues,
faces visibles et surfaces à protéger. Un objet décoratif et un contenant ne
partagent pas nécessairement les mêmes critères. Préciser les inconnues plutôt
que choisir une valeur prétendument mesurée. Conserver original et empreinte.

Pour une création, suivre [la conception](preparer-une-piece.md) ; pour un modèle importé,
vérifier unités, dimensions, fermeture et transformations avant toute réparation.
Le droit d’utiliser un fichier ne vaut pas droit de publier un modèle tiers.

## Concevoir et vérifier l’assemblage

Contrôler géométrie exportée, épaisseur utile, jeux et trajectoire de montage.
Reprendre les mesures [U32 dans leur portée](../explications/fiabilite-fdm.md).
Si la pièce doit être découpée, vérifier zones protégées, connecteurs et
réassemblage ; un connecteur numériquement ajusté reste non validé physiquement.

## Préparer et trancher

Choisir machine, buse, plaque et [filament](../reference/filaments.md). Partir
d’un [profil réussi proche](../reference/profils-et-procedes.md), déclarer les
changements et préserver les surcharges par objet. Examiner orientation,
appuis, [supports](../explications/surplombs-ponts-supports.md) et
[couture](../explications/coutures.md) sur les faces visibles.

Consulter `benchab projet --help` pour créer un projet et les gabarits disponibles.
La [référence des commandes](../reference/commandes.md) décrit les opérations
communes. Utiliser uniquement `benchab fabrication trancher` pour le slicer,
avec un profil sauvegardé explicite. Consulter son aide pour les chemins ;
ne pas lancer le binaire directement ni changer les profils installés.

Dans un projet généré, `src/parametres.py` déclare `FILAMENTS`, `REGLAGES` et
`PLAQUES`. Pour reprendre les réglages embarqués d’une préparation privée,
déclarer aussi `PROFIL_REFERENCE = "../../references/profil-explicite.3mf"`
(chemin relatif à la racine du projet). Ce fichier doit exister dans `prive/` ;
son empreinte et les surcharges sont rapportées dans `preuves/profil-source.json`.
Ce nom d’exemple n’est pas un profil livré. Sans profil, un gabarit peut produire
une géométrie et des plaques de conception, mais le lanceur refuse un 3MF sans
modèle de machine Kobra S1/S1 Combo et sans buse explicitement déclarée à 0,4 mm.
Il ne complète jamais ces informations en sélectionnant un profil utilisateur.

## Relire et décider

Rouvrir chaque plaque du fichier tranché, vérifier matériau, paramètres embarqués,
supports et trajectoires aux zones critiques. Exécuter les contrôles concernés,
puis le contrôle d’envoi de la candidate figée. Une routine globale ne remplace
pas l’examen des alertes. Ne jamais contourner la cohérence BENCHAB.

Avant le vote, une relecture critique courte par un gros modèle sans historique
cherche aspect, usage, surplombs visibles et montage. Ses nouveaux risques
rejoignent A4 en NON VÉRIFIÉ. Sans cette relecture, vote EN ATTENTE. Si tout passe
ou si le vote est unanime, le dixième homme produit trois scénarios d’échec,
les signes précoces et le plan de secours. Voir [le vote](../reference/vote.md).

## Figer, transmettre et apprendre

Figer préparation, empreinte, plaques, contrôles, réglages et surveillance dans
le dossier d’envoi. Un fichier prêt numériquement n’est pas une preuve
d’impression. Aucune commande à la machine sans demande explicite.

Après la pièce réelle, suivre [retour d’impression](retour-impression.md), puis
terminer la séance avec les documents regroupés. Le résultat réel nourrit la
préparation suivante sans réécrire la précédente.

## Sources

Cycle historique de l’atelier, règles U24/U30/U31/U32, contrat du lanceur et
retours sur les limites d’un vote unanime :
[référence des règles](../reference/regles.md), [corpus technique](../../benchab/reference/connaissances.jsonl)
et [sources anonymisées](../../benchab/reference/sources-savoir.jsonl).

Après une étape importante, `benchab seance note "Fait, résultat et suite"`
consigne une note courte dans la séance active. `benchab seance etat` restitue
sa dernière note avec le lot et le début ; la clôture regroupe les vues et
l'export. Une note ne déclare pas une validation physique.

### Verrou de séance

`benchab seance debut` rend un jeton valable 24 h. Le conserver dans
l’environnement de la séance, sous `BENCHAB_JETON_SEANCE`, pour
`benchab seance fin`. La base ne garde que son empreinte. La commande
`benchab seance etat` indique si le verrou est actif ou orphelin. Si le
verrou est expiré ou absent, `benchab seance reprendre` conserve la séance
et fournit un nouveau jeton. `benchab seance fin --forcer` consigne une
fermeture explicite sans jeton. Les mesures d’une séance orpheline ne lui
sont pas rattachées.
