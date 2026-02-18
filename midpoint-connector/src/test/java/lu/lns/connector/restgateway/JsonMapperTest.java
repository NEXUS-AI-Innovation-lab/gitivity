package lu.lns.connector.restgateway;

import org.identityconnectors.framework.common.objects.*;
import org.junit.Test;

import java.util.*;

import static org.junit.Assert.*;

public class JsonMapperTest {

    @Test
    public void testAttributesToMap() {
        Set<Attribute> attributes = new HashSet<>();
        attributes.add(new Name("jdoe"));
        attributes.add(AttributeBuilder.build("firstName", "John"));
        attributes.add(AttributeBuilder.build("lastName", "Doe"));
        attributes.add(AttributeBuilder.build("email", "john.doe@example.com"));
        attributes.add(AttributeBuilder.build("enabled", true));

        Map<String, Object> result = JsonMapper.attributesToMap(attributes);

        assertEquals("jdoe", result.get("username"));
        assertEquals("John", result.get("firstName"));
        assertEquals("Doe", result.get("lastName"));
        assertEquals("john.doe@example.com", result.get("email"));
        assertEquals(true, result.get("enabled"));
    }

    @Test
    public void testAttributesToMapWithMultiValue() {
        Set<Attribute> attributes = new HashSet<>();
        attributes.add(new Name("jdoe"));
        attributes.add(AttributeBuilder.build("roles", Arrays.asList("ROLE_USER", "ROLE_ADMIN")));

        Map<String, Object> result = JsonMapper.attributesToMap(attributes);

        assertEquals("jdoe", result.get("username"));
        assertTrue(result.get("roles") instanceof List);
        @SuppressWarnings("unchecked")
        List<Object> roles = (List<Object>) result.get("roles");
        assertEquals(2, roles.size());
        assertTrue(roles.contains("ROLE_USER"));
        assertTrue(roles.contains("ROLE_ADMIN"));
    }

    @Test
    public void testCreatePayloadWithoutUid() {
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("username", "jdoe");
        attributes.put("email", "jdoe@example.com");

        String json = JsonMapper.createPayload("CREATE", "User", attributes);

        assertNotNull(json);
        assertTrue(json.contains("\"operation\": \"CREATE\""));
        assertTrue(json.contains("\"entityType\": \"User\""));
        assertTrue(json.contains("\"timestamp\""));
        assertTrue(json.contains("\"attributes\""));
        assertTrue(json.contains("\"username\": \"jdoe\""));
        assertFalse(json.contains("\"uid\""));
    }

    @Test
    public void testCreatePayloadWithUid() {
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("email", "new@example.com");

        String json = JsonMapper.createPayload("UPDATE", "User", "12345", attributes);

        assertNotNull(json);
        assertTrue(json.contains("\"operation\": \"UPDATE\""));
        assertTrue(json.contains("\"entityType\": \"User\""));
        assertTrue(json.contains("\"uid\": \"12345\""));
        assertTrue(json.contains("\"email\": \"new@example.com\""));
    }

    @Test
    public void testParseUidFromResponse() {
        String jsonResponse = "{\"success\": true, \"uid\": \"12345\", \"message\": \"Created\"}";

        String uid = JsonMapper.parseUidFromResponse(jsonResponse);

        assertEquals("12345", uid);
    }

    @Test
    public void testParseUidFromResponseWithoutUid() {
        String jsonResponse = "{\"success\": true, \"message\": \"Created\"}";

        String uid = JsonMapper.parseUidFromResponse(jsonResponse);

        assertNull(uid);
    }

    @Test
    public void testAttributeDeltasToMap() {
        Set<AttributeDelta> deltas = new HashSet<>();
        deltas.add(AttributeDeltaBuilder.build("email", "newemail@example.com"));
        deltas.add(AttributeDeltaBuilder.build("enabled", false));

        Map<String, Object> result = JsonMapper.attributeDeltasToMap(deltas);

        assertEquals("newemail@example.com", result.get("email"));
        assertEquals(false, result.get("enabled"));
    }
}
