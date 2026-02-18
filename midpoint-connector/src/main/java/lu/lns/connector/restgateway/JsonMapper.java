package lu.lns.connector.restgateway;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.identityconnectors.common.security.GuardedString;
import org.identityconnectors.framework.common.objects.Attribute;
import org.identityconnectors.framework.common.objects.AttributeDelta;
import org.identityconnectors.framework.common.objects.Name;
import org.identityconnectors.framework.common.objects.Uid;

import java.time.Instant;
import java.util.*;

public class JsonMapper {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public static String createPayload(String operation, String entityType, Map<String, Object> attributes) {
        return createPayload(operation, entityType, null, attributes);
    }

    public static String createPayload(String operation, String entityType, String uid, Map<String, Object> attributes) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("operation", operation);
        payload.put("entityType", entityType);
        payload.put("timestamp", Instant.now().toString());

        if (uid != null) {
            payload.put("uid", uid);
        }

        if (attributes != null && !attributes.isEmpty()) {
            payload.put("attributes", attributes);
        }

        return GSON.toJson(payload);
    }

    public static Map<String, Object> attributesToMap(Set<Attribute> attributes) {
        Map<String, Object> result = new LinkedHashMap<>();

        for (Attribute attr : attributes) {
            String name = attr.getName();
            List<Object> values = attr.getValue();

            // Skip special __UID__ attribute (it's not an attribute to send)
            if (Uid.NAME.equals(name)) {
                continue;
            }

            // Handle __NAME__ specially - it's the username/identifier
            if (Name.NAME.equals(name)) {
                result.put("username", getSingleValue(values));
                continue;
            }

            // Handle GuardedString (passwords)
            if (values != null && !values.isEmpty() && values.get(0) instanceof GuardedString) {
                result.put(name, extractGuardedString((GuardedString) values.get(0)));
                continue;
            }

            // Handle multi-valued attributes
            if (values == null || values.isEmpty()) {
                result.put(name, null);
            } else if (values.size() == 1) {
                result.put(name, values.get(0));
            } else {
                result.put(name, values);
            }
        }

        return result;
    }

    public static Map<String, Object> attributeDeltasToMap(Set<AttributeDelta> deltas) {
        Map<String, Object> result = new LinkedHashMap<>();

        for (AttributeDelta delta : deltas) {
            String name = delta.getName();

            // Skip __UID__ (immutable)
            if (Uid.NAME.equals(name)) {
                continue;
            }

            // Handle __NAME__
            if (Name.NAME.equals(name)) {
                List<Object> valuesToReplace = delta.getValuesToReplace();
                if (valuesToReplace != null && !valuesToReplace.isEmpty()) {
                    result.put("username", getSingleValue(valuesToReplace));
                }
                continue;
            }

            // For UPDATE, we send the final state, not deltas
            // If valuesToReplace is set, use it (full replacement)
            List<Object> valuesToReplace = delta.getValuesToReplace();
            if (valuesToReplace != null) {
                if (valuesToReplace.isEmpty()) {
                    result.put(name, null);
                } else if (valuesToReplace.size() == 1) {
                    Object value = valuesToReplace.get(0);
                    if (value instanceof GuardedString) {
                        result.put(name, extractGuardedString((GuardedString) value));
                    } else {
                        result.put(name, value);
                    }
                } else {
                    result.put(name, valuesToReplace);
                }
            } else {
                // Handle add/remove for multi-valued attributes
                List<Object> toAdd = delta.getValuesToAdd();
                List<Object> toRemove = delta.getValuesToRemove();

                // Send added values
                if (toAdd != null && !toAdd.isEmpty()) {
                    result.put(name, toAdd.size() == 1 ? toAdd.get(0) : toAdd);
                }

                // Track removed values for specific attributes (roles, ldapGroups)
                // This allows the gateway to trigger DELETE operations for removed services
                if (toRemove != null && !toRemove.isEmpty()) {
                    String removedKey = "removed" + capitalizeFirst(name);
                    result.put(removedKey, toRemove.size() == 1 ? toRemove.get(0) : toRemove);
                }

                // If only removal (no add), also set the attribute to empty
                if ((toAdd == null || toAdd.isEmpty()) && (toRemove != null && !toRemove.isEmpty())) {
                    result.put(name, Collections.emptyList());
                }
            }
        }

        return result;
    }

    private static String capitalizeFirst(String str) {
        if (str == null || str.isEmpty()) {
            return str;
        }
        return Character.toUpperCase(str.charAt(0)) + str.substring(1);
    }

    private static Object getSingleValue(List<Object> values) {
        if (values == null || values.isEmpty()) {
            return null;
        }
        return values.get(0);
    }

    private static String extractGuardedString(GuardedString guardedString) {
        if (guardedString == null) {
            return null;
        }

        final StringBuilder sb = new StringBuilder();
        guardedString.access(chars -> {
            sb.append(new String(chars));
        });
        return sb.toString();
    }

    public static String parseUidFromResponse(String jsonResponse) {
        try {
            com.google.gson.JsonObject json = com.google.gson.JsonParser.parseString(jsonResponse).getAsJsonObject();
            if (json.has("uid")) {
                return json.get("uid").getAsString();
            }
        } catch (Exception e) {
            // Ignore parsing errors
        }
        return null;
    }
}
