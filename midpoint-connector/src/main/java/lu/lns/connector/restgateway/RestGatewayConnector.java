package lu.lns.connector.restgateway;

import org.identityconnectors.framework.common.objects.*;
import org.identityconnectors.framework.common.objects.filter.AbstractFilterTranslator;
import org.identityconnectors.framework.common.objects.filter.EqualsFilter;
import org.identityconnectors.framework.common.objects.filter.FilterTranslator;
import org.identityconnectors.framework.spi.Configuration;
import org.identityconnectors.framework.spi.Connector;
import org.identityconnectors.framework.spi.ConnectorClass;
import org.identityconnectors.framework.spi.PoolableConnector;
import org.identityconnectors.framework.spi.operations.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeoutException;

@ConnectorClass(
    displayNameKey = "connector.restgateway.display",
    configurationClass = RestGatewayConfiguration.class
)
public class RestGatewayConnector implements PoolableConnector, CreateOp, UpdateDeltaOp, DeleteOp, TestOp, SchemaOp, SearchOp<String> {

    private static final Logger LOG = LoggerFactory.getLogger(RestGatewayConnector.class);

    private RestGatewayConfiguration configuration;
    private RestGatewayClient client;
    private RabbitMQClient rabbitmqClient;

    @Override
    public Configuration getConfiguration() {
        return configuration;
    }

    @Override
    public void init(Configuration configuration) {
        LOG.info("Initializing REST Gateway Connector");
        this.configuration = (RestGatewayConfiguration) configuration;
        this.configuration.validate();

        if (Boolean.TRUE.equals(this.configuration.getUseRabbitmq())) {
            LOG.info("Using RabbitMQ mode");
            this.rabbitmqClient = new RabbitMQClient(this.configuration);
            try {
                this.rabbitmqClient.init();
            } catch (IOException | TimeoutException e) {
                throw new RuntimeException("Failed to initialize RabbitMQ: " + e.getMessage(), e);
            }
        } else {
            LOG.info("Using HTTP mode");
            this.client = new RestGatewayClient(this.configuration);
        }
    }

    @Override
    public void dispose() {
        LOG.info("Disposing REST Gateway Connector");
        if (rabbitmqClient != null) {
            rabbitmqClient.close();
        }
        this.client = null;
        this.configuration = null;
    }

    @Override
    public void checkAlive() {
        LOG.debug("Check alive called");
        if (Boolean.TRUE.equals(configuration.getUseRabbitmq())) {
            if (rabbitmqClient == null) {
                throw new IllegalStateException("RabbitMQ client not initialized");
            }
        } else {
            if (client == null) {
                throw new IllegalStateException("HTTP client not initialized");
            }
        }
    }

    @Override
    public void test() {
        LOG.info("Testing connection");
        if (Boolean.TRUE.equals(configuration.getUseRabbitmq())) {
            try {
                rabbitmqClient.testConnection();
            } catch (IOException | TimeoutException e) {
                throw new RuntimeException("RabbitMQ test failed: " + e.getMessage(), e);
            }
        } else {
            client.testConnection();
        }
        LOG.info("Connection test successful");
    }

