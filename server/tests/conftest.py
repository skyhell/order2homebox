"""Test environment is configured BEFORE the app modules are imported."""
import os
import tempfile

import bcrypt
import pytest

TEST_PASSWORD = "testpass"

os.environ["O2H_SECRET_KEY"] = "test-secret"
os.environ["O2H_WEB_USER"] = "admin"
os.environ["O2H_WEB_PASSWORD_HASH"] = bcrypt.hashpw(
    TEST_PASSWORD.encode(), bcrypt.gensalt()
).decode()
os.environ["O2H_DATA_DIR"] = tempfile.mkdtemp(prefix="o2h-test-")
os.environ["O2H_HOMEBOX_URL"] = "http://homebox.test"
os.environ["O2H_HOMEBOX_USERNAME"] = "hb-user"
os.environ["O2H_HOMEBOX_PASSWORD"] = "hb-pass"
os.environ["O2H_PRINT_AGENT_URL"] = "http://printagent.test"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def logged_in(client):
    response = client.post(
        "/login", data={"username": "admin", "password": TEST_PASSWORD}
    )
    assert response.status_code == 303
    return client
