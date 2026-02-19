package lu.lns.connector.restgateway;

import com.rabbitmq.client.Channel;
import com.rabbitmq.client.Connection;
import com.rabbitmq.client.ConnectionFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeoutException;

public class RabbitMQClient {

    private static final Logger LOG = LoggerFactory.getLogger(RabbitMQClient.class);

    private final RestGatewayConfiguration configuration;
    private Connection connection;
    private Channel channel;

    public RabbitMQClient(RestGatewayConfiguration configuration) {
        this.configuration = configuration;
    }

    public void init() throws IOException, TimeoutException {
        LOG.info("Initializing RabbitMQ connection to {}:{}",
                configuration.getRabbitmqHost(),
                configuration.getRabbitmqPort());

        ConnectionFactory factory = new ConnectionFactory();
        factory.setHost(configuration.getRabbitmqHost());
        factory.setPort(configuration.getRabbitmqPort());
        factory.setUsername(configuration.getRabbitmqUsername());
        factory.setPassword(configuration.getRabbitmqPassword());
        factory.setConnectionTimeout(30000);
        factory.setRequestedHeartbeat(60);
        factory.setAutomaticRecoveryEnabled(true);

        this.connection = factory.newConnection();
        this.channel = connection.createChannel();

        String queueName = configuration.getRabbitmqQueue();
        channel.queueDeclare(queueName, true, false, false, null);

        LOG.info("RabbitMQ connection established, queue: {}", queueName);
    }

    public void publish(String jsonMessage) throws IOException {
        if (channel == null || !channel.isOpen()) {
            LOG.warn("RabbitMQ channel is not open, attempting to reconnect...");
            try {
                reconnect();
            } catch (TimeoutException e) {
                throw new IOException("Failed to reconnect to RabbitMQ: " + e.getMessage(), e);
            }
        }

        String queueName = configuration.getRabbitmqQueue();
        channel.basicPublish("", queueName, null, jsonMessage.getBytes(StandardCharsets.UTF_8));
        LOG.info("Message published to RabbitMQ queue: {}", queueName);
    }

    private void reconnect() throws IOException, TimeoutException {
        LOG.info("Reconnecting to RabbitMQ {}:{}", configuration.getRabbitmqHost(), configuration.getRabbitmqPort());
        close();
        init();
        LOG.info("Reconnected to RabbitMQ successfully");
    }

    public void testConnection() throws IOException, TimeoutException {
        LOG.info("Testing RabbitMQ connection");
        if (channel == null || !channel.isOpen()) {
            throw new IOException("RabbitMQ channel is not initialized or not open");
        }
        LOG.info("RabbitMQ connection test successful");
        // Ne pas fermer la connexion - elle est réutilisée pour publish()
    }

    public void close() {
        try {
            if (channel != null && channel.isOpen()) {
                channel.close();
            }
            if (connection != null && connection.isOpen()) {
                connection.close();
            }
            LOG.info("RabbitMQ connection closed");
        } catch (IOException | TimeoutException e) {
            LOG.error("Error closing RabbitMQ connection", e);
        }
    }
}
