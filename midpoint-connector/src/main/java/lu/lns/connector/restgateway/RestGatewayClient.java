package lu.lns.connector.restgateway;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.reflect.TypeToken;
import org.identityconnectors.framework.common.exceptions.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import java.io.IOException;
import java.lang.reflect.Type;
import java.net.ConnectException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.security.KeyManagementException;
import java.security.NoSuchAlgorithmException;
import java.security.cert.X509Certificate;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class RestGatewayClient {

    private static final Logger LOG = LoggerFactory.getLogger(RestGatewayClient.class);
    private static final Gson GSON = new Gson();

    private final HttpClient httpClient;
    private final String gatewayUrl;
    private final int requestTimeout;

    public RestGatewayClient(RestGatewayConfiguration config) {
        this.gatewayUrl = config.getGatewayUrl();
        this.requestTimeout = config.getRequestTimeout();

        HttpClient.Builder clientBuilder = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofMillis(config.getConnectionTimeout()));

        // Disable SSL validation if configured
        if (Boolean.FALSE.equals(config.getValidateSsl())) {
            try {
                SSLContext sslContext = createInsecureSSLContext();
                clientBuilder.sslContext(sslContext);
                LOG.warn("SSL certificate validation is disabled");
            } catch (Exception e) {
                LOG.error("Failed to create insecure SSL context", e);
            }
        }

        this.httpClient = clientBuilder.build();
    }

    public String post(String endpoint, String jsonPayload) {
        String url = gatewayUrl + endpoint;

        LOG.debug("POST {} with payload: {}", url, jsonPayload);

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofMillis(requestTimeout))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(jsonPayload))
            .build();

        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());

            LOG.debug("Response status: {}, body: {}", response.statusCode(), response.body());

            handleResponseStatus(response.statusCode(), response.body());

            return response.body();

        } catch (HttpTimeoutException e) {
            LOG.error("Request timeout after {}ms: {}", requestTimeout, url, e);
            throw new OperationTimeoutException("Request timeout after " + requestTimeout + "ms", e);

        } catch (ConnectException e) {
            LOG.error("Cannot connect to gateway at {}", gatewayUrl, e);
            throw new ConnectionFailedException("Cannot connect to gateway at " + gatewayUrl, e);

        } catch (IOException e) {
            LOG.error("I/O error communicating with gateway", e);
            throw new ConnectorIOException("I/O error communicating with gateway", e);

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            LOG.error("Request interrupted", e);
            throw new ConnectorException("Request interrupted", e);
        }
    }

    public void testConnection() {
        String url = gatewayUrl;

        LOG.debug("Testing connection to {}", url);

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofMillis(requestTimeout))
            .GET()
            .build();

        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() >= 400) {
                throw new ConnectorException("Gateway not available: HTTP " + response.statusCode());
            }

            LOG.info("Connection test successful: {}", url);

        } catch (HttpTimeoutException e) {
            throw new OperationTimeoutException("Connection test timeout", e);

        } catch (ConnectException e) {
            throw new ConnectionFailedException("Cannot connect to gateway", e);

        } catch (IOException e) {
            throw new ConnectorIOException("I/O error during connection test", e);

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ConnectorException("Connection test interrupted", e);
        }
    }

    /**
     * Execute a GET request and return the response body
     */
    public String get(String endpoint) {
        String url = gatewayUrl + endpoint;

        LOG.debug("GET {}", url);

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofMillis(requestTimeout))
            .header("Accept", "application/json")
            .GET()
            .build();

        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());

            LOG.debug("Response status: {}, body: {}", response.statusCode(), response.body());

            handleResponseStatus(response.statusCode(), response.body());

            return response.body();

        } catch (HttpTimeoutException e) {
            LOG.error("Request timeout after {}ms: {}", requestTimeout, url, e);
            throw new OperationTimeoutException("Request timeout after " + requestTimeout + "ms", e);

        } catch (ConnectException e) {
            LOG.error("Cannot connect to gateway at {}", gatewayUrl, e);
            throw new ConnectionFailedException("Cannot connect to gateway at " + gatewayUrl, e);

        } catch (IOException e) {
            LOG.error("I/O error communicating with gateway", e);
            throw new ConnectorIOException("I/O error communicating with gateway", e);

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            LOG.error("Request interrupted", e);
            throw new ConnectorException("Request interrupted", e);
        }
    }

    /**
     * Fetch LDAP groups from the gateway
     */
    public List<Map<String, Object>> fetchLdapGroups() {
        try {
            String response = get("/entitlements/ldap-groups");
            Type listType = new TypeToken<List<Map<String, Object>>>(){}.getType();
            return GSON.fromJson(response, listType);
        } catch (Exception e) {
            LOG.warn("Failed to fetch LDAP groups: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /**
     * Fetch PostgreSQL profiles from the gateway
     */
    public List<Map<String, Object>> fetchPostgresqlProfiles() {
        try {
            String response = get("/entitlements/postgresql-profiles");
            Type listType = new TypeToken<List<Map<String, Object>>>(){}.getType();
            return GSON.fromJson(response, listType);
        } catch (Exception e) {
            LOG.warn("Failed to fetch PostgreSQL profiles: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /**
     * Fetch MySQL profiles from the gateway
     */
    public List<Map<String, Object>> fetchMysqlProfiles() {
        try {
            String response = get("/entitlements/mysql-profiles");
            Type listType = new TypeToken<List<Map<String, Object>>>(){}.getType();
            return GSON.fromJson(response, listType);
        } catch (Exception e) {
            LOG.warn("Failed to fetch MySQL profiles: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    private void handleResponseStatus(int statusCode, String body) {
        if (statusCode >= 200 && statusCode < 300) {
            return; // Success
        }

        String errorMessage = extractErrorMessage(body);

        switch (statusCode) {
            case 400:
                throw new InvalidAttributeValueException("Gateway rejected request: " + errorMessage);

            case 401:
            case 403:
                throw new PermissionDeniedException("Authentication/authorization failed: " + errorMessage);

            case 404:
                throw new UnknownUidException("Object not found: " + errorMessage);

            case 409:
                throw new AlreadyExistsException("Object already exists: " + errorMessage);

            case 500:
            case 502:
            case 503:
            case 504:
                throw new ConnectorException("Gateway error: " + errorMessage);

            default:
                throw new ConnectorException("Unexpected response code " + statusCode + ": " + errorMessage);
        }
    }

    private String extractErrorMessage(String body) {
        if (body == null || body.trim().isEmpty()) {
            return "No error message";
        }

        try {
            JsonObject json = JsonParser.parseString(body).getAsJsonObject();
            if (json.has("message")) {
                return json.get("message").getAsString();
            }
            if (json.has("error")) {
                return json.get("error").getAsString();
            }
        } catch (Exception e) {
            // Not JSON or parsing failed, return raw body
        }

        return body.length() > 200 ? body.substring(0, 200) + "..." : body;
    }

    private SSLContext createInsecureSSLContext() throws NoSuchAlgorithmException, KeyManagementException {
        TrustManager[] trustAllCerts = new TrustManager[]{
            new X509TrustManager() {
                public X509Certificate[] getAcceptedIssuers() {
                    return new X509Certificate[0];
                }
                public void checkClientTrusted(X509Certificate[] certs, String authType) {}
                public void checkServerTrusted(X509Certificate[] certs, String authType) {}
            }
        };

        SSLContext sslContext = SSLContext.getInstance("TLS");
        sslContext.init(null, trustAllCerts, new java.security.SecureRandom());
        return sslContext;
    }
}
