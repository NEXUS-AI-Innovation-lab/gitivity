# Gateway IAM - Documentation Projet

Ce fichier fournit le contexte complet sur le projet Gateway IAM.

## Vue d'ensemble

**Gateway IAM** est un systeme de provisionnement d'identites qui connecte MidPoint (IAM) aux services cibles (LDAP, Odoo, PostgreSQL, MySQL) via un connecteur Java, une gateway Python (FastAPI), et un workflow d'approbation n8n.

## Architecture Globale

```
+----------+   +-----------------+   +-----------+   +------------------+
|          |   |  Java Connector |   |  RabbitMQ |   | Python Consumer  |
| MIDPOINT |-->|  (ConnId)       |-->|   Queue   |-->| (start_api.sh)   |
|   (IAM)  |   |  RestGateway    |   |           |   |                  |
+----------+   +--------+--------+   +-----------+   +--------+---------+
     ^                  |                                      |
     |                  | HTTP (entitlements)                   v
     |                  v                              +--------------+
     |           +-------------+                       | FastAPI      |
     |           | Gateway HTTP|                       | (port 8000)  |
     |           | (Flask 5000)|                       | Orchestrator |
     |           | Entitlements|                       +------+-------+
     |           +-------------+                              |
     |                                                        |
     |   +----------------------------------------------------+
     |   |         |              |             |              |
     |   v         v              v             v              v
     | +------+ +------+ +-----------+ +-------+ +-----------+
     | | LDAP | | Odoo | | PostgreSQL| | MySQL | |   Redis   |
     | +------+ +------+ +-----------+ +-------+ +-----------+
     |                                                         |
     |   +------------------+                                  |
     +---| MidPoint REST API|<--- Role removal on rejection    |
         | (unassign role)  |                                  |
         +------------------+                                  |
                                                               |
         +------------------+     +---------------------------+
         |   n8n (5678)     |<--->| Approval workflow         |
         | Approval emails  |     | Multi-level chain         |
         | AI analysis      |     | Wait/Resume webhooks      |
         +------------------+     +---------------------------+
```

## Structure du Projet

```
gateway-iam/
|-- CLAUDE.md                        # Ce fichier
|-- .env                             # Variables d'environnement
|-- data/
|   +-- approvers.json               # Liste des approbateurs (niveaux)
|-- start/
|   +-- start_api.sh                 # Script de demarrage de l'API
|-- app/
|   |-- main.py                      # Point d'entree FastAPI
|   |-- config/
|   |   +-- settings.py              # Configuration Pydantic BaseSettings
|   |-- api/v1/
|   |   |-- router.py                # Routeur principal
|   |   +-- endpoints/
|   |       |-- connectors.py        # CRUD + health checks connecteurs
|   |       |-- provisioning.py      # Provisioning + approval callback
|   |       +-- approvers.py         # CRUD approbateurs
|   |-- core/
|   |   |-- orchestrator.py          # Orchestrateur principal (flow complet)
|   |   +-- connectors/
|   |       |-- base.py              # Interface abstraite ProvisioningConnector
|   |       |-- factory.py           # Factory pattern
|   |       |-- postgresql_connector.py
|   |       |-- mysql_connector.py
|   |       |-- ldap_connector.py
|   |       +-- odoo_connector.py
|   |-- services/
|   |   |-- midpoint_client.py       # Client REST MidPoint (search, unassign)
|   |   |-- audit_service.py         # Service d'audit
|   |   +-- n8n_client.py            # Client n8n webhooks
|   |-- db/
|   |   |-- redis_client.py          # Client Redis
|   |   +-- repositories/
|   |       |-- provisioning_repository.py
|   |       +-- approval_redis_repository.py
|   |-- models/
|   |   +-- domain.py                # MidPointMessage, UserData, etc.
|   |-- static/
|   |   |-- connectors.html          # Dashboard connecteurs
|   |   +-- approvers.html           # Dashboard approbateurs
|   +-- utils/
|       |-- enums.py                 # OperationType, TargetService, etc.
|       +-- exceptions.py            # Exceptions metier
|-- connecteur/
|   |-- config.json                  # Configuration connecteurs + entitlements
|   |-- gateway-http.py              # Gateway HTTP Flask (entitlements)
|   |-- consumer-rabbitmq.py         # Consumer Python simple
|   +-- idm-connector-rest-gateway/  # Connecteur Java MidPoint
|       |-- build.gradle
|       +-- src/main/java/lu/lns/connector/restgateway/
|           |-- RestGatewayConnector.java
|           |-- RestGatewayConfiguration.java
|           |-- RestGatewayClient.java
|           |-- RabbitMQClient.java
|           +-- JsonMapper.java

n8n-approval/
+-- workflows/
    +-- approval-workflow.json       # Workflow n8n d'approbation multi-niveaux
```

