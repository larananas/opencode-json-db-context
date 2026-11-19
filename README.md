# OpenCode JSON DB Context 🗄️

**Transformez le schéma d'une base de données relationnelle en un seul JSON détaillé, prêt à être utilisé comme contexte par une IA.**

Cet outil est un agent spécialisé pour [OpenCode](https://opencode.ai/). Il inspecte les bases SQL accessibles, décrit les colonnes, les contraintes, les index et les relations, compte les lignes réelles et ajoute jusqu'à **20 lignes d'exemple par table**. Il enregistre le résultat dans **un seul fichier JSON lisible**, dans `export/`.

Pas besoin de documenter les tables manuellement, de copier des résultats SQL dans le chat ou de produire une collection de CSV. L'extraction s'exécute localement via un serveur MCP.

> **Périmètre : bases relationnelles uniquement.** MongoDB, les bases documentaires, les graphes et les magasins clé-valeur ne sont pas pris en charge. Ce projet n'est **pas** un outil de sauvegarde intégrale : seules la structure et des données d'exemple sont exportées.

## ✨ Fonctionnalités

- **Six moteurs SQL :** MariaDB, MySQL, PostgreSQL, Microsoft SQL Server, Oracle Database (mode Thin) et SQLite.
- **Périmètre au choix :** une table, plusieurs tables, un schéma, une base, plusieurs bases ou les bases accessibles d'un serveur, selon le moteur.
- **Description détaillée :** colonnes, types, valeurs par défaut, contraintes, clés primaires et étrangères, index, commentaires, vues et autres métadonnées disponibles.
- **Vrais comptages :** `COUNT(*)` par table (`total_rows`), distinct des estimations du moteur.
- **Exemples représentatifs :** jusqu'à 20 lignes par table, ou toutes les lignes si la table en contient moins.
- **Exemples liés par défaut :** ajout prioritaire de lignes parentes référencées par les exemples enfants, lorsque les relations déclarées et le périmètre le permettent.
- **Grosses tables :** méthode d'échantillonnage plus légère pour certaines clés primaires entières ; les limitations sont signalées.
- **Mode compact, en option :** remplacement des grandes valeurs texte/binaires par un résumé pour réduire la taille du JSON.
- **Un JSON final :** pas de CSV ni de fichiers temporaires conservés dans `export/`. Les anciens JSON restent disponibles.
- **Validation intégrée :** totaux, tables détectées, échantillons, erreurs et avertissements.
- **Lecture uniquement :** les outils MCP ne modifient pas les données ni le schéma SQL.

## 🚀 Installation (Windows, Linux et macOS)

L'installation se fait **dans le dossier du projet**, quel que soit le processeur (x86-64, ARM64, etc.). Le script choisit une installation Python compatible avec votre machine. Aucune dépendance à Rosetta ou à une architecture matérielle particulière n'est codée en dur.

### Étape 1 — Récupérer le projet

Téléchargez le ZIP du dépôt GitHub et décompressez-le, ou clonez le dépôt :

```sh
git clone https://github.com/larananas/opencode-json-db-context.git
```

Le dépôt public est disponible sur GitHub.

### Étape 2 — Lancer l'assistant d'installation

**macOS ou Linux**, depuis le répertoire décompressé :

```sh
./setup.sh
```

**Windows**, dans PowerShell depuis le répertoire décompressé :

```powershell
.\setup.ps1
```

Si la politique d'exécution PowerShell empêche le lancement, utilisez une session temporaire autorisée selon les règles de votre organisation ; le projet ne modifie pas cette politique à votre place.

Le script :

1. cherche **Python 3.10 ou plus récent** ainsi que `venv`, `ensurepip`, `ssl` et `sqlite3` ;
2. indique précisément ce qui manque ; si Python est absent, **propose** une installation au moyen du gestionnaire de paquets du système (jamais sans accord) ;
3. prépare un environnement Python indépendant, `.venv/`, dans le projet ;
4. demande votre confirmation avant d'installer les dépendances MCP et les pilotes SQL dans cet environnement ;
5. vérifie que les bibliothèques s'importent correctement ;
6. crée la configuration locale `.opencode/opencode.jsonc`, qui utilise le bon interpréteur pour votre machine.

Cette configuration locale et `.venv/` sont **ignorés par Git**. Si vous déplacez le projet vers un autre ordinateur ou remplacez Python, relancez simplement l'installation.

> **Limites réalistes :** l'installation automatique de Python dépend du gestionnaire de paquets disponible, des droits nécessaires et de l'existence de paquets compatibles avec le système. Sur Windows, le script utilise `winget` s'il est présent ; sur macOS, Homebrew ; sur Linux, les gestionnaires détectés (`apt`, `dnf`, `pacman`, `zypper`, `apk`). Si l'installation automatique ne convient pas, le programme affiche la marche à suivre. Aucun système n'est garanti sans prérequis.

### Étape 3 — Ouvrir OpenCode

Ouvrez ce dossier dans OpenCode ou lancez `opencode` depuis ce répertoire. Le profil `tohru-db` est l'agent principal par défaut.

Dans le chat, demandez par exemple :

```text
Connecte-toi à mon serveur MariaDB et montre-moi les bases disponibles.
```

Ou utilisez les commandes :

- `/connect-db` pour établir une connexion ;
- `/export-db` pour exporter un périmètre précis.

L'agent répond normalement dans **la langue de l'utilisateur** : les instructions techniques internes sont rédigées en anglais, mais aucune langue n'est imposée pour la conversation.

### Diagnostiquer l'installation, sans rien modifier

Sur macOS/Linux :

```sh
./setup.sh --check
```

Sur Windows :

```powershell
.\setup.ps1 -Check
```

Ces commandes vérifient Python, l'environnement virtuel, les dépendances et la configuration locale. Elles n'installent rien. En cas de problème, relancez l'assistant d'installation et approuvez les opérations nécessaires.

## 🔐 Connexion : identifiants, `.env` ou fichier local

**Le fonctionnement par défaut est volontairement simple : fournissez seulement vos identifiants ou le contenu d’un `.env`, et Tohru exporte toutes les bases relationnelles non système accessibles à ce compte, avec toutes leurs tables, dans un unique JSON.** La présence de `DB_DATABASE` dans un `.env` sert à établir la connexion, **elle ne limite pas à elle seule l’export**.

Vous gardez le contrôle du périmètre : si vous demandez explicitement une seule base, un schéma, une table ou une liste de tables, Tohru **n’exporte que cette sélection**. Si vous demandez uniquement une connexion ou une liste de bases, **aucun export n’est lancé**.

### Méthode A — Coller ou joindre un `.env` dans le chat

Collez des variables existantes dans OpenCode (ou joignez un fichier si votre client permet d'en lire le contenu) :

```dotenv
DB_CONNECTION=mariadb
DB_HOST=127.0.0.1
DB_PORT=3307
DB_DATABASE=example_app
DB_USERNAME=example_reader
DB_PASSWORD=replace_with_your_local_password
```

Puis : « Utilise cette configuration. » Tohru reconnaît notamment `DATABASE_URL`, `DB_URL`, `DB_*`, `MYSQL_*`, `MARIADB_*`, `POSTGRES_*`, `PG*`, `MSSQL_*`, `SQLSERVER_*`, `ORACLE_*` et `SQLITE_*`, ainsi que les URL de connexion usuelles. Les formes ambigües ou les variables `${...}` non résolues sont signalées au lieu d’être devinées silencieusement.

**Attention :** coller ou joindre un `.env` à une conversation IA peut transmettre des secrets au fournisseur du modèle. Réservez cette méthode à des identifiants appropriés et autorisés ; utilisez plutôt la méthode B pour les secrets sensibles.

### Méthode B — Lire un `.env` local sans exposer son mot de passe au modèle (recommandée)

1. Placez votre `.env` dans le dossier **`.local/`** du projet (par exemple `.local/db.env` ou `.local/.env`). Ce répertoire est ignoré par Git.
2. Dans OpenCode, écrivez : « Connecte-toi en lisant `.local/db.env` et exporte les bases accessibles. »
3. Tohru appelle l’outil `db_connect_env` avec le **nom du fichier**, sans copier son contenu dans la conversation. Le MCP le lit localement, et conserve les identifiants uniquement en mémoire pour la connexion.

Pour éviter toute lecture arbitraire de fichiers, seuls les fichiers `.env` situés dans `.local/` sont acceptés ; les liens symboliques pointant hors de ce dossier sont refusés. **Ne collez pas ensuite les secrets dans le chat.**

### Docker / Compose : quel hôte et quel port ?

Le hostname `db`, `mysql` ou `postgres` d’un fichier Docker Compose fonctionne généralement **entre conteneurs**. Si OpenCode s’exécute sur l’ordinateur hôte, il faut normalement utiliser `127.0.0.1` et **le port publié** (par exemple `3307:3306` signifie port hôte `3307`).

Tohru détecte les hôtes évocateurs d’un réseau Docker et explique le décalage éventuel. Il ne prétend pas connaître la configuration Docker sans information suffisante et n’essaie pas de modifier les conteneurs.

### Exemples de demandes

```text
Voici mon .env. Connecte-toi avec ces identifiants.
```

→ **Par défaut : toutes les bases non système accessibles, un seul JSON**.

```text
Utilise ces identifiants, mais exporte uniquement la base inventory.
```

→ **Une base uniquement**.

```text
Exporte seulement users, roles et permissions depuis app_database.
```

→ **Trois tables uniquement**, sans élargissement.

```text
Connecte-toi à ce serveur et liste les bases, sans rien exporter.
```

→ **Connexion et exploration uniquement**.

```text
Exporte tout le serveur avec 20 exemples liés par table.
```

→ Export global et échantillons liés **best effort**, sans dépasser 20 lignes par table.

Les instructions de l’agent sont en anglais, mais il répond dans la langue de votre demande. L’outil reste utilisable pour des questions SQL sans demander systématiquement un export.

## 🧩 Que contient le JSON ?

Chaque export comprend les sections principales suivantes :

| Section | Contenu |
|---|---|
| `export_metadata` | Date, moteur, version, serveur, périmètre, paramètres d'échantillonnage |
| `databases` | Bases → schémas → tables et vues → colonnes, contraintes et exemples |
| `relationships` | Relations explicitement déclarées par les clés étrangères |
| `validation` | Contrôles de cohérence, erreurs, avertissements et états de complétude |
| `summary` | Statistiques globales et par base : tables, colonnes, relations, lignes et exemples |

### Exemple MariaDB (fictif, volontairement abrégé)

Cet exemple montre **une base contenant deux tables liées**. Il reprend la hiérarchie d'un export réel, avec des noms et valeurs fictifs ; les métadonnées natives complètes sont plus détaillées dans les vrais fichiers.

```json
{
  "export_metadata": {
    "engine": "mariadb",
    "server": "localhost",
    "scope": "database",
    "requested_database": "demo_shop",
    "sample_limit_per_table": 20,
    "coherent_samples": true,
    "database_count": 1
  },
  "databases": {
    "demo_shop": {
      "metadata": {
        "database": [{ "SCHEMA_NAME": "demo_shop", "DEFAULT_CHARACTER_SET_NAME": "utf8mb4" }],
        "triggers": [],
        "routines": [],
        "events": []
      },
      "schemas": {
        "demo_shop": {
          "tables": {
            "customers": {
              "name": "customers",
              "schema": "demo_shop",
              "type": "table",
              "create_sql": "CREATE TABLE `customers` (`id` int NOT NULL, `name` varchar(100) NOT NULL, PRIMARY KEY (`id`)) ENGINE=InnoDB",
              "columns": [
                { "COLUMN_NAME": "id", "ORDINAL_POSITION": 1, "DATA_TYPE": "int", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI" },
                { "COLUMN_NAME": "name", "ORDINAL_POSITION": 2, "DATA_TYPE": "varchar", "IS_NULLABLE": "NO" }
              ],
              "primary_key": ["id"],
              "foreign_keys": [],
              "indexes": [{ "Key_name": "PRIMARY", "Column_name": "id", "Seq_in_index": 1 }],
              "unique_constraints": [],
              "check_constraints": [],
              "estimated_rows": 1,
              "total_rows": 1,
              "row_count_status": "exact",
              "sample_count": 1,
              "sample_rows": [{ "id": 1, "name": "Example Customer" }]
            },
            "orders": {
              "name": "orders",
              "schema": "demo_shop",
              "type": "table",
              "create_sql": "CREATE TABLE `orders` (`id` int NOT NULL, `customer_id` int NOT NULL, `total_amount` decimal(10,2) NOT NULL, PRIMARY KEY (`id`), CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`)) ENGINE=InnoDB",
              "columns": [
                { "COLUMN_NAME": "id", "ORDINAL_POSITION": 1, "DATA_TYPE": "int", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI" },
                { "COLUMN_NAME": "customer_id", "ORDINAL_POSITION": 2, "DATA_TYPE": "int", "IS_NULLABLE": "NO", "COLUMN_KEY": "MUL" },
                { "COLUMN_NAME": "total_amount", "ORDINAL_POSITION": 3, "DATA_TYPE": "decimal", "IS_NULLABLE": "NO" }
              ],
              "primary_key": ["id"],
              "foreign_keys": [
                {
                  "name": "fk_orders_customer",
                  "source_database": "demo_shop",
                  "source_schema": "demo_shop",
                  "source_table": "orders",
                  "source_columns": ["customer_id"],
                  "target_database": "demo_shop",
                  "target_schema": "demo_shop",
                  "target_table": "customers",
                  "target_columns": ["id"],
                  "update_rule": "RESTRICT",
                  "delete_rule": "RESTRICT"
                }
              ],
              "indexes": [],
              "unique_constraints": [],
              "check_constraints": [],
              "estimated_rows": 1,
              "total_rows": 1,
              "row_count_status": "exact",
              "sample_count": 1,
              "sample_rows": [
                { "id": 101, "customer_id": 1, "total_amount": { "$type": "decimal", "value": "42.50" } }
              ]
            }
          },
          "views": {}
        }
      },
      "table_count": 2,
      "view_count": 0
    }
  },
  "relationships": [
    {
      "name": "fk_orders_customer",
      "source_database": "demo_shop",
      "source_schema": "demo_shop",
      "source_table": "orders",
      "source_columns": ["customer_id"],
      "target_database": "demo_shop",
      "target_schema": "demo_shop",
      "target_table": "customers",
      "target_columns": ["id"],
      "update_rule": "RESTRICT",
      "delete_rule": "RESTRICT"
    }
  ],
  "validation": {
    "errors": [],
    "warnings": [],
    "schema_complete": true,
    "sampling_complete": true,
    "row_counts_complete": true
  },
  "summary": {
    "database_count": 1,
    "table_count": 2,
    "column_count": 5,
    "foreign_key_count": 1,
    "sampled_rows": 2,
    "total_rows_exact": 2
  }
}
```

Dans cet exemple, `orders.customer_id = 1` pointe bien sur `customers.id = 1`. En **mode par défaut**, Tohru conserve les exemples aléatoires, puis essaie d’inclure les lignes parentes référencées par ces exemples **dans les tables déjà sélectionnées**. La cohérence est un objectif *best effort* et peut être partielle si 20 lignes ne suffisent pas. Vous pouvez demander `coherent_samples=false` pour conserver des exemples totalement indépendants.

## 🗃️ Bases de données et compatibilité

| Moteur | Pilote Python | État |
|---|---|---|
| MariaDB | PyMySQL | Export réel vérifié |
| MySQL | PyMySQL | Adaptateur implémenté, intégration réelle à compléter |
| PostgreSQL | pg8000 | Adaptateur implémenté, intégration réelle à compléter |
| Microsoft SQL Server | python-tds | Adaptateur implémenté, intégration réelle à compléter |
| Oracle Database | python-oracledb (Thin) | Adaptateur implémenté, intégration réelle à compléter |
| SQLite | Bibliothèque standard Python | Tests d'intégration vérifiés |
| MongoDB | Aucun | **Non pris en charge** |

Les métadonnées accessibles et les options d'authentification dépendent du serveur et des permissions de connexion. Le DDL complet `CREATE TABLE` n'est **pas garanti** pour tous les moteurs. Oracle fonctionne en mode Thin, avec les limitations d'authentification propres à ce mode ; son périmètre « serveur » correspond aux schémas du service Oracle connecté.

### SQLite : emplacement des fichiers

Par conception, OpenCode n'a pas d'accès général hors du dépôt. Les fichiers SQLite doivent donc se trouver **dans le projet**, par exemple dans `.local/` (répertoire ignoré par Git). Les chemins qui pointent vers l'extérieur, y compris au travers d'un lien symbolique, sont refusés.

## 🔒 Permissions sans fenêtres de confirmation répétées

Les tâches ordinaires — import `.env`, connexion SQL, exploration, export JSON — sont **autorisées par défaut** via les outils MCP dédiés. Pas de demande supplémentaire pour chaque lecture ou export.

En contrepartie, le profil OpenCode du projet **n'autorise pas** le shell général, les modifications libres de fichiers ni les accès à un répertoire externe. Cela permet à l'agent de générer des requêtes SQL et de répondre à des questions sans lui donner le droit d'exécuter des programmes arbitraires sur l'ordinateur.

La base SQL est inspectée en **lecture seule**. L'outil ne fournit aucune commande de modification des données ou du schéma. Pour de telles opérations, l'agent peut expliquer la requête à effectuer, mais pas l'exécuter via le MCP livré.

> Attention aux données confidentielles : les échantillons du JSON sont **réels** et peuvent contenir des identifiants, adresses ou jetons. Le dossier `export/` est ignoré par Git. Les mots de passe indiqués dans une conversation avec un modèle externe peuvent être transmis au fournisseur du modèle : utilisez de préférence un compte SQL dédié en lecture seule.

Les configurations OpenCode globales ou les politiques administrées peuvent compléter ou remplacer des autorisations de projet. Cette configuration limite le profil livré ; ce n'est pas un bac à sable système formel.

## 📂 Structure du projet

```text
opencode-json-db-context/
├── .OpenCode                   # Repère informatif
├── .gitignore                  # Exports, pilotes et secrets locaux exclus
├── .github/workflows/          # CI multiplateforme et intégration SQL
├── .opencode/
│   ├── agents/tohru-db.md
│   ├── commands/connect-db.md
│   ├── commands/export-db.md
│   └── skills/database-export/SKILL.md
├── AGENTS.md                   # Consignes techniques de l'agent (anglais)
├── LICENSE                     # Licence MIT
├── README.md                   # Documentation (français)
├── opencode.jsonc              # Paramètres et permissions du projet
├── requirements.txt            # Pilotes et SDK MCP
├── setup.sh                    # Installation macOS et Linux
├── setup.ps1                   # Installation Windows
├── scripts/
│   ├── setup.py                # Diagnostic et configuration locale
│   ├── db_engine.py            # Extraction relationnelle
│   ├── env_config.py           # Analyse et validation .env locale
│   ├── mcp_server.py           # Outils MCP
│   ├── check_publication.py    # Vérification des secrets/publication
│   └── run_mcp.py              # Compatibilité et diagnostic
└── tests/                      # Tests automatiques
```

Après installation, `.venv/`, `.opencode/opencode.jsonc`, `.local/` et `export/` apparaissent localement et **ne sont pas publiés**.

## 🧪 Tests et compatibilité multiplateforme

La suite locale comprend des tests sur l’export, les contraintes, les échantillons liés, les requêtes SQL par dialecte, les configurations `.env`, les chemins locaux, l’installation et les permissions.

Depuis la racine du projet :

```sh
python3 -m unittest discover -s tests -v
```

Sur Windows, utilisez `py -3 -m unittest discover -s tests -v` si la commande `python3` est absente. Les tests d’intégration SQL réels sont **ignorés** sans serveur de test explicitement configuré.

GitHub Actions exécute, après publication :

- **Vérification multiplateforme** : Windows x64/ARM64, Linux x64/ARM64 et macOS Intel/Apple Silicon, plus Linux Python 3.10. Ces jobs vérifient le démarrage guidé, les pilotes installés, la configuration MCP et les tests SQLite.
- **Intégration SQL réelle** : trois serveurs éphémères MariaDB, MySQL et PostgreSQL. Chaque job crée deux tables de test liées, exporte un vrai JSON et vérifie les contraintes, les exemples et les compteurs.
- **Protection du dépôt** : vérification des fichiers sensibles suivis et de l’exclusion des fichiers générés.

> Les workflows fournis ne constituent **pas** des résultats de CI. Il faut les publier et attendre leurs exécutions pour connaître leur statut réel, notamment sur Windows ARM64 et macOS Intel. Oracle et SQL Server nécessitent encore des tests de connexion réels séparés et ne sont pas couverts par les trois conteneurs automatisés.

Les tests unitaires peuvent réussir alors qu’un service SQL particulier refuse l’authentification, certains privilèges ou des métadonnées. Signalez les problèmes d’intégration avec le moteur et sa version **sans joindre de données réelles ni de secrets**.

### Publication publique

- Ne committez jamais les fichiers `export/`, `.local/`, `.venv/`, les `.env` réels, les bases SQLite ou la configuration générée `.opencode/opencode.jsonc`.
- Exécutez `python3 scripts/check_publication.py` pour repérer des fichiers privés évidents ; un contrôle humain et la détection native des secrets GitHub restent recommandés.
- Le projet est sous **licence MIT**. L’archive d’origine ne contient pas de jeux de données personnels.

## 🤝 Contribuer

Les correctifs, tests d'intégration et retours d'utilisation sont les bienvenus, notamment sur les variantes d'Oracle, de SQL Server et les distributions Linux. **Ne joignez jamais un vrai export de base de données, mot de passe ou jeton dans un ticket ou une demande de contribution.**

**Une structure SQL détaillée. Des exemples concrets. Un seul JSON pour vos IA.** 🐉