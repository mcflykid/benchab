-- Schéma commun nécessaire : aucune base relationnelle n'existait dans le paquet.
PRAGMA foreign_keys = ON;

CREATE TABLE projets (
    id TEXT PRIMARY KEY NOT NULL,
    nom TEXT NOT NULL,
    origine TEXT,
    etat TEXT NOT NULL DEFAULT 'inconnu',
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE preparations (
    id TEXT PRIMARY KEY NOT NULL,
    projet_id TEXT NOT NULL REFERENCES projets(id) ON DELETE RESTRICT,
    nom TEXT NOT NULL,
    date TEXT,
    empreinte TEXT,
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json)),
    UNIQUE (projet_id, nom)
);

CREATE TABLE impressions (
    id TEXT PRIMARY KEY NOT NULL,
    preparation_id TEXT NOT NULL REFERENCES preparations(id) ON DELETE RESTRICT,
    date TEXT,
    resultat TEXT NOT NULL DEFAULT 'inconnu'
        CHECK (resultat IN ('inconnu', 'reussie', 'echec', 'interrompue')),
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE retours (
    id TEXT PRIMARY KEY NOT NULL,
    impression_id TEXT NOT NULL REFERENCES impressions(id) ON DELETE RESTRICT,
    date TEXT,
    texte TEXT NOT NULL,
    source TEXT NOT NULL,
    validation_physique INTEGER NOT NULL DEFAULT 0 CHECK (validation_physique IN (0, 1)),
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE filaments (
    id TEXT PRIMARY KEY NOT NULL,
    nom TEXT NOT NULL,
    matiere TEXT NOT NULL,
    fabricant TEXT,
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE bobines (
    id TEXT PRIMARY KEY NOT NULL,
    filament_id TEXT NOT NULL REFERENCES filaments(id) ON DELETE RESTRICT,
    masse_initiale_g REAL CHECK (masse_initiale_g >= 0),
    masse_restante_g REAL CHECK (masse_restante_g >= 0),
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE profils (
    id TEXT PRIMARY KEY NOT NULL,
    nom TEXT NOT NULL,
    source TEXT NOT NULL,
    empreinte TEXT,
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE connaissances (
    id TEXT PRIMARY KEY NOT NULL,
    texte TEXT NOT NULL,
    categorie TEXT NOT NULL CHECK (categorie IN
        ('fabricant', 'embarque', 'numerique', 'observation', 'physique', 'decision')),
    source TEXT NOT NULL,
    date_source TEXT,
    public INTEGER NOT NULL DEFAULT 0 CHECK (public IN (0, 1)),
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE decisions (
    id TEXT PRIMARY KEY NOT NULL,
    projet_id TEXT REFERENCES projets(id) ON DELETE RESTRICT,
    date TEXT,
    texte TEXT NOT NULL,
    source TEXT NOT NULL,
    donnees_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(donnees_json))
);

CREATE TABLE votes (
    id TEXT PRIMARY KEY NOT NULL,
    preparation_id TEXT NOT NULL REFERENCES preparations(id) ON DELETE RESTRICT,
    date TEXT NOT NULL,
    grille_version TEXT NOT NULL,
    resultat TEXT NOT NULL CHECK (resultat IN ('pour', 'contre', 'abstention')),
    donnees_json TEXT NOT NULL CHECK (json_valid(donnees_json))
);

CREATE TABLE seances (
    id TEXT PRIMARY KEY NOT NULL,
    debut TEXT NOT NULL,
    fin TEXT,
    lot TEXT,
    candidates INTEGER NOT NULL DEFAULT 0 CHECK (candidates BETWEEN 0 AND 3),
    temps_vote_s REAL NOT NULL DEFAULT 0 CHECK (temps_vote_s >= 0),
    temps_controles_s REAL NOT NULL DEFAULT 0 CHECK (temps_controles_s >= 0)
);

CREATE TABLE mesures (
    id TEXT PRIMARY KEY NOT NULL,
    seance_id TEXT REFERENCES seances(id) ON DELETE RESTRICT,
    date TEXT NOT NULL,
    commande TEXT NOT NULL,
    duree_s REAL NOT NULL CHECK (duree_s >= 0),
    cpu_utilisateur_s REAL NOT NULL CHECK (cpu_utilisateur_s >= 0),
    cpu_systeme_s REAL NOT NULL CHECK (cpu_systeme_s >= 0),
    memoire_max_octets INTEGER NOT NULL CHECK (memoire_max_octets >= 0),
    code INTEGER NOT NULL
);

CREATE UNIQUE INDEX une_seance_active ON seances ((1)) WHERE fin IS NULL;

CREATE TABLE versions (
    id TEXT PRIMARY KEY NOT NULL,
    numero TEXT NOT NULL,
    date TEXT NOT NULL,
    changement TEXT NOT NULL,
    source TEXT
);

PRAGMA user_version = 1;
