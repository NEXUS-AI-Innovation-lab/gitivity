# Connecteur Midpoint avec RabbitMQ

## 🎯 Architecture Optimale

```
Midpoint → Connecteur Java → RabbitMQ → Consumer Python → Traitement
```

**Fini la gateway HTTP intermédiaire !** Le connecteur publie **directement** dans RabbitMQ.

## 📦 Installation Rapide

### 1. Démarrer RabbitMQ

```bash
cd SAE5-All
docker-compose -f docker-compose-rabbitmq.yml up -d
```

**Vérifier que RabbitMQ est démarré :**
```bash
docker ps | grep rabbitmq
```

### 2. Installer les dépendances Python

```bash
pip install pika
```

### 3. Compiler le nouveau connecteur

```bash
cd SAE5-All/idm-connector-rest-gateway
./gradlew clean jar --no-daemon
```

Le JAR sera généré dans `build/libs/connector-restgateway-1.0.0-SNAPSHOT.jar`

### 4. Installer le connecteur dans Midpoint

```bash
docker cp build/libs/connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

Attendez 1-2 minutes que Midpoint redémarre.

## ⚙️ Configuration Midpoint

### Dans la ressource Gateway, configurez :

**Propriétés RabbitMQ :**
- **Use RabbitMQ** : `true` ✅
- **RabbitMQ Host** : `172.17.0.1` (IP du host Docker)
- **RabbitMQ Port** : `5672`
- **RabbitMQ Username** : `admin`
- **RabbitMQ Password** : `admin123`
- **RabbitMQ Queue** : `midpoint-operations`

**Les anciennes propriétés HTTP sont ignorées quand useRabbitmq=true.**

### Exemple de configuration XML :

```xml
<connectorConfiguration xmlns:icfc="http://midpoint.evolveum.com/xml/ns/public/connector/icf-1/connector-schema-3">
    <icfc:configurationProperties xmlns:gen="http://midpoint.evolveum.com/xml/ns/public/connector/icf-1/bundle/lu.lns.connector.restgateway/lu.lns.connector.restgateway.RestGatewayConnector">
        <gen:useRabbitmq>true</gen:useRabbitmq>
        <gen:rabbitmqHost>172.17.0.1</gen:rabbitmqHost>
        <gen:rabbitmqPort>5672</gen:rabbitmqPort>
        <gen:rabbitmqUsername>admin</gen:rabbitmqUsername>
        <gen:rabbitmqPassword>admin123</gen:rabbitmqPassword>
        <gen:rabbitmqQueue>midpoint-operations</gen:rabbitmqQueue>
    </icfc:configurationProperties>
</connectorConfiguration>
```

## 🚀 Utilisation

### Démarrer le consumer RabbitMQ

```bash
cd SAE5-All
python3 gateway-rabbitmq.py
```

Vous verrez :
```
============================================================
Gateway RabbitMQ - Consommateur Midpoint
============================================================
Connexion à RabbitMQ sur localhost:5672
Queue : midpoint-operations
============================================================

✅ Connecté à RabbitMQ
⏳ En attente de messages sur 'midpoint-operations'...
```

### Tester dans Midpoint

1. **Créez un utilisateur** avec la ressource Gateway
2. Le consumer affichera :

```
============================================================
CREATE REÇU VIA RABBITMQ
============================================================
{
  "operation": "CREATE",
  "entityType": "User",
  "timestamp": "2026-01-20T20:15:00Z",
  "attributes": {
    "username": "jdoe",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "enabled": true,
    "roles": ["ROLE_USER"]
  }
}
============================================================
```

3. **Modifiez l'utilisateur** → Vous verrez UPDATE
4. **Supprimez l'utilisateur** → Vous verrez DELETE

## 🔍 Monitoring RabbitMQ

### Interface Web

Ouvrez : http://localhost:15672

- **Username** : `admin`
- **Password** : `admin123`

Dans l'interface :
- **Queues** → Voir la queue `midpoint-operations`
- **Connections** → Voir les connexions actives
- **Channels** → Voir les canaux ouverts

### Via CLI

```bash
# Voir les queues
docker exec rabbitmq-midpoint rabbitmqctl list_queues

# Voir les messages en attente
docker exec rabbitmq-midpoint rabbitmqctl list_queues name messages

