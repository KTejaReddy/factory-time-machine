import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ai_ask_uses_dataset_key():
    """Test that the AI /ask endpoint accepts and uses the dataset key."""
    # We test with a fake model key to see if it complains about the missing key
    response = client.post(
        "/api/ai/ask",
        json={
            "question": "What is the bottleneck?",
            "scope": "overview",
            "key": "user:nonexistent_dataset_123"
        }
    )
    # The application should raise a 404 or something because the dataset doesn't exist.
    # The exact error depends on the implementation, but it definitely shouldn't silently use model3
    assert response.status_code != 200, "Should not succeed with a nonexistent dataset"


def test_simulation_options_uses_dataset_key():
    """Test that the What-If /options endpoint accepts the key parameter."""
    response = client.get("/api/simulation/options?key=user:nonexistent_dataset_123")
    assert response.status_code != 200, "Should not succeed with a nonexistent dataset"


def test_simulation_validation_uses_dataset_key():
    """Test that the What-If /validation endpoint accepts the key parameter."""
    response = client.get("/api/simulation/validation?key=user:nonexistent_dataset_123")
    assert response.status_code != 200, "Should not succeed with a nonexistent dataset"