---

## API FastAPI (port 8000)

**Chemin**: `app/main.py`

L'API principale du gateway. Recoit les messages de RabbitMQ via le consumer, orchestre la validation, l'approbation, et le provisioning.

### Endpoints principaux

| Endpoint | Methode | Description |
|----------|---------|-------------|
| `/` | GET | Info racine + liens dashboards |
| `/health` | GET | Health check API |
| `/dashboard/connectors` | GET | Page web dashboard connecteurs |
| `/dashboard/approvers` | GET | Page web dashboard approbateurs |
| `/api/v1/connectors` | GET | Liste tous les connecteurs (Gateway + MidPoint) |
| `/api/v1/connectors/gateway/{name}/test` | POST | Tester un connecteur Gateway |
| `/api/v1/connectors/midpoint/{oid}/test` | POST | Tester un connecteur MidPoint |
| `/api/v1/approvers` | GET/POST | Lister / ajouter approbateurs |
| `/api/v1/approvers/{id}` | PUT/DELETE | Modifier / supprimer approbateur |
| `/api/v1/provisioning/{id}/approve-callback` | POST | Callback n8n approbation |

### Lancement

```bash
cd gateway-iam
./start/start_api.sh
# API sur http://localhost:8000
```

---

## Composants Principaux

### 1. Connecteur Java (idm-connector-rest-gateway)

**Chemin**: `connecteur/idm-connector-rest-gateway/`

Connecteur ConnId pour MidPoint qui envoie les operations CREATE/UPDATE/DELETE vers RabbitMQ ou HTTP.

**Classes principales**:
| Classe | Description |
|--------|-------------|
| `RestGatewayConnector.java` | Implemente CreateOp, UpdateDeltaOp, DeleteOp, SearchOp, SchemaOp |
| `RestGatewayConfiguration.java` | Proprietes de configuration (@ConfigurationProperty) |
| `RestGatewayClient.java` | Client HTTP avec fetchLdapGroups(), fetchPostgresqlProfiles(), fetchMysqlProfiles() |
| `RabbitMQClient.java` | Publication messages vers RabbitMQ |
| `JsonMapper.java` | Conversion attributs ConnId vers JSON |

**Modes de fonctionnement**:
- **Mode RabbitMQ** (`useRabbitmq=true`): Publie les operations dans une queue
- **Mode HTTP** (`useRabbitmq=false`): Appelle directement la gateway HTTP

**ObjectClass supportes**:
| ObjectClass | Type | Description |
|-------------|------|-------------|
| `__ACCOUNT__` | account | Utilisateurs MidPoint |
| `Role` | generic | Roles |
| `Service` | generic | Services |
| `Organisation` | generic | Organisations |
| `LdapGroup` | entitlement | Groupes LDAP |
| `PostgresqlProfile` | entitlement | Profils droits PostgreSQL |
| `MysqlProfile` | entitlement | Profils droits MySQL |

**Build**:
```bash
cd connecteur/idm-connector-rest-gateway
./gradlew clean jar
# JAR: build/libs/connector-restgateway-1.2.0-SNAPSHOT.jar
```

**Deploiement dans MidPoint**:
```bash
docker cp build/libs/connector-restgateway-*.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

---

### 2. Gateway HTTP (gateway-http.py)

**Chemin**: `connecteur/gateway-http.py`

API Flask qui expose les entitlements pour MidPoint (decouverte des droits).

**Endpoints**:
| Endpoint | Methode | Description |
|----------|---------|-------------|
| `/create` | POST | Creation utilisateur |
| `/update` | POST | Modification utilisateur |
| `/delete` | POST | Suppression utilisateur |
| `/health` | GET | Health check |
| `/entitlements/ldap-groups` | GET | Liste groupes LDAP |
| `/entitlements/postgresql-profiles` | GET | Profils PostgreSQL |
| `/entitlements/mysql-profiles` | GET | Profils MySQL |

**Lancement**:
```bash
cd connecteur
python gateway-http.py
# Serveur sur http://localhost:5000
```

---

### 3. Orchestrateur (orchestrator.py)

**Chemin**: `app/core/orchestrator.py`

Classe `ProvisioningOrchestrator` qui gere le flow complet :

```
Message RabbitMQ
  → Creer operation (PENDING)
  → Verifier marqueur Redis rejected_create (pour UPDATE/DELETE)
  → Valider avec n8n (VALIDATING → VALIDATED)
  → Demander approbation multi-niveaux (APPROVAL_PENDING)
  → [Callback n8n] → Provisionner vers service cible (PROCESSING → SUCCESS)
  → Envoyer notification de succes
