# REST Gateway Connector for Midpoint

Un connecteur ConnId pour Midpoint qui transmet les opérations d'identité (CREATE, UPDATE, DELETE) vers une gateway REST locale sous forme de payloads JSON.

## Description

Ce connecteur permet à Midpoint d'envoyer automatiquement des notifications JSON à une API REST locale lors des opérations suivantes :

- **CREATE** : Création d'un utilisateur, rôle, service ou organisation
- **UPDATE** : Modification d'un objet existant
- **DELETE** : Suppression d'un objet

## Types d'Entités Supportés

- **User** (utilisateurs)
- **Role** (rôles)
- **Service** (services)
- **Organisation** (organisations)

## Configuration Requise

- Java 11 ou supérieur
- Midpoint 4.x
- Une gateway REST écoutant sur le port configuré (par défaut: `http://localhost:5000`)

## Build

### Compiler le connecteur

```bash
.\gradlew clean jar
```

Le JAR sera généré dans `build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar`

### Exécuter les tests

```bash
.\gradlew test
```

## Installation dans Midpoint

### 1. Copier le JAR dans Midpoint Docker

```bash
docker cp build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
```

### 2. Redémarrer Midpoint

```bash
docker restart midpoint
```

### 3. Créer une Ressource dans Midpoint

1. Connectez-vous à Midpoint : `http://localhost:8080/midpoint`
2. Allez dans **Configuration → Repository Objects → Resources**
3. Cliquez sur **New Resource**
4. Sélectionnez **REST Gateway Connector** dans la liste
5. Configurez les paramètres :
   - **Gateway URL** : `http://localhost:5000` (ou votre URL)
   - **Connection Timeout** : `30000` ms (optionnel)
   - **Request Timeout** : `60000` ms (optionnel)
   - **Validate SSL** : `false` pour localhost (optionnel)

### 4. Tester la Connexion

Cliquez sur **Test Connection** dans Midpoint pour vérifier que la gateway est accessible.

## Format JSON Envoyé

### CREATE

```json
{
  "operation": "CREATE",
  "entityType": "User",
  "timestamp": "2026-01-20T10:30:00Z",
  "attributes": {
    "username": "jdoe",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "enabled": true,
    "roles": ["ROLE_USER"]
  }
}
```

### UPDATE

```json
{
  "operation": "UPDATE",
  "entityType": "User",
  "uid": "12345",
  "timestamp": "2026-01-20T10:35:00Z",
  "attributes": {
    "email": "john.new@example.com",
    "enabled": false
  }
}
```

### DELETE

```json
{
  "operation": "DELETE",
  "entityType": "User",
  "uid": "12345",
  "timestamp": "2026-01-20T10:40:00Z"
}
```

## Endpoints Gateway

La gateway REST doit exposer les endpoints suivants :

- `POST /create` - Reçoit les objets créés
- `POST /update` - Reçoit les modifications
- `POST /delete` - Reçoit les suppressions
- `GET /` - Test de connexion (optionnel)

## Exemple de Gateway Simple

Voici un exemple de gateway Node.js/Express pour tester :

```javascript
const express = require('express');
const app = express();

app.use(express.json());

app.post('/create', (req, res) => {
  console.log('CREATE:', JSON.stringify(req.body, null, 2));
  res.json({ success: true, uid: Date.now().toString() });
});

app.post('/update', (req, res) => {
  console.log('UPDATE:', JSON.stringify(req.body, null, 2));
  res.json({ success: true });
});

app.post('/delete', (req, res) => {
  console.log('DELETE:', JSON.stringify(req.body, null, 2));
  res.json({ success: true });
});

app.get('/', (req, res) => {
  res.json({ status: 'ok' });
});

app.listen(5000, () => {
  console.log('Gateway listening on port 5000');
});
```

## Gestion des Erreurs

Le connecteur mappe les codes HTTP vers les exceptions ConnId :

- **200-299** : Succès
- **400** : Erreur de validation (InvalidAttributeValueException)
- **401/403** : Erreur d'authentification (PermissionDeniedException)
- **404** : Objet non trouvé (UnknownUidException)
- **409** : Objet déjà existant (AlreadyExistsException)
- **500-599** : Erreur serveur (ConnectorException)
- **Timeout** : Timeout (OperationTimeoutException)
- **Connection refused** : Connexion impossible (ConnectionFailedException)

## Attributs par Type d'Entité

### User
- `username` (requis)
- `firstName`
- `lastName`
- `email`
- `enabled`
- `roles` (multi-valued)

### Role
- `name` (requis)
- `description`
- `roleType`

### Service
- `name` (requis)
- `description`
- `url`
- `enabled`
- `serviceType`

### Organisation
- `name` (requis)
- `parentOrgRef`
- `displayName`
- `locality`
- `costCenter`

## Limitations (v1.0)

Cette version MVP ne supporte pas :

- Opérations de recherche complexes (SearchOp)
- Synchronisation temps réel (SyncOp)
- Gestion des relations entre objets
- Authentification vers la gateway
- Retry automatique

## Développement

Voir [CLAUDE.md](CLAUDE.md) pour les détails d'architecture et les commandes de développement.

## Licence

Copyright (c) 2026 LNS
