package lu.lns.connector.restgateway;

import org.identityconnectors.common.security.GuardedString;
import org.identityconnectors.framework.spi.AbstractConfiguration;
import org.identityconnectors.framework.spi.ConfigurationProperty;

public class RestGatewayConfiguration extends AbstractConfiguration {

    private String gatewayUrl = "http://localhost:5100";
    private Integer connectionTimeout = 30000;
    private Integer requestTimeout = 60000;
    private Boolean validateSsl = true;

    // RabbitMQ Configuration
    private Boolean useRabbitmq = false;
    private String rabbitmqHost = "localhost";
    private Integer rabbitmqPort = 5672;
    private String rabbitmqUsername = "admin";
    private String rabbitmqPassword = "admin123";
    private String rabbitmqQueue = "midpoint-operations";

    @Override
    public void validate() {
        if (gatewayUrl == null || gatewayUrl.trim().isEmpty()) {
            throw new IllegalArgumentException("Gateway URL cannot be empty");
        }

        if (!gatewayUrl.startsWith("http://") && !gatewayUrl.startsWith("https://")) {
            throw new IllegalArgumentException("Gateway URL must start with http:// or https://");
        }

        if (connectionTimeout != null && connectionTimeout <= 0) {
            throw new IllegalArgumentException("Connection timeout must be positive");
        }

        if (requestTimeout != null && requestTimeout <= 0) {
            throw new IllegalArgumentException("Request timeout must be positive");
        }
    }

    @ConfigurationProperty(
        order = 1,
        displayMessageKey = "gatewayUrl.display",
        helpMessageKey = "gatewayUrl.help",
        required = true
    )
    public String getGatewayUrl() {
        return gatewayUrl;
    }

    public void setGatewayUrl(String gatewayUrl) {
        this.gatewayUrl = gatewayUrl;
    }

    @ConfigurationProperty(
        order = 2,
        displayMessageKey = "connectionTimeout.display",
        helpMessageKey = "connectionTimeout.help"
    )
    public Integer getConnectionTimeout() {
        return connectionTimeout;
    }

    public void setConnectionTimeout(Integer connectionTimeout) {
        this.connectionTimeout = connectionTimeout;
    }

    @ConfigurationProperty(
        order = 3,
        displayMessageKey = "requestTimeout.display",
        helpMessageKey = "requestTimeout.help"
    )
    public Integer getRequestTimeout() {
        return requestTimeout;
    }

    public void setRequestTimeout(Integer requestTimeout) {
        this.requestTimeout = requestTimeout;
    }

    @ConfigurationProperty(
        order = 4,
        displayMessageKey = "validateSsl.display",
        helpMessageKey = "validateSsl.help"
    )
    public Boolean getValidateSsl() {
        return validateSsl;
    }

    public void setValidateSsl(Boolean validateSsl) {
        this.validateSsl = validateSsl;
    }

    @ConfigurationProperty(
        order = 10,
        displayMessageKey = "useRabbitmq.display",
        helpMessageKey = "useRabbitmq.help"
    )
    public Boolean getUseRabbitmq() {
        return useRabbitmq;
    }

    public void setUseRabbitmq(Boolean useRabbitmq) {
        this.useRabbitmq = useRabbitmq;
    }

    @ConfigurationProperty(
        order = 11,
        displayMessageKey = "rabbitmqHost.display",
        helpMessageKey = "rabbitmqHost.help"
    )
    public String getRabbitmqHost() {
        return rabbitmqHost;
    }

    public void setRabbitmqHost(String rabbitmqHost) {
        this.rabbitmqHost = rabbitmqHost;
    }

    @ConfigurationProperty(
        order = 12,
        displayMessageKey = "rabbitmqPort.display",
        helpMessageKey = "rabbitmqPort.help"
    )
    public Integer getRabbitmqPort() {
        return rabbitmqPort;
    }

    public void setRabbitmqPort(Integer rabbitmqPort) {
        this.rabbitmqPort = rabbitmqPort;
    }

    @ConfigurationProperty(
        order = 13,
        displayMessageKey = "rabbitmqUsername.display",
        helpMessageKey = "rabbitmqUsername.help"
    )
    public String getRabbitmqUsername() {
        return rabbitmqUsername;
    }

    public void setRabbitmqUsername(String rabbitmqUsername) {
        this.rabbitmqUsername = rabbitmqUsername;
    }

    @ConfigurationProperty(
        order = 14,
        displayMessageKey = "rabbitmqPassword.display",
        helpMessageKey = "rabbitmqPassword.help",
        confidential = true
    )
    public String getRabbitmqPassword() {
        return rabbitmqPassword;
    }

    public void setRabbitmqPassword(String rabbitmqPassword) {
        this.rabbitmqPassword = rabbitmqPassword;
    }

    @ConfigurationProperty(
        order = 15,
        displayMessageKey = "rabbitmqQueue.display",
        helpMessageKey = "rabbitmqQueue.help"
    )
    public String getRabbitmqQueue() {
        return rabbitmqQueue;
    }

    public void setRabbitmqQueue(String rabbitmqQueue) {
        this.rabbitmqQueue = rabbitmqQueue;
    }
}