```

**Methodes cles**:
| Methode | Description |
|---------|-------------|
| `process_message()` | Point d'entree principal |
| `_request_approval()` | Envoie la demande a n8n avec liste d'approbateurs |
| `process_approval_response()` | Callback apres decision (approve/reject) |
| `_remove_midpoint_role()` | Retire le role MidPoint apres rejet CREATE |
| `_load_approvers()` | Charge les approbateurs depuis `data/approvers.json` |
| `_provision_to_target()` | Provisionne vers le service cible via ConnectorFactory |

---

## Approbation Multi-niveaux

### Principe

Chaque demande de provisioning passe par une chaine d'approbateurs triee par niveau. Le niveau 1 approuve en premier, puis le niveau 2, etc. Si un approbateur rejette, la chaine s'arrete immediatement.

### Configuration des approbateurs

**Fichier**: `data/approvers.json`
```json
{
  "approvers": [
    {"id": "1", "name": "Admin", "email": "admin@company.com", "level": 1},
    {"id": "2", "name": "Directeur", "email": "dir@company.com", "level": 2}
  ]
}
```

**Dashboard web**: `http://<host>:8000/dashboard/approvers`
- Ajouter, modifier, supprimer des approbateurs
- Visualiser les niveaux avec badges colores

**API CRUD**: `GET/POST /api/v1/approvers`, `PUT/DELETE /api/v1/approvers/{id}`

### Workflow n8n

**Fichier**: `n8n-approval/workflows/approval-workflow.json`

```
Approval Webhook (POST /webhook/approval)
  → Respond 202 Accepted
  → Prepare Approvers (Code: trie par level, 1 item par approbateur)
  → Loop Over Approvers (SplitInBatches, batchSize=1)
    → Build Approver Email (Code: analyse IA, roles filtres, detection UPDATE)
    → Send Approval Email (emailSend via SMTP Gmail)
    → Wait for Decision (webhook resume)
    → Parse Decision (Code: lecture query.approved)
    → IF Approved?
      → [Oui] → Retour a Loop Over Approvers (prochain niveau)
      → [Non] → Callback Gateway (Rejected)
  → [Tous approuves] → Prepare Callback Data
    → IF CREATE_USER? → Build Credentials Email → Send Credentials Email
    → Callback Gateway (Approved)
```

**Points techniques n8n**:
- `SplitInBatches` v3 : Output 0 = "Done" (tous traites), Output 1 = "Loop" (item courant)
- `Wait` node en mode webhook : `{WEBHOOK_URL}/webhook-waiting/{executionId}?approved=true|false`
- `emailSend` ne passe PAS les donnees en input → utiliser `$('NodeName').first().json`
- URLs approve/reject dans l'email pointent vers le Wait node

### Analyse IA dans les emails

L'email d'approbation contient une section "Analyse automatique" qui detecte :
- Stagiaire/intern avec role admin
- Roles admin sur plusieurs services
- Suppression d'un compte admin
- Creation sans email
- Absence de description/poste
- Nombre eleve de roles (>4)
- **Pour UPDATE** : modification sans changement visible, changement de mot de passe, modification de roles admin

### Filtrage des roles

Pour chaque email, seuls les roles pertinents au service cible sont affiches (ex: pour Odoo, seulement les roles contenant "odoo" + les roles generiques comme "admin", "readonly", "readwrite"). Chaque role est affiche sur sa propre ligne.

### Emails UPDATE

Pour les operations UPDATE, l'email montre uniquement les champs modifies dans une section "Modifications apportees" (fond vert), au lieu d'afficher toutes les informations utilisateur.

---

## Rejet CREATE et MidPoint

### Probleme

Quand MidPoint assigne un role a un utilisateur, le connecteur envoie un CREATE vers le gateway. Si l'approbation est rejetee, l'utilisateur n'est jamais provisionne sur le service cible, mais MidPoint garde le role assigne. Cela cause des incoherences.

### Solution implementee