    @Override
    public Schema schema() {
        LOG.debug("Building schema");

        SchemaBuilder schemaBuilder = new SchemaBuilder(RestGatewayConnector.class);

        // User (Account) ObjectClass - TOUS LES ATTRIBUTS
        ObjectClassInfoBuilder userClassBuilder = new ObjectClassInfoBuilder();
        userClassBuilder.setType(ObjectClass.ACCOUNT_NAME);
        userClassBuilder.addAttributeInfo(Name.INFO);

        // Attributs de base
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("fullName", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("firstName", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("lastName", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("additionalName", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("nickname", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("honorificPrefix", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("honorificSuffix", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("title", String.class));

        // Langue et localisation
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("preferredLanguage", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("locale", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("timezone", String.class));

        // Contact
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("email", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("telephoneNumber", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("personalNumber", String.class));

        // Organisation
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("costCenter", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("organization", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("organizationalUnit", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("locality", String.class));

        // Booléens
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("enabled", Boolean.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("indestructible", Boolean.class));

        // Rôles (multi-valué)
        AttributeInfoBuilder rolesBuilder = new AttributeInfoBuilder("roles", String.class);
        rolesBuilder.setMultiValued(true);
        userClassBuilder.addAttributeInfo(rolesBuilder.build());

        // Groupes LDAP (multi-valué)
        AttributeInfoBuilder ldapGroupsBuilder = new AttributeInfoBuilder("ldapGroups", String.class);
        ldapGroupsBuilder.setMultiValued(true);
        userClassBuilder.addAttributeInfo(ldapGroupsBuilder.build());

        // MySQL grants (comma-separated privileges like "SELECT, INSERT, UPDATE")
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("mysqlGrants", String.class));

        // PostgreSQL grants (comma-separated privileges like "SELECT, INSERT, UPDATE")
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("postgresqlGrants", String.class));

        // Odoo provisioning control
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("odooCreateUser", Boolean.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("odooCreateEmployee", Boolean.class));

        // Employee attributes
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("employeeNumber", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("department", String.class));
        userClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("mobile", String.class));

        // Password
        userClassBuilder.addAttributeInfo(OperationalAttributeInfos.PASSWORD);

        schemaBuilder.defineObjectClass(userClassBuilder.build());

        // Role ObjectClass
        ObjectClassInfoBuilder roleClassBuilder = new ObjectClassInfoBuilder();
        roleClassBuilder.setType("Role");
        roleClassBuilder.addAttributeInfo(Name.INFO);
        roleClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        roleClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("roleType", String.class));
        schemaBuilder.defineObjectClass(roleClassBuilder.build());

        // Service ObjectClass
        ObjectClassInfoBuilder serviceClassBuilder = new ObjectClassInfoBuilder();
        serviceClassBuilder.setType("Service");
        serviceClassBuilder.addAttributeInfo(Name.INFO);
        serviceClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        serviceClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("url", String.class));
        serviceClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("enabled", Boolean.class));
        serviceClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("serviceType", String.class));
        schemaBuilder.defineObjectClass(serviceClassBuilder.build());

        // Organisation ObjectClass
        ObjectClassInfoBuilder orgClassBuilder = new ObjectClassInfoBuilder();
        orgClassBuilder.setType("Organisation");
        orgClassBuilder.addAttributeInfo(Name.INFO);
        orgClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("parentOrgRef", String.class));
        orgClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("displayName", String.class));
        orgClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("locality", String.class));
        orgClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("costCenter", String.class));
        schemaBuilder.defineObjectClass(orgClassBuilder.build());

        // ============================================================
        // ENTITLEMENTS - Groupes LDAP et Profils SQL
        // ============================================================

