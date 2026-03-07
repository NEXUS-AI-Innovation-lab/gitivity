# Gateway IAM

API de provisionnement IAM pour MidPoint. Reçoit des messages via RabbitMQ, coordonne la validation avec n8n, provisionne les utilisateurs vers des services cibles (MySQL, PostgreSQL, Odoo), et gère un système complet de retry et audit.

## Architecture

```
+----------+   +-----------------+   +-----------+   +------------------+
|          |   |  Java Connector |   |  RabbitMQ |   | Python Consumer  |
| MIDPOINT |-->|  (ConnId)       |-->|   Queue   |-->| (RabbitMQ)       |
|   (IAM)  |   |  RestGateway    |   |           |   |                  |
+----------+   +--------+--------+   +-----------+   +--------+---------+
     ^                  |                                      |
     |                  | HTTP (entitlements)                  v
     |                  v                             +--------------+
     |           +-------------+                      | FastAPI      |
     |           | Gateway HTTP|                      | (port 8000)  |
     |           | Flask :5100 |                      | Orchestrator |
     |           | Entitlements|                      +------+-------+
     |           +-------------+                             |
     |                                                       |
     |   +---------------------------------------------------+
     |   |         |              |             |
     |   v         v              v             v
     | +------+ +------+ +-----------+ +-------+
     | | LDAP | | Odoo | | PostgreSQL| | MySQL |
     | +------+ +------+ +-----------+ +-------+
     |
     |   +------------------+     +--------+     +--------+
     +---| MidPoint REST API|     | Redis  |     |  n8n   |
         | (unassign role)  |     |        |     | :5678  |
         +------------------+     +--------+     +--------+
```

## Stack Technique

- **Python 3.11+**
- **FastAPI** - API REST
- **Prisma** - ORM avec PostgreSQL
- **aio_pika** - Consumer RabbitMQ async
- **httpx** - Client HTTP async pour n8n
- **Redis** - Store clé/valeur en mémoire pour l'état temporaire

## Prérequis

- Python 3.11+
- PostgreSQL 15+
- RabbitMQ 3.12+
- Redis 6+
- n8n (pour validation et notifications)

## Installation

### 1. Environnement virtuel

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env avec votre configuration
```

### 3. Base de données

```bash
# Générer le client Prisma
prisma generate