1. **Marqueur Redis** : quand un CREATE est rejete, un marqueur `rejected_create:{username}:{service}` est stocke dans Redis (TTL 7 jours)

2. **Suppression role MidPoint** : apres le rejet, `_remove_midpoint_role()` :
   - Cherche l'utilisateur dans MidPoint par username (`search_user()`)
   - Recupere ses assignments (roles)
   - Pour chaque role de type `RoleType`, resout le nom via `get_role()`
   - Si le nom contient le service cible → `unassign_role()` (PATCH avec modification delta)

3. **Gateway intelligente** : si un UPDATE arrive pour un utilisateur dont le CREATE a ete rejete :
   - L'UPDATE est converti en CREATE (l'utilisateur n'existe pas sur le service cible)
   - Le marqueur Redis est conserve jusqu'a ce qu'un DELETE arrive

### API MidPoint utilisee

| Methode | Endpoint | Description |
|---------|----------|-------------|
| `search_user()` | GET `/ws/rest/users?name={name}` | Recherche utilisateur |
| `get_user()` | GET `/ws/rest/users/{oid}` | Details utilisateur |
| `get_role()` | GET `/ws/rest/roles/{oid}` | Details role |
| `unassign_role()` | PATCH `/ws/rest/users/{oid}` | Suppression assignment |

---

## Redis

**Utilise pour** :
- **Pending approvals** : stockage temporaire des demandes en attente d'approbation, incluant le message MidPoint complet pour reconstruction apres callback
- **Rejected CREATE markers** : marqueurs des CREATE rejetes pour detection des UPDATE/DELETE subsequents (TTL 7 jours)

**Configuration** : `REDIS_HOST`, `REDIS_PORT` dans `.env`

---

## Dashboards Web

### Dashboard Connecteurs
**URL**: `http://<host>:8000/dashboard/connectors`

Affiche l'etat de tous les connecteurs :
- **Gateway connectors** : LDAP, PostgreSQL, MySQL, Odoo (health check en temps reel)
- **MidPoint resources** : ressources MidPoint avec etat operationnel
- Bouton "Tester" pour verifier la connexion
- Configuration affichee par connecteur

### Dashboard Approbateurs
**URL**: `http://<host>:8000/dashboard/approvers`

Gestion des approbateurs :
- Tableau avec nom, email, niveau (badges colores)
- Ajout via modale (nom, email, niveau 1-5)
- Modification et suppression en ligne

---

## Connecteurs Gateway

Chaque connecteur implemente l'interface `ProvisioningConnector` :

| Methode | Description |
|---------|-------------|
| `connect()` | Etablir la connexion (pool, bind, session) |
| `disconnect()` | Fermer la connexion |
| `health_check()` | Verifier la connectivite (requiert `connect()` prealable) |
| `provision_user()` | Creer un utilisateur |
| `update_user()` | Modifier un utilisateur |
| `delete_user()` | Supprimer un utilisateur |

**Important** : `health_check()` retourne toujours `False` si `connect()` n'a pas ete appele avant. Le pattern correct est :
```python
connector = ConnectorFactory.create(service)
await connector.connect()       # Obligatoire avant health_check
is_healthy = await connector.health_check()
await connector.disconnect()
```

### PostgreSQL
- Roles mappes : `readonly` → `pg_read_all_data`, `readwrite` → `pg_read_all_data + pg_write_all_data`
- Priorite : `postgresqlGrants` > `postgresqlRole` > `roles`
- Supporte les profils depuis config.json ou les privileges SQL directs

### MySQL
- Roles mappes : `readonly` → `SELECT`, `readwrite` → `SELECT,INSERT,UPDATE,DELETE`, `admin` → `ALL PRIVILEGES`
- Meme systeme de priorite que PostgreSQL

### LDAP
- Creation dans `ou=Users` sous le base DN
- Ajout comme `member` des groupes LDAP specifies dans `ldapGroups`

### Odoo
- Connexion via XML-RPC
- Gestion des utilisateurs et droits Odoo

---

## Configuration (config.json)

**Chemin**: `connecteur/config.json`

Configuration des connecteurs cibles et des entitlements.