# Voir les consumers
docker exec rabbitmq-midpoint rabbitmqctl list_consumers
```

## 📊 Flux Complet

1. **Utilisateur modifié dans Midpoint**
2. **Connecteur Java** génère le JSON et publie dans RabbitMQ
3. **RabbitMQ** stocke le message dans la queue `midpoint-operations`
4. **Consumer Python** (gateway-rabbitmq.py) consomme et traite
5. **Votre logique métier** (base de données, API, etc.)

## ✅ Avantages de cette Architecture

| Avantage | Description |
|----------|-------------|
| **Découplage total** | Connecteur et consumer totalement indépendants |
| **Fiabilité** | Messages persistés même si consumer est arrêté |
| **Scalabilité** | Plusieurs consumers peuvent traiter en parallèle |
| **Ordre garanti** | Messages traités dans l'ordre FIFO |
| **Monitoring** | Interface RabbitMQ pour surveiller |
| **Pas de perte** | Si consumer crash, messages restent en queue |
| **Performance** | Async, pas de blocage du connecteur |

## 🛠️ Développement de votre Consumer

Le fichier `gateway-rabbitmq.py` est un exemple simple. Vous pouvez le remplacer par :

### Exemple : Insérer dans une base de données

```python
def callback(ch, method, properties, body):
    data = json.loads(body)
    operation = data.get('operation')

    if operation == 'CREATE':
        # Insérer dans votre base
        user_id = db.insert_user(data['attributes'])
        print(f"User {user_id} créé en base")

    elif operation == 'UPDATE':
        # Mettre à jour
        db.update_user(data['uid'], data['attributes'])
        print(f"User {data['uid']} mis à jour")

    elif operation == 'DELETE':
        # Supprimer
        db.delete_user(data['uid'])
        print(f"User {data['uid']} supprimé")

    ch.basic_ack(delivery_tag=method.delivery_tag)
```

### Exemple : Envoyer à une autre API

```python
def callback(ch, method, properties, body):
    data = json.loads(body)

    # Forward à votre API métier
    response = requests.post(
        'https://votre-api.com/users',
        json=data,
        headers={'Authorization': 'Bearer YOUR_TOKEN'}
    )

    if response.ok:
        print(f"✅ Envoyé à l'API : {data['operation']}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
    else:
        print(f"❌ Erreur API : {response.status_code}")
        # Rejeter le message pour retry
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
```

## 🔧 Troubleshooting

### Le connecteur ne se connecte pas à RabbitMQ

**Erreur** : `Failed to connect to RabbitMQ: Connection refused`

**Solution** :
1. Vérifiez que RabbitMQ est démarré : `docker ps | grep rabbitmq`
2. Vérifiez l'IP du host : utilisez `172.17.0.1` ou `host.docker.internal`
3. Testez depuis Midpoint : `docker exec midpoint telnet 172.17.0.1 5672`

### Les messages ne s'affichent pas dans le consumer

**Vérifiez** :
1. Le consumer est bien lancé : `python3 gateway-rabbitmq.py`
2. La queue existe : Interface web → Queues → `midpoint-operations`
3. Les messages sont bien publiés : Interface web → voir le compteur
4. Pas d'erreur dans les logs Midpoint : `docker logs midpoint | grep -i rabbit`

### Test Connection échoue dans Midpoint

**Vérifiez la configuration** :
- `useRabbitmq` = `true`
- `rabbitmqHost` = `172.17.0.1` (pas `localhost`)
- `rabbitmqPort` = `5672`
- `rabbitmqUsername` = `admin`
- `rabbitmqPassword` = `admin123`

## 📝 Logs

### Logs Midpoint
```bash
docker logs -f midpoint | grep -i "REST Gateway\|RabbitMQ"
```

### Logs RabbitMQ
```bash
docker logs -f rabbitmq-midpoint
```

## 🧹 Nettoyage

### Arrêter tout

```bash
# Arrêter le consumer Python (CTRL+C dans le terminal)

# Arrêter RabbitMQ
docker-compose -f docker-compose-rabbitmq.yml down

# Supprimer les données RabbitMQ
docker-compose -f docker-compose-rabbitmq.yml down -v
```

## 🎓 Prochaines Étapes

1. ✅ Démarrez RabbitMQ
2. ✅ Compilez et installez le nouveau connecteur
3. ✅ Configurez Midpoint avec `useRabbitmq=true`
4. ✅ Lancez `gateway-rabbitmq.py`
5. ✅ Testez en créant/modifiant des utilisateurs
6. 🚀 Remplacez `gateway-rabbitmq.py` par votre logique métier !

**Félicitations !** Vous avez une architecture d'intégration asynchrone professionnelle avec Midpoint ! 🎉