# Appliquer les migrations
prisma migrate dev
```

### 4. Docker (optionnel)

```bash
cd docker
docker-compose up -d
```

Services démarrés par défaut :

| Service    | Port(s)           | Description                     |
| ---------- | ----------------- | ------------------------------- |
| PostgreSQL | `5432`            | Base de données                 |
| RabbitMQ   | `5672`, `15672`   | Broker + Management UI          |
| Redis      | `6379`            | Store clé/valeur (approbations) |

**RabbitMQ Management UI** : <http://localhost:15672> (guest/guest)

#### Kafka (optionnel)

Si vous avez besoin de Kafka au lieu de RabbitMQ :

```bash
docker-compose --profile kafka up -d
```

Cela démarre également Zookeeper et Kafka UI (port `8080`).

## Lancement

### API Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Message Consumer

```bash
python scripts/start_consumer.py
```

## Interface de Gestion des Connecteurs

L'application dispose d'une **interface web moderne** permettant de visualiser et configurer les connecteurs en temps réel.

### Fonctionnalités

| Fonctionnalité | Description |
| -------------- | ----------- |
| **Connecteurs Gateway** | Affiche l'état des connecteurs locaux (MySQL, PostgreSQL, Odoo, LDAP) |
| **Connecteurs MidPoint** | Affiche les ressources (connecteurs Java) configurées dans MidPoint |
| **Indicateurs d'état** | Pastilles colorées indiquant l'état de chaque connecteur |
| **Configuration** | Icône de paramètres pour voir/modifier la configuration |
| **Test de connexion** | Bouton pour tester la connectivité en un clic |

### Accès à l'interface

```text
http://localhost:8000/dashboard/connectors
```

### Types de connecteurs

**Connecteurs Gateway** (gérés par cette application) :

- MySQL, PostgreSQL, Odoo, LDAP
- Configuration modifiable via l'interface (runtime uniquement)
- Test de connexion instantané

**Connecteurs MidPoint** (ressources Java dans MidPoint) :

- Affichage en lecture seule
- Test de connexion via l'API MidPoint
- Nécessite que MidPoint soit accessible (voir configuration)

### Configuration MidPoint

Pour afficher les connecteurs MidPoint, configurez les variables d'environnement :

```bash
MIDPOINT_URL="http://localhost:8080/midpoint"
MIDPOINT_USERNAME="administrator"
MIDPOINT_PASSWORD="5ecr3t"
```

## API Endpoints

| Endpoint                          | Méthode | Description                        |
| --------------------------------- | ------- | ---------------------------------- |
| `/health`                         | GET     | Health check                       |
| `/metrics`                        | GET     | Métriques (Prometheus-compatible)  |
| `/api/v1/provisioning`            | GET     | Liste des opérations (avec filtres)|
| `/api/v1/provisioning/{id}`       | GET     | Détails d'une opération            |
| `/api/v1/provisioning/{id}/retry` | POST    | Retry manuel                       |
| `/api/v1/audit/{operation_id}`    | GET     | Audit trail                        |
| `/api/v1/connectors`              | GET     | Liste des connecteurs et leur état |
| `/api/v1/connectors/{name}`       | GET     | Détails d'un connecteur            |
| `/api/v1/connectors/{name}`       | PUT     | Modifier la configuration          |
| `/api/v1/connectors/{name}/test`  | POST    | Tester la connexion                |

Documentation interactive : `/docs` (Swagger) ou `/redoc`

## Redis

Redis est utilisé comme store clé/valeur en mémoire pour trois usages distincts.

### Clés stockées

| Clé | TTL | Contenu | Usage |
| --- | --- | ------- | ----- |
| `approval:pending:{operation_id}` | 2 heures | Message MidPoint complet + statut | Survie au redémarrage pendant l'approbation |
| `user_state:{service}:{username}` | 90 jours | Attributs user au dernier provisioning réussi | Calcul du diff pour les UPDATE |
| `rejected_create:{service}:{username}` | 30 jours | Raison et date du rejet | Détection des UPDATE/DELETE après un CREATE rejeté |

### Détail des clés

**`approval:pending:{operation_id}`**

Stocké dès qu'une opération part en approbation. Permet de reconstruire le message MidPoint complet si l'API redémarre pendant l'attente de l'approbateur.

```json
{
  "operation_id": "uuid-123",
  "target_service": "LDAP",
  "operation_type": "CREATE_USER",
  "user_data": { "username": "ahmed", "email": "...", "roles": ["ldap"] },
  "midpoint_message": { ... },
  "requested_at": "2026-02-20T10:00:00Z",
  "timeout_at": "2026-02-20T12:00:00Z",
  "status": "pending"
}
```

**`user_state:{service}:{username}`**

Stocké après chaque provisioning réussi (CREATE ou UPDATE). Utilisé pour calculer le diff lors du prochain UPDATE et afficher les modifications dans l'email n8n.

```json
{
  "username": "ahmed",
  "first_name": "Ahmed",
  "last_name": "Doe",
  "email": "ahmed@company.com",
  "password": null,
  "roles": ["Developer"],
  "attributes": { ... }
}
```

**`rejected_create:{service}:{username}`**

Stocké quand un CREATE est rejeté par un approbateur. Permet à l'orchestrateur de :
- Ignorer les DELETE suivants (l'utilisateur n'a jamais existé sur le service)
- Convertir les UPDATE suivants en CREATE (re-tentative de provisioning)

```json
{
  "operation_id": "uuid-456",
  "rejected_at": "2026-02-20T11:00:00Z",
  "reason": "Rejected by Admin (Level 1)"
}
```

### Configuration Redis

```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=          # optionnel
REDIS_DB=0
```

## Configuration

Variables d'environnement principales :

| Variable                       | Description              | Défaut                     |
| ------------------------------ | ------------------------ | -------------------------- |
| `DATABASE_URL`                 | URL PostgreSQL           | -                          |
| `BROKER_TYPE`                  | `rabbitmq` ou `kafka`    | `rabbitmq`                 |
| `RABBITMQ_HOST`                | Hôte RabbitMQ            | `localhost`                |
| `RABBITMQ_PORT`                | Port RabbitMQ            | `5672`                     |
| `RABBITMQ_QUEUE`               | Queue de messages        | `gateway-iam-provisioning` |
| `N8N_VALIDATION_WEBHOOK_URL`   | Webhook validation n8n   | -                          |
| `N8N_NOTIFICATION_WEBHOOK_URL` | Webhook notification n8n | -                          |
| `RETRY_MAX_ATTEMPTS`           | Tentatives max avant DLQ | `3`                        |
| `RETRY_BACKOFF_MULTIPLIER`     | Multiplicateur backoff   | `2.0`                      |

Voir `.env.example` pour la liste complète.

## Approbation Multi-niveaux

Chaque opération de provisioning passe par une chaîne d'approbateurs définie dans `data/approvers.json`, triée par niveau croissant.

### Flow d'approbation

```
Opération reçue (CREATE/UPDATE/DELETE)
  → Statut APPROVAL_PENDING
  → Message stocké dans Redis (TTL 2h)
  → Envoi à n8n avec liste des approbateurs
  → n8n envoie email niveau par niveau
  → Approbateur clique Approuver/Rejeter
  → Callback POST /api/v1/provisioning/{id}/approve-callback
  → Si approuvé → provisioning vers le service cible
  → Si rejeté   → retrait du rôle MidPoint + marqueur Redis 30j
