# Recomposition des contrôles permanents

BENCHAB sépare le code public, les connaissances techniques contextualisées et
les projets privés. La cohérence contrôle les représentations encore actives ;
elle ne recharge ni l’ancien atelier ni un profil personnel installé.

| Responsabilité | Contrôle actuel |
| --- | --- |
| Registre privé, relations et export | `benchab.qualite.coherence.verifier` compare SQLite, export et références. |
| Profils sources et preuves de filament | Le même contrôle relit les fichiers privés et leurs SHA ; une conversion historique nécessite un contrat explicite vérifié. |
| Vues et sections générées | Les vues sont recalculées depuis SQLite et comparées à leurs fichiers et au manifeste. |
| Projet, préparation et fichiers protégés | Les contrats privés et empreintes sont vérifiés ; aucune préparation figée n’est réécrite. |
| Documentation, licences et structure publique | `benchab.qualite.edition.verifier` vérifie liens, licences et nommage ; les exceptions de structure ont une preuve. |
| Réglages et 3MF transformés | Le dictionnaire typé et le contrôle d’intégrité avant/après restent obligatoires. |
| Références d’or | Les tests relisent les empreintes, réglages et commandes machine ; sans les données privées, ils sont non exécutés. |

## Représentations historiques retirées

Le catalogue global `BASE.json` de l’ancien atelier n’est pas recréé. Il figeait
les tailles et SHA des fichiers courants non volatils, y compris code et
documentation, puis devait être réactualisé après une modification. Les journaux
marqués `VOLATILS` étaient inventoriés sans comparaison SHA figée.
BENCHAB conserve les empreintes contractuelles des données et des préparations ;
Git porte les versions du code et des documents. La publication contrôle aussi
les fichiers de travail. **Ce n’est pas un contrôle d’identité global équivalent
à l’ancien catalogue.** Cette identité globale est abandonnée pour éviter une
seconde représentation réinscriptible du checkout ; les références d’or et les
preuves d’impression conservent leurs empreintes fixes.

L’ancien rapprochement entre un index de chantiers Markdown et des missions
Markdown est retiré : ces deux états concurrents ne sont pas des entrées actives
de BENCHAB. Le journal de séance et les vues privées utilisent SQLite. Le plan de
construction de l’atelier est un document privé temporaire ; il ne fait pas
partie des instructions permanentes du produit.

Le contrôle de conservation des mots clés de l’ancien noyau d’instructions
n’est pas présenté comme un contrôle des 3MF. Son heuristique de mots exacts et
de destinations documentaires anciennes est retirée, avec ses vérifications
de présence des unités U, de fiches et de renvois. Sa branche historique
contrôlait aussi une limite de mots, inactive dans la dernière variante du noyau. La reprise des consignes
est tracée dans le registre de migration ; le savoir, les règles documentées,
AGENTS.md et SKILL.md restent distincts. La relecture critique vérifie les
consignes nouvelles ou modifiées. Il n’existe pas de contrôle automatique
prétendant prouver leur équivalence sémantique ; l’automatisation des annotations
de code appartient à la feuille de route suivante.

Ces retraits sont des changements de contrat explicites. Ils n’autorisent pas à
ignorer un FAIL de cohérence, une preuve manquante, un réglage inconnu ou une
mutation non déclarée d’un 3MF. Les archives historiques restent intactes.

## Dépendances entre fichiers

L’ancienne carte de dépendances attachée au registre JSON des filaments n’est
plus une entrée active. Ses chemins de l’ancienne architecture ne sont pas
recréés. Ce retrait est distinct des relations SQL : une clé étrangère ne prouve
jamais qu’un fichier existe. Les fichiers encore déclarés par les projets,
préparations, profils, fiches, vues et contrats de connaissances ont leurs
contrôles de présence et d’intégrité. Le graphe d’impact est une aide partielle
au choix des tests ; il ne certifie pas l’absence d’une dépendance non reconnue.

Les fiches de recherche des filaments créées par BENCHAB sont des vues privées
des données canoniques ; leur présence et leur contenu sont contrôlés. Les
fiches historiques conservées dans l’archive restent immuables : le bloc de
référence est comparé aux données reprises, sans réécriture ni recalibration.

Les fiches modernes sont régénérées avec les autres vues par `benchab donnees vues`, après mise à jour canonique de SQLite. Cette génération ne réécrit aucune fiche historique archivée.
