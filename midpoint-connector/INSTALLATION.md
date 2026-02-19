# Installation du Connecteur REST Gateway dans Midpoint

## ✅ Statut du Projet

Le connecteur a été **compilé avec succès** :
- JAR généré : `build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar` (15 KB)
- Manifest ConnId validé
- 4 classes Java compilées
- Tests unitaires créés

## 📦 Installation dans Midpoint Docker

### Étape 1 : Copier le JAR dans le conteneur

```bash
docker cp build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
```

**Note** : Remplacez `midpoint` par le nom de votre conteneur Docker si différent. Vous pouvez vérifier avec :
```bash
docker ps
```

### Étape 2 : Redémarrer Midpoint

```bash
docker restart midpoint
```

Attendez environ 1-2 minutes que Midpoint redémarre complètement.

### Étape 3 : Vérifier le chargement du connecteur

Connectez-vous à Midpoint : `http://localhost:8080/midpoint`

1. Allez dans **Configuration → Repository Objects → Resources**
2. Cliquez sur **New Resource**
3. Dans la liste des connecteurs, cherchez **"REST Gateway Connector"**
4. Si vous le voyez, le connecteur est bien chargé ! ✅

## ⚙️ Configuration du Connecteur dans Midpoint

### Configuration XML Minimale

```xml
<resource>
    <name>REST Gateway Resource</name>

    <connectorRef type="ConnectorType">
        <filter>
            <q:equal>
                <q:path>connectorType</q:path>
                <q:value>lu.lns.connector.restgateway.RestGatewayConnector</q:value>
            </q:equal>
        </filter>
    </connectorRef>

    <connectorConfiguration>
        <icfc:configurationProperties
            xmlns:icfc="http://midpoint.evolveum.com/xml/ns/public/connector/icf-1/connector-schema-3"
            xmlns:icfcrest="http://midpoint.evolveum.com/xml/ns/public/connector/icf-1/bundle/lu.lns.connector-restgateway/lu.lns.connector.restgateway.RestGatewayConnector">

            <icfcrest:gatewayUrl>http://localhost:5000</icfcrest:gatewayUrl>
            <icfcrest:connectionTimeout>30000</icfcrest:connectionTimeout>
            <icfcrest:requestTimeout>60000</icfcrest:requestTimeout>
            <icfcrest:validateSsl>false</icfcrest:validateSsl>
        </icfc:configurationProperties>
    </connectorConfiguration>

    <schemaHandling>
        <objectType>
            <kind>account</kind>
            <intent>default</intent>
            <default>true</default>
            <objectClass>ri:AccountObjectClass</objectClass>

            <attribute>
                <ref>ri:firstName</ref>
                <outbound>
                    <source><path>givenName</path></source>
                </outbound>
            </attribute>

            <attribute>
                <ref>ri:lastName</ref>
                <outbound>
                    <source><path>familyName</path></source>
                </outbound>
            </attribute>

            <attribute>
                <ref>ri:email</ref>
                <outbound>
                    <source><path>emailAddress</path></source>
                </outbound>
            </attribute>
        </objectType>
    </schemaHandling>
</resource>
```

### Configuration via l'Interface Web

1. **New Resource** → Sélectionnez "REST Gateway Connector"
2. Configurez les propriétés :
   - **Gateway URL** : `http://localhost:5000`
   - **Connection Timeout** : `30000` (30 secondes)
   - **Request Timeout** : `60000` (60 secondes)
   - **Validate SSL** : `false` (pour localhost)
3. Cliquez sur **Save**
4. Cliquez sur **Test Connection**

Si le test échoue avec "Connection refused", c'est **normal** - cela signifie que votre gateway REST n'est pas encore démarrée.

## 🔌 Créer une Gateway REST de Test

Pour tester le connecteur, vous devez créer une gateway REST qui écoute sur `localhost:5000`.

### Exemple avec Node.js/Express

Créez un fichier `gateway.js` :

```javascript
const express = require('express');
const app = express();

app.use(express.json());

app.post('/create', (req, res) => {
  console.log('\n=== CREATE ===');
  console.log(JSON.stringify(req.body, null, 2));
  res.json({
    success: true,
    uid: Date.now().toString()
  });
});

app.post('/update', (req, res) => {
  console.log('\n=== UPDATE ===');
  console.log(JSON.stringify(req.body, null, 2));
  res.json({ success: true });
});

app.post('/delete', (req, res) => {
  console.log('\n=== DELETE ===');
  console.log(JSON.stringify(req.body, null, 2));
  res.json({ success: true });
});

app.get('/', (req, res) => {
  res.json({ status: 'Gateway REST OK' });
});

app.listen(5000, () => {
  console.log('Gateway REST écoute sur http://localhost:5000');
});
```

