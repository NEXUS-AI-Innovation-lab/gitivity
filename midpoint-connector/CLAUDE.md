# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **ConnId connector for Midpoint** that acts as a REST gateway bridge. It forwards identity management operations (CREATE, UPDATE, DELETE) to a local REST API endpoint as JSON payloads. The connector supports 4 entity types: Users, Roles, Services, and Organisations.

**Key architectural choice**: This is a simplified connector with a static schema (no dynamic discovery) designed for rapid development. It sends all Midpoint object attributes as JSON to `localhost:5000` endpoints.

## Core Architecture

### ConnId Framework Integration

The connector implements the standard ConnId SPI interfaces:

- **`RestGatewayConnector`** - Main connector class implementing:
  - `PoolableConnector` - Lifecycle management (init/dispose)
  - `CreateOp` - Sends POST to `/create`
  - `UpdateDeltaOp` - Sends POST to `/update`
  - `DeleteOp` - Sends POST to `/delete`
  - `TestOp` - Validates gateway connectivity
  - `SchemaOp` - Returns static schema for 4 entity types

- **`RestGatewayConfiguration`** - Extends `AbstractConfiguration`, annotated properties:
  - `@ConfigurationProperty` marks fields exposed in Midpoint UI
  - Must implement `validate()` to check required properties

- **`RestGatewayClient`** - HTTP communication layer:
  - Wraps Java 11 `HttpClient`
  - Maps HTTP status codes to ConnId exceptions
  - Handles timeouts and connection failures

- **`JsonMapper`** - Attribute conversion utilities:
  - Converts ConnId `Attribute` sets to JSON-serializable Maps
  - Merges `AttributeDelta` updates for UPDATE operations
  - Uses Gson for JSON serialization

### Static Schema Design

Unlike dynamic connectors (e.g., Odoo connector), this uses **hardcoded schemas**:

```java
// SchemaOp.schema() returns SchemaBuilder with 4 ObjectClasses:
ObjectClassInfo userClass = new ObjectClassInfoBuilder()
    .setType("__ACCOUNT__")  // Standard user type
    .addAttributeInfo(AttributeInfoBuilder.build("__NAME__", String.class, REQUIRED))
    .addAttributeInfo(AttributeInfoBuilder.build("firstName", String.class))
    // ... etc
    .build();
```

**Why static**: No need to query gateway for schema metadata. Schemas are defined in code based on Midpoint object types.

### HTTP Error Mapping

Critical pattern for ConnId compliance:

```java
// HTTP Status → ConnId Exception
200-299 → Success
400     → InvalidAttributeValueException
401/403 → PermissionDeniedException
404     → UnknownUidException
409     → AlreadyExistsException
500-599 → ConnectorException
Timeout → OperationTimeoutException
Connection refused → ConnectionFailedException
```

**Why important**: Midpoint relies on specific exceptions to handle provisioning failures correctly (e.g., skip vs retry vs fail).

### JSON Payload Format

All operations send this structure to the gateway:

```json
{
  "operation": "CREATE|UPDATE|DELETE",
  "entityType": "User|Role|Service|Organisation",
  "uid": "12345",  // For UPDATE/DELETE only
  "timestamp": "2026-01-20T10:30:00Z",
  "attributes": {
    "username": "jdoe",
    "email": "jdoe@example.com",
    // ... all Midpoint attributes as key-value pairs
  }
}
```

**Key design**: Gateway receives complete object state, not deltas. For UPDATE, the connector merges deltas into full attribute map before sending.

## Build Commands

### Development
```bash
# Build connector JAR (without tests)
.\gradlew clean jar

# JAR output location
# build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar

# Run tests only
.\gradlew test

# Clean build directory
.\gradlew clean
```

### Testing
```bash
# Run unit tests with output
.\gradlew test --info

# Run specific test class
.\gradlew test --tests RestGatewayConnectorTest

# Run specific test method
.\gradlew test --tests RestGatewayConnectorTest.testCreateUser
```

### Deployment
```bash
# Build and copy to Midpoint
.\gradlew jar
copy build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar <midpoint-home>\icf-connectors\
```

## Connector Manifest Requirements

The JAR manifest **must** include ConnId metadata for Midpoint discovery:

```gradle
jar {
    manifest {
        attributes(
            'ConnectorBundle-Version': version,
            'ConnectorBundle-Name': 'lu.lns.connector.restgateway',
            'ConnectorBundle-FrameworkVersion': '1.5.0.17'
        )
    }
}
```

**Critical**: Without these manifest entries, Midpoint won't detect the connector.

## Configuration Properties Pattern

When adding configuration properties:

```java
@ConfigurationProperty(
    order = 1,
    displayMessageKey = "gatewayUrl.display",
    helpMessageKey = "gatewayUrl.help",
    required = true
)
public String getGatewayUrl() {
    return gatewayUrl;
}
```

