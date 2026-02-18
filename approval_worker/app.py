"""Flask Approval Worker - Simulates manual approval with random decisions"""
import os
import time
import random
import logging
import threading
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration (valeurs par defaut pour fonctionner sans .env)
GATEWAY_CALLBACK_URL = os.getenv("GATEWAY_CALLBACK_URL", "http://localhost:8000")
SLEEP_DURATION = int(os.getenv("SLEEP_DURATION", 10))
WORKER_ID = os.getenv("WORKER_ID", "flask-approval-worker-1")
WORKER_PORT = int(os.getenv("WORKER_PORT", 5001))

# In-memory tracking of active threads (for monitoring)
active_threads = {}


def process_approval_decision(operation_id: str, request_id: str):
    """
    Background thread that sleeps and then makes a random approval decision.

    Args:
        operation_id: ID of the operation to approve/reject
        request_id: Unique request ID for tracking
    """
    try:
        logger.info(
            f"[{operation_id}] Starting approval process. Sleeping for {SLEEP_DURATION}s..."
        )
        active_threads[operation_id] = {
            "request_id": request_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "sleeping",
        }

        # Sleep for configured duration
        time.sleep(SLEEP_DURATION)

        # Always approve (set to False to always reject, or use random.choice([True, False]) for random)
        approved = True
        reason = f"Auto-approved after {SLEEP_DURATION}s"

        logger.info(
            f"[{operation_id}] Decision: {'APPROVED' if approved else 'REJECTED'} - {reason}"
        )

        # Update thread status
        active_threads[operation_id]["status"] = "sending_callback"

        # Send callback to Gateway
        callback_url = f"{GATEWAY_CALLBACK_URL}/api/v1/provisioning/{operation_id}/approve-callback"
        payload = {
            "approved": approved,
            "reason": reason,
            "worker_id": WORKER_ID,
            "request_id": request_id,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }

        response = requests.post(callback_url, json=payload, timeout=30)
        response.raise_for_status()

        logger.info(
            f"[{operation_id}] Callback sent successfully. Gateway response: {response.status_code}"
        )

        # Mark as completed
        active_threads[operation_id]["status"] = "completed"
        active_threads[operation_id]["completed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

    except requests.RequestException as e:
        logger.error(f"[{operation_id}] Failed to send callback to Gateway: {e}")
        active_threads[operation_id]["status"] = "failed"
        active_threads[operation_id]["error"] = str(e)

    except Exception as e:
        logger.error(f"[{operation_id}] Unexpected error in approval process: {e}")
        active_threads[operation_id]["status"] = "failed"
        active_threads[operation_id]["error"] = str(e)


@app.route("/api/v1/approve/request", methods=["POST"])
def receive_approval_request():
    """
    Receive approval request from Gateway and start background processing.

    Expected payload:
        {
            "operation_id": str,
            "request_id": str,
            "operation_data": dict
        }

    Returns:
        202 Accepted with worker details
    """
    try:
        data = request.get_json()
        operation_id = data.get("operation_id")
        request_id = data.get("request_id")
        operation_data = data.get("operation_data", {})

        if not operation_id or not request_id:
            return (
                jsonify(
                    {"error": "Missing required fields: operation_id, request_id"}
                ),
                400,
            )

        logger.info(
            f"[{operation_id}] Received approval request. Request ID: {request_id}"
        )

        # Start background thread
        thread = threading.Thread(
            target=process_approval_decision,
            args=(operation_id, request_id),
            daemon=True,
        )
        thread.start()

        return (
            jsonify(
                {
                    "status": "accepted",
                    "operation_id": operation_id,
                    "request_id": request_id,
                    "worker_id": WORKER_ID,
                    "sleep_duration": SLEEP_DURATION,
                    "message": f"Approval request accepted. Decision will be made in {SLEEP_DURATION} seconds.",
                }
            ),
            202,
        )

    except Exception as e:
        logger.error(f"Error processing approval request: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/health", methods=["GET"])
def health_check():
    """Health check endpoint.

    Returns:
        200 OK with worker status
    """
    return (
        jsonify(
            {
                "status": "healthy",
                "worker_id": WORKER_ID,
                "active_threads": len(active_threads),
                "sleep_duration": SLEEP_DURATION,
                "gateway_url": GATEWAY_CALLBACK_URL,
            }
        ),
        200,
    )


@app.route("/api/v1/status", methods=["GET"])
def get_status():
    """Get status of all active approval threads (for monitoring).

    Returns:
        200 OK with active threads details
    """
    return (
        jsonify({"worker_id": WORKER_ID, "active_threads": active_threads}),
        200,
    )


if __name__ == "__main__":
    logger.info(f"Starting Flask Approval Worker: {WORKER_ID}")
    logger.info(f"Gateway callback URL: {GATEWAY_CALLBACK_URL}")
    logger.info(f"Sleep duration: {SLEEP_DURATION} seconds")
    logger.info(f"Listening on port: {WORKER_PORT}")

    app.run(host="0.0.0.0", port=WORKER_PORT, debug=False)