Démarrez-la :
```bash
npm install express
node gateway.js
```

### Exemple avec Python/Flask

Créez un fichier `gateway.py` :

```python
from flask import Flask, request, jsonify
import json
from datetime import datetime

app = Flask(__name__)

@app.route('/create', methods=['POST'])
def create():
    print('\n=== CREATE ===')
    print(json.dumps(request.json, indent=2))
    return jsonify({
        'success': True,
        'uid': str(int(datetime.now().timestamp() * 1000))
    })

@app.route('/update', methods=['POST'])
def update():
    print('\n=== UPDATE ===')
    print(json.dumps(request.json, indent=2))
    return jsonify({'success': True})

@app.route('/delete', methods=['POST'])
def delete():
    print('\n=== DELETE ===')
    print(json.dumps(request.json, indent=2))
    return jsonify({'success': True})

@app.route('/')
def health():
    return jsonify({'status': 'Gateway REST OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

Démarrez-la :
```bash
pip install flask
python gateway.py
```

## 🧪 Tester le Connecteur

### 1. Démarrez votre gateway REST
```bash
node gateway.js
# OU
python gateway.py
```

### 2. Dans Midpoint, testez la connexion
- Ouvrez votre ressource "REST Gateway Resource"
- Cliquez sur **Test Connection**
- Vous devriez voir "Success" ✅

### 3. Créez un utilisateur test dans Midpoint
1. Allez dans **Users** → **New User**
2. Remplissez :
   - Name : `testuser`
   - Given Name : `Test`
   - Family Name : `User`
   - Email : `test@example.com`
3. Dans l'onglet **Projections**, ajoutez la ressource "REST Gateway Resource"
4. Cliquez sur **Save**

### 4. Vérifiez dans la console de votre gateway

Vous devriez voir :
```json
=== CREATE ===
{
  "operation": "CREATE",
  "entityType": "User",
  "timestamp": "2026-01-20T...",
  "attributes": {
    "username": "testuser",
    "firstName": "Test",
    "lastName": "User",
    "email": "test@example.com",
    "enabled": true
  }
}
```

## 🎉 Succès !

Si vous voyez ce JSON dans votre gateway, **le connecteur fonctionne parfaitement** !

Maintenant, à chaque opération dans Midpoint :
- **Créer un utilisateur** → POST `/create`
- **Modifier un utilisateur** → POST `/update`
- **Supprimer un utilisateur** → POST `/delete`

## 📝 Notes Importantes

1. **Localhost uniquement** : Ce connecteur v1.0 est conçu pour localhost. Pour une utilisation en production, ajoutez de l'authentification.

2. **Pas de SearchOp** : Ce connecteur ne supporte pas la recherche depuis la gateway. Midpoint gère les utilisateurs en interne.

3. **Format JSON fixe** : Le format JSON envoyé est défini dans le connecteur. Si vous avez besoin d'un format différent, modifiez `JsonMapper.java`.

4. **Logs Midpoint** : Pour déboguer, consultez les logs :
   ```bash
   docker logs -f midpoint
   ```

## 🐛 Dépannage

### Le connecteur n'apparaît pas dans Midpoint
- Vérifiez que le JAR est bien dans `/opt/midpoint/var/icf-connectors/`
- Redémarrez Midpoint
- Consultez les logs : `docker logs midpoint | grep -i connector`

### Test Connection échoue
- Vérifiez que votre gateway écoute bien sur le port 5000
- Testez manuellement : `curl http://localhost:5000`
- Vérifiez l'URL configurée dans Midpoint

### Les requêtes ne sont pas envoyées
- Vérifiez que l'utilisateur a bien la projection sur la ressource
- Consultez les logs Midpoint pour voir les erreurs
- Vérifiez que la gateway renvoie bien un JSON avec `{"success": true}`

## 📚 Documentation

- [README.md](README.md) - Vue d'ensemble du projet
- [CLAUDE.md](CLAUDE.md) - Architecture pour développeurs