```json
{
  "connecteurs": ["LDAP"],
  "configs": {
    "LDAP": { "host": "localhost", "port": "389", ... },
    "SQL": { "host": "", "port": "5432", ... },
    "Odoo": { "host": "", "port": "8069", ... }
  },
  "entitlements": {
    "postgresql": {
      "profiles": [
        {"profileName": "readonly", "grants": "SELECT", "description": "Lecture seule"},
        {"profileName": "readwrite", "grants": "SELECT, INSERT, UPDATE", "description": "Lecture et ecriture"},
        {"profileName": "admin", "grants": "ALL", "description": "Administrateur complet"}
      ]
    },
    "mysql": {
      "profiles": [ ... ]
    }
  }
}
```

---

## Entitlements

Les entitlements permettent a MidPoint de decouvrir et gerer les droits disponibles.

### Flux de decouverte
1. MidPoint fait un "Refresh" sur la ressource
2. Le connecteur Java appelle `executeQuery()` pour l'ObjectClass entitlement
3. Le connecteur appelle la gateway HTTP (`/entitlements/*`)
4. La gateway retourne les groupes/profils disponibles depuis config.json
5. MidPoint cree des Shadows avec OIDs pour chaque entitlement
6. Les roles peuvent utiliser `shadowRef` pour associer des entitlements

### Re-import des entitlements (si perdus)

Si les entitlements disparaissent de MidPoint (shadows supprimes) :
1. S'assurer que `gateway-http.py` tourne sur le port 5000
2. Verifier : `curl http://localhost:5000/entitlements/postgresql-profiles`
3. Dans MidPoint : Resources → REST Gateway Resource → Content
4. Selectionner l'ObjectClass (ex: `CustomPostgresqlProfileObjectClass`)
5. Cliquer "Import" ou "Reconcile"
6. Les shadows seront recrees automatiquement

### Types d'entitlements
| Type | Source | Endpoint |
|------|--------|----------|
| Groupes LDAP | Serveur LDAP (ou mock) | `/entitlements/ldap-groups` |
| Profils PostgreSQL | config.json | `/entitlements/postgresql-profiles` |
| Profils MySQL | config.json | `/entitlements/mysql-profiles` |

---

## Configuration MidPoint

### Ressource XML

**Fichier**: `SAR5-n8n/chibanie/ressource-rabbit-mq-1.1.xml`

**Points importants**:
- OID: `736ea741-2c73-4478-b5d1-07d84cdf860f`
- Connecteur version: `1.2.0-SNAPSHOT`
- `gatewayUrl`: `http://172.17.0.1:5000` (IP Docker host, pas localhost!)
- `useRabbitmq`: `true`

**Naming des ObjectClass dans schemaHandling**:
MidPoint ajoute le prefixe `Custom` aux ObjectClass non-standard:
- `LdapGroup` → `ri:CustomLdapGroupObjectClass`
- `PostgresqlProfile` → `ri:CustomPostgresqlProfileObjectClass`
- `MysqlProfile` → `ri:CustomMysqlProfileObjectClass`

### Docker Networking - IMPORTANT

MidPoint tourne dans Docker. Pour que le connecteur puisse appeler la gateway Python sur l'hote:
- Utiliser `172.17.0.1` (IP du host Docker) au lieu de `localhost`
- `localhost` dans le conteneur = le conteneur lui-meme

```xml
<gen685:gatewayUrl>http://172.17.0.1:5000</gen685:gatewayUrl>
<gen685:rabbitmqHost>172.17.0.1</gen685:rabbitmqHost>
```

Les services Docker (LDAP, PostgreSQL, MySQL, etc.) exposent leurs ports sur l'hote. La gateway Python (qui tourne directement sur la VM, pas dans Docker) y accede via `localhost:port`.

---

## Format des Messages JSON

Messages envoyes vers RabbitMQ/HTTP par le connecteur Java :

```json
{
  "operation": "CREATE|UPDATE|DELETE",
  "entityType": "User|Role|Service|Organisation",
  "uid": "uuid-unique",
  "timestamp": "2026-02-06T10:30:00Z",
  "attributes": {
    "__NAME__": "username",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "roles": ["Developer", "Admin"],
    "ldapGroups": ["cn=Developers,ou=Groups,dc=example,dc=com"],
    "enabled": true
  }
}
```

---

## Attributs Mappes MidPoint -> Connector

| Attribut Connector | Source MidPoint |
|--------------------|-----------------|
| `icfs:name` | `$focus/name` |
| `ri:firstName` | `$focus/givenName` |
| `ri:lastName` | `$focus/familyName` |
| `ri:fullName` | `$focus/fullName` |
| `ri:email` | `$focus/emailAddress` |
| `ri:telephoneNumber` | `$focus/telephoneNumber` |
| `ri:organization` | `$focus/organization` (script) |
| `ri:organizationalUnit` | `$focus/organizationalUnit` (script) |
| `ri:roles` | Calcule depuis assignments |
| `ri:ldapGroups` | Calcule depuis roles -> mapping DN |
| `ri:enabled` | `$focus/activation/administrativeStatus` |