```

### Configuration des approbateurs

```json
{
  "approvers": [
    {"id": "1", "name": "Admin", "email": "admin@company.com", "level": 1},
    {"id": "2", "name": "Directeur", "email": "dir@company.com", "level": 2}
  ]
}
```

Dashboard de gestion : `http://localhost:8000/dashboard/approvers`

## Workflow n8n

Le workflow `n8n/workflows/approval-workflow.json` gère :

- Envoi d'emails HTML par approbateur avec boutons Approuver/Rejeter
- Boucle niveau par niveau (`SplitInBatches`, batchSize=1)
- Attente via webhook (`Wait` node, timeout 2h)
- Analyse automatique par règles métier (stagiaire + admin, trop de rôles, etc.)
- Affichage des modifications avant/après pour les UPDATE (fond rouge/vert)
- Callback vers FastAPI avec la décision finale

## Connecteur Java (ConnId)

Le connecteur `midpoint-connector/` est un connecteur ConnId pour MidPoint.

### Interfaces implémentées

| Interface | Méthode | Description |
| --------- | ------- | ----------- |
| `CreateOp` | `create()` | Reçoit les nouvelles identités |
| `UpdateDeltaOp` | `updateDelta()` | Reçoit uniquement les champs modifiés |
| `DeleteOp` | `delete()` | Reçoit les suppressions |
| `SchemaOp` | `schema()` | Déclare les ObjectClass à MidPoint |
| `SearchOp` | `executeQuery()` | Retourne les entitlements (groupes LDAP, profils SQL) |

### Build

```bash
cd midpoint-connector
./gradlew clean jar
# JAR : build/libs/connector-restgateway-1.2.0-SNAPSHOT.jar
```

### Déploiement dans MidPoint

```bash
docker cp build/libs/connector-restgateway-*.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

## Entitlements

Les entitlements permettent à MidPoint de découvrir les droits disponibles sur les services cibles.

| Type | Endpoint Gateway HTTP | Source |
| ---- | --------------------- | ------ |
| Groupes LDAP | `GET /entitlements/ldap-groups` | Serveur LDAP |
| Profils PostgreSQL | `GET /entitlements/postgresql-profiles` | `config.json` |
| Profils MySQL | `GET /entitlements/mysql-profiles` | `config.json` |

La Gateway HTTP Flask tourne sur le port `5100` et est appelée par le connecteur Java lors des opérations `executeQuery()` de MidPoint.

## Format des Messages

```json
{
  "request_id": "mp-req-001",
  "operation_type": "CREATE_USER",
  "target_service": "MYSQL",
  "user_data": {
    "username": "john.doe",
    "email": "john@example.com",
    "password": "SecurePass123!",
    "roles": ["read", "write"]
  },
  "metadata": {}
}
```

**operation_type** : `CREATE_USER`, `UPDATE_USER`, `DELETE_USER`, `CREATE_ROLE`, etc.

**target_service** : `MYSQL`, `POSTGRESQL`, `ODOO`

## Tests

```bash
# Tous les tests
pytest

# Tests unitaires uniquement
pytest tests/unit/

# Avec couverture
pytest --cov=app --cov-report=html
```

## Développement

### GitFlow

- `main` : Production
- `develop` : Intégration
- `feature/*` : Nouvelles fonctionnalités
- `bugfix/*` : Corrections

### Ajouter un nouveau connecteur

1. Créer `app/core/connectors/<service>_connector.py`
2. Hériter de `ProvisioningConnector`
3. Implémenter : `provision_user()`, `update_user()`, `delete_user()`, `health_check()`
4. Enregistrer dans `ConnectorFactory._connectors`
5. Ajouter `TargetService` enum si nécessaire

## License

MIT
