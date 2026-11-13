# Frontière entre savoir technique public et données privées

Décision de l’auteur du 04/11/2026, précisée le 05/11/2026. La précision remplace
la règle antérieure qui réservait par défaut toute observation au domaine privé.

Le savoir technique est public, même issu de l’expérience de l’auteur : machine,
ACE 2 Pro, slicer et incidents, filaments, profils, procédés, mesures de jeux,
températures et débits, réglages réussis, erreurs, défauts, causes établies ou
supposées et parades. Son origine personnelle ne justifie pas de le perdre.

## Portée obligatoire de chaque connaissance

- Source : « observé par l’auteur sur sa Kobra S1 Combo » pour un retour local.
  Documentation fabricant et analyse numérique gardent leur propre attribution ;
  elles ne deviennent pas une observation physique.
- Conditions : filament, réglages et date ; une information absente reste inconnue.
- Nombre d’occurrences : nombre réel d’essais, jamais nombre de journaux. S’il
  n’est pas établi, il est explicitement inconnu.
- Statut : mesuré, observé une fois, hypothèse ou validé dans un domaine décrit.
  Valeur de profil, estimation et mesure physique restent distinctes.

Une réussite locale n’est pas une prescription universelle. Une nouvelle
observation peut contredire une cause supposée sans effacer le défaut constaté.
Les anciennes instructions et hypothèses sont signalées comme historiques ;
les corrections actuelles des guides prévalent.

## Ce qui reste privé

| Contenu | Traitement |
| --- | --- |
| Fichiers de projets, modèles, préparations, G-code et photos | Dans `prive/`, jamais dans la référence publique. |
| Noms et identifiants de projets | Remplacés par le type de pièce, par exemple lithophane sphérique d’un modèle tiers. |
| Modèles tiers | Aucune redistribution, même si le savoir tiré de leur impression est public. |
| Stock de bobines et données personnelles | Base et configuration privées ; une fiche matière ne décrit pas le stock. |
| Comptes, identifiants de machine, adresses et secrets | Jamais dans le dépôt ni dans les rapports publics. |
| Témoins S, M, L et fixtures générés par l’atelier | Seules géométries publiques admises, avec provenance vérifiable. |

Tous les projets restent privés, y compris les créations personnelles de
l’auteur. L’anonymisation retire le contexte personnel sans effacer le savoir,
les réglages, les contre-exemples ni les limites de preuve.

## Mise en œuvre et traçabilité

Le savoir est livré dans [connaissances.jsonl](../../benchab/reference/connaissances.jsonl).
Chaque entrée décrit sa portée et sa provenance ; les sources historiques sont
référencées par [identifiant neutre et empreinte](../../benchab/reference/sources-savoir.jsonl).
Le registre privé conserve la correspondance exacte avec les documents originaux,
les décisions de tri et les faits non techniques ou personnels.

`benchab controle publication` et `benchab controle frontiere` continuent à
contrôler fichiers publics, liens sortants, secrets et modèles. L’exception des
témoins et fixtures exige un manifeste des empreintes du fichier et de son
générateur public ; toute modification invalide cette exception.

Les noms et alias de projets, les noms de modèles tiers et les identifiants
personnels sont lus dans la base privée au moment du contrôle. Cette liste
n'est jamais livrée avec le paquet. Le filtre s'applique aux chemins, aux textes
et aux membres décompressés des ZIP/3MF publics. Une archive illisible ou trop
volumineuse pour être contrôlée est refusée. Le paramètre privé de la commande
détermine la base consultée, même si elle est séparée du dépôt contrôlé.

L’index Git est vérifié pour la racine privée effectivement résolue, ainsi
que pour le préfixe historique `prive/`. Le parcours public ne lit aucun
de ces sous-arbres. Une racine privée égale à la racine publique est refusée.

### Homonymes du vocabulaire public

Une collision vérifiée avec un alias peut être attestée dans le fichier privé
`collisions-alias-publics.json`. Chaque entrée est indexée par le chemin public
relatif et contient `sha256` (les octets publics vérifiés), `motifs_sha256`
(les empreintes des motifs d’alias concernés) et une `justification` locale.
Les empreintes de motif sont calculées sur la représentation UTF-8 du motif
retourné par `benchab.qualite.confidentialite.motifs`, sans l’afficher.

L’attestation concerne le contenu de ce fichier seulement. Une modification
des octets réactive le contrôle complet. Les chemins, archives, noms et
identifiants explicites, courriels et jetons passent leurs contrôles habituels.
Un nom complet également déclaré comme alias reste protégé.