Corresponding `Messages.properties`:
```properties
gatewayUrl.display=Gateway URL
gatewayUrl.help=REST API endpoint (e.g., http://localhost:5000)
```

**Pattern**: Getters are annotated (not fields). Message keys reference property name + `.display`/`.help`.

## ConnId Attribute Conventions

Special attribute names have framework meaning:

- `__UID__` - Unique identifier (immutable, required for UPDATE/DELETE)
- `__NAME__` - Primary identifier (mutable, required for CREATE)
- `__PASSWORD__` - Stored as GuardedString (encrypted)
- `__ENABLE__` - Account enabled/disabled flag

**Multi-valued attributes**: Use `String[]` or `List<String>` type. Example: `roles` attribute.

## Testing Against Midpoint

### Mock Gateway Setup
For testing, create a simple HTTP server:

```javascript
// Example with Express.js
app.post('/create', (req, res) => {
  console.log('CREATE:', JSON.stringify(req.body, null, 2));
  res.json({ success: true, uid: Date.now().toString() });
});
```

### Midpoint Resource Configuration
Minimal XML for testing the connector:

```xml
<connectorConfiguration>
    <configurationProperties>
        <gatewayUrl>http://localhost:5000</gatewayUrl>
        <connectionTimeout>30000</connectionTimeout>
        <requestTimeout>60000</requestTimeout>
    </configurationProperties>
</connectorConfiguration>
```

## Common Implementation Pitfalls

1. **UID vs NAME confusion**:
   - CREATE takes NAME, returns UID
   - UPDATE/DELETE require UID, not NAME

2. **AttributeDelta merging**:
   - `AttributeDelta.getValuesToAdd()` / `getValuesToRemove()` for multi-valued
   - Must compute final state, not send deltas to gateway

3. **Exception handling**:
   - Always wrap checked exceptions in ConnId exceptions
   - Never let raw IOExceptions escape to framework

4. **GuardedString handling**:
   - Use `SecurityUtil.decrypt()` to access password values
   - Never log GuardedString contents

5. **OperationOptions**:
   - Contains Midpoint options like `__ALLOW_PARTIAL_ATTRIBUTE_VALUES__`
   - Usually can ignore for simple connectors

## Gradle Dependencies

Core dependencies (versions matter for ConnId compatibility):

```gradle
dependencies {
    // ConnId framework - MUST match Midpoint version
    implementation 'net.tirasa.connid:connector-framework:1.5.0.17'

    // Polygon utilities - exclude to avoid version conflicts
    implementation('com.evolveum.polygon:connector-common:3.0.1') {
        exclude group: 'net.tirasa.connid'
    }

    // JSON processing
    implementation 'com.google.code.gson:gson:2.10.1'

    // Testing
    testImplementation 'junit:junit:4.13.2'
}
```

**Version compatibility**: ConnId 1.5.0.17 is for Midpoint 4.x. Check Midpoint version before changing.

## Simplifications in This Connector

This connector intentionally omits:

- **SearchOp**: No filtering/pagination (Midpoint can work without it)
- **SyncOp**: No incremental sync (full reconciliation only)
- **Schema discovery**: Schemas hardcoded, not queried from gateway
- **Relationship handling**: No Many2One/Many2Many references
- **Caching**: No metadata or object caching
- **Retry logic**: Fails immediately on errors
- **Authentication**: Assumes localhost, no auth headers

**Rationale**: MVP for rapid deployment. These can be added incrementally if needed.

## Entity Types Supported

| Midpoint Type | ObjectClass Name | Key Attributes |
|---------------|------------------|----------------|
| User | `__ACCOUNT__` | username, email, firstName, lastName, enabled, roles |
| Role | `Role` | name, description |
| Service | `Service` | name, description, url, enabled |
| Organisation | `Organisation` | name, parentOrgRef, displayName |

**Note**: User uses special `__ACCOUNT__` type (ConnId convention). Others use custom ObjectClass names.

## Debugging Tips

### Enable HTTP logging
Add to `RestGatewayClient`:
```java
HttpClient client = HttpClient.newBuilder()
    .version(HttpClient.Version.HTTP_1_1)
    // Uncomment for debugging:
    // .executor(Executors.newSingleThreadExecutor())
    .build();
```

### Test connector outside Midpoint
Create standalone test:
```java
RestGatewayConfiguration config = new RestGatewayConfiguration();
config.setGatewayUrl("http://localhost:5000");
RestGatewayConnector connector = new RestGatewayConnector();
connector.init(config);
connector.test(); // Should not throw if gateway is up
```

### Midpoint logs location
Check `<midpoint-home>/var/log/midpoint.log` for connector errors.