---

## Commandes Utiles

### Demarrer tous les services
```bash
# 1. RabbitMQ (Docker)
docker-compose -f RabbitMQ/docker-compose-rabbitmq.yml up -d

# 2. Gateway HTTP (entitlements pour MidPoint)
cd connecteur && python gateway-http.py

# 3. API FastAPI + Consumer RabbitMQ
cd gateway-iam && ./start/start_api.sh

# 4. n8n (Docker ou natif)
# Importer le workflow: n8n-approval/workflows/approval-workflow.json
```

### Build et deploiement connecteur Java
```bash
cd connecteur/idm-connector-rest-gateway
./gradlew clean jar
docker cp build/libs/connector-restgateway-*.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

### Tests
```bash
# Tester les endpoints entitlements
curl http://localhost:5000/entitlements/ldap-groups
curl http://localhost:5000/entitlements/postgresql-profiles
curl http://localhost:5000/entitlements/mysql-profiles

# Tester l'API FastAPI
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/connectors
curl http://localhost:8000/api/v1/approvers

# Tests Java
cd connecteur/idm-connector-rest-gateway
./gradlew test
```

---

## Troubleshooting

### Connecteurs "Deconnecte" sur le dashboard
- Verifier que les services Docker sont lances : `docker ps`
- Verifier la config `.env` (LDAP_HOST=localhost, POSTGRESQL_HOST=localhost, etc.)
- Les services Docker exposent leurs ports sur localhost via `-p`
- Le health_check necessite que `connect()` soit appele avant

### Entitlements vides ou supprimes dans MidPoint
1. Verifier que `gateway-http.py` est lance sur le port 5000
2. Verifier que `gatewayUrl` utilise `172.17.0.1:5000` (pas `localhost`)
3. Tester : `curl http://localhost:5000/entitlements/postgresql-profiles`
4. Re-import dans MidPoint : Resources → Resource → Content → ObjectClass → Import

### MidPoint user not found pour role removal
- Verifier les logs : le parsing gere plusieurs formats de reponse MidPoint REST API
- Verifier que l'utilisateur existe dans MidPoint (recherche dans l'admin UI)
- Les logs affichent les cles de la reponse pour debug

### "Unknown object class LdapGroupObjectClass"
Le schemaHandling doit utiliser `ri:CustomLdapGroupObjectClass` (avec prefixe Custom).

### Messages non recus dans RabbitMQ
1. Verifier que RabbitMQ est demarre: `docker ps | grep rabbitmq`
2. Verifier la connexion: UI RabbitMQ sur http://localhost:15672
3. Verifier que `rabbitmqHost` = `172.17.0.1` dans la ressource MidPoint

### n8n workflow - emails non envoyes
1. Verifier le credential SMTP Gmail dans n8n
2. Le champ "From" utilise le SMTP credential configure (pas besoin de le remplir)
3. Le champ "To" est rempli automatiquement par les expressions du workflow

### Logs MidPoint
```bash
docker logs midpoint 2>&1 | grep -i "restgateway"
```

---

## Versions

| Composant | Version |
|-----------|---------|
| Connecteur Java | 1.2.0-SNAPSHOT |
| ConnId Framework | 1.5.0.17 |
| Python | 3.x |
| FastAPI | (requirements.txt) |
| n8n | (Docker ou natif) |
| Redis | 6+ |
| Prisma | (requirements.txt) |

---

## GitLab CLI

Ce projet utilise **GitLab** pour le controle de version. Utiliser `glab` (GitLab CLI):

```bash
# Creer une issue
glab issue create --title "Title" --description "Description"

# Creer une merge request
glab mr create --source-branch feature/xxx --target-branch develop

# Lister les issues
glab issue list
```

## Git Workflow

**Toujours suivre GitFlow:**
- `main`: Code pret pour production
- `develop`: Branche d'integration
- `feature/*`, `bugfix/*`, `enhancement/*`: Branches depuis `develop`

**Processus:**
1. Creer branche depuis `develop`
2. Faire les modifications
3. Commit avec message clair
4. Creer merge request vers `develop`
5. NE JAMAIS commit directement sur `develop` ou `main`