        // LdapGroup ObjectClass (Entitlement)
        ObjectClassInfoBuilder ldapGroupClassBuilder = new ObjectClassInfoBuilder();
        ldapGroupClassBuilder.setType("LdapGroup");
        ldapGroupClassBuilder.addAttributeInfo(Name.INFO);
        ldapGroupClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("dn", String.class));
        ldapGroupClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("cn", String.class));
        ldapGroupClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        schemaBuilder.defineObjectClass(ldapGroupClassBuilder.build());

        // PostgresqlProfile ObjectClass (Entitlement)
        ObjectClassInfoBuilder pgProfileClassBuilder = new ObjectClassInfoBuilder();
        pgProfileClassBuilder.setType("PostgresqlProfile");
        pgProfileClassBuilder.addAttributeInfo(Name.INFO);
        pgProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("profileName", String.class));
        pgProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("grants", String.class));
        pgProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        schemaBuilder.defineObjectClass(pgProfileClassBuilder.build());

        // MysqlProfile ObjectClass (Entitlement)
        ObjectClassInfoBuilder mysqlProfileClassBuilder = new ObjectClassInfoBuilder();
        mysqlProfileClassBuilder.setType("MysqlProfile");
        mysqlProfileClassBuilder.addAttributeInfo(Name.INFO);
        mysqlProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("profileName", String.class));
        mysqlProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("grants", String.class));
        mysqlProfileClassBuilder.addAttributeInfo(AttributeInfoBuilder.build("description", String.class));
        schemaBuilder.defineObjectClass(mysqlProfileClassBuilder.build());

        Schema schema = schemaBuilder.build();
        LOG.info("Schema built with {} object classes", schema.getObjectClassInfo().size());
        return schema;
    }

    @Override
    public Uid create(ObjectClass objectClass, Set<Attribute> createAttributes, OperationOptions options) {
        String entityType = getEntityType(objectClass);
        LOG.info("Creating {} with attributes: {}", entityType, createAttributes);

        // Générer un UID unique (UUID)
        String uid = java.util.UUID.randomUUID().toString();

        // Récupérer le __NAME__ pour le stocker aussi
        Attribute nameAttr = AttributeUtil.find(Name.NAME, createAttributes);
        String name = nameAttr != null ? AttributeUtil.getStringValue(nameAttr) : uid;

        Map<String, Object> attributesMap = JsonMapper.attributesToMap(createAttributes);
        // Créer le payload AVEC l'UID inclus
        String jsonPayload = JsonMapper.createPayload("CREATE", entityType, uid, attributesMap);

        sendMessage(jsonPayload);

        LOG.info("Created {} with UID: {} (name: {})", entityType, uid, name);
        return new Uid(uid, new Name(name));
    }

    @Override
    public Set<AttributeDelta> updateDelta(ObjectClass objectClass, Uid uid, Set<AttributeDelta> modifications, OperationOptions options) {
        String entityType = getEntityType(objectClass);
        LOG.info("Updating {} with UID {} - modifications: {}", entityType, uid.getUidValue(), modifications);

        Map<String, Object> attributesMap = JsonMapper.attributeDeltasToMap(modifications);
        String jsonPayload = JsonMapper.createPayload("UPDATE", entityType, uid.getUidValue(), attributesMap);

        sendMessage(jsonPayload);

        LOG.info("Updated {} with UID: {}", entityType, uid.getUidValue());
        return null; // No side effects
    }

    @Override
    public void delete(ObjectClass objectClass, Uid uid, OperationOptions options) {
        String entityType = getEntityType(objectClass);
        LOG.info("Deleting {} with UID: {}", entityType, uid.getUidValue());

        String jsonPayload = JsonMapper.createPayload("DELETE", entityType, uid.getUidValue(), null);

        sendMessage(jsonPayload);

        LOG.info("Deleted {} with UID: {}", entityType, uid.getUidValue());
    }

    private void sendMessage(String jsonPayload) {
        try {
            if (Boolean.TRUE.equals(configuration.getUseRabbitmq())) {
                rabbitmqClient.publish(jsonPayload);
            } else {
                String endpoint = "/create";
                if (jsonPayload.contains("\"operation\":\"UPDATE\"")) {
                    endpoint = "/update";
                } else if (jsonPayload.contains("\"operation\":\"DELETE\"")) {
                    endpoint = "/delete";
                }
                client.post(endpoint, jsonPayload);
            }
        } catch (Exception e) {
            throw new RuntimeException("Failed to send message: " + e.getMessage(), e);
        }
    }

    private String getEntityType(ObjectClass objectClass) {
        String type = objectClass.getObjectClassValue();

        // Map __ACCOUNT__ to User
        if (ObjectClass.ACCOUNT_NAME.equals(type)) {
            return "User";
        }

        return type;
    }

    @Override
    public FilterTranslator<String> createFilterTranslator(ObjectClass objectClass, OperationOptions options) {
        LOG.debug("createFilterTranslator called - returning UID-based translator");
        return new AbstractFilterTranslator<String>() {
            @Override
            protected String createEqualsExpression(org.identityconnectors.framework.common.objects.filter.EqualsFilter filter, boolean not) {
                if (!not && filter.getAttribute().is(Uid.NAME)) {
                    // Retourner l'UID pour le rechercher
                    return AttributeUtil.getStringValue(filter.getAttribute());
                }
                return null;
            }
        };
    }

    @Override
    public void executeQuery(ObjectClass objectClass, String query, ResultsHandler handler, OperationOptions options) {
        String entityType = getEntityType(objectClass);
        LOG.info("executeQuery called for {} with query: {}", entityType, query);

        String objectClassName = objectClass.getObjectClassValue();

        // ============================================================
        // ENTITLEMENTS - Recherche des groupes LDAP et profils SQL
        // ============================================================

        if ("LdapGroup".equals(objectClassName)) {
            // Récupérer les groupes LDAP depuis la Gateway
            LOG.info("Fetching LDAP groups from gateway...");
            List<Map<String, Object>> groups = getHttpClient().fetchLdapGroups();
            LOG.info("Found {} LDAP groups", groups.size());

            for (Map<String, Object> group : groups) {
                String dn = (String) group.get("dn");
                String cn = (String) group.get("cn");
                String description = (String) group.get("description");

                // Si on a un query (UID), filtrer
                if (query != null && !query.isEmpty() && !query.equals(dn)) {
                    continue;
                }

                ConnectorObjectBuilder builder = new ConnectorObjectBuilder();
                builder.setObjectClass(objectClass);
                builder.setUid(dn);  // UID = DN du groupe
                builder.setName(cn != null ? cn : dn);  // Name = CN
                builder.addAttribute("dn", dn);
                builder.addAttribute("cn", cn);
                builder.addAttribute("description", description);

                if (!handler.handle(builder.build())) {
                    break;
                }
            }
            return;
        }

        if ("PostgresqlProfile".equals(objectClassName)) {
            // Récupérer les profils PostgreSQL depuis la Gateway
            LOG.info("Fetching PostgreSQL profiles from gateway...");
            List<Map<String, Object>> profiles = getHttpClient().fetchPostgresqlProfiles();
            LOG.info("Found {} PostgreSQL profiles", profiles.size());

            for (Map<String, Object> profile : profiles) {
                String profileName = (String) profile.get("profileName");
                String grants = (String) profile.get("grants");
                String description = (String) profile.get("description");

                // Si on a un query (UID), filtrer
                if (query != null && !query.isEmpty() && !query.equals(profileName)) {
                    continue;
                }

                ConnectorObjectBuilder builder = new ConnectorObjectBuilder();
                builder.setObjectClass(objectClass);
                builder.setUid(profileName);  // UID = profileName
                builder.setName(profileName);
                builder.addAttribute("profileName", profileName);
                builder.addAttribute("grants", grants);
                builder.addAttribute("description", description);

                if (!handler.handle(builder.build())) {
                    break;
                }
            }
            return;
        }

        if ("MysqlProfile".equals(objectClassName)) {
            // Récupérer les profils MySQL depuis la Gateway
            LOG.info("Fetching MySQL profiles from gateway...");
            List<Map<String, Object>> profiles = getHttpClient().fetchMysqlProfiles();
            LOG.info("Found {} MySQL profiles", profiles.size());

            for (Map<String, Object> profile : profiles) {
                String profileName = (String) profile.get("profileName");
                String grants = (String) profile.get("grants");
                String description = (String) profile.get("description");

                // Si on a un query (UID), filtrer
                if (query != null && !query.isEmpty() && !query.equals(profileName)) {
                    continue;
                }

                ConnectorObjectBuilder builder = new ConnectorObjectBuilder();
                builder.setObjectClass(objectClass);
                builder.setUid(profileName);  // UID = profileName
                builder.setName(profileName);
                builder.addAttribute("profileName", profileName);
                builder.addAttribute("grants", grants);
                builder.addAttribute("description", description);

                if (!handler.handle(builder.build())) {
                    break;
                }
            }
            return;
        }

        // ============================================================
        // ACCOUNTS et autres types (comportement existant)
        // ============================================================

        // Si on a un UID (query), on retourne un ConnectorObject "simulé"
        // Cela permet à MidPoint de considérer que l'objet existe
        if (query != null && !query.isEmpty()) {
            LOG.info("Returning simulated object for UID: {}", query);

            ConnectorObjectBuilder builder = new ConnectorObjectBuilder();
            builder.setObjectClass(objectClass);
            builder.setUid(query);
            builder.setName(query); // Le name sera mis à jour par MidPoint si nécessaire

            handler.handle(builder.build());
        } else {
            LOG.debug("No query provided - returning empty result set (write-only connector)");
        }
    }

    /**
     * Get HTTP client (creates one if needed for RabbitMQ mode)
     */
    private RestGatewayClient getHttpClient() {
        if (client != null) {
            return client;
        }
        // En mode RabbitMQ, on crée un client HTTP temporaire pour les entitlements
        return new RestGatewayClient(configuration);
    }
}
