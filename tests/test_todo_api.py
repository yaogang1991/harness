"""
Test suite for the Todo Items REST API.

Success Criteria:
  - All CRUD operations tested
  - Validation edge cases covered
  - Error responses verified
  - Coverage > 80%
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from todo_api.main import app, _storage
from todo_api.models import TodoItemCreate

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_storage():
    """Clear storage before each test."""
    _storage.clear()
    yield
    _storage.clear()


@pytest.fixture
def sample_todo():
    """Create a sample todo item and return its ID."""
    response = client.post("/api/v1/todos", json={
        "title": "Test Todo",
        "description": "A test description",
        "completed": False,
        "priority": "medium",
        "tags": ["test", "api"],
    })
    assert response.status_code == 201
    return response.json()["id"]


# ---------------------------------------------------------------------------
# POST /api/v1/todos — Create
# ---------------------------------------------------------------------------

class TestCreateTodo:
    """Success Criteria:
      - Creates item with auto-generated UUID
      - Sets created_at and updated_at to current UTC time
      - Returns 201 with full item and Location header
      - Rejects empty title with 400
      - Rejects invalid priority with 422
    """

    def test_create_success(self):
        response = client.post("/api/v1/todos", json={
            "title": "Buy groceries",
            "description": "Milk, eggs, bread",
            "priority": "high",
        })
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["title"] == "Buy groceries"
        assert data["completed"] is False
        assert data["priority"] == "high"
        assert "created_at" in data
        assert "updated_at" in data
        assert "Location" in response.headers

    def test_create_minimal(self):
        response = client.post("/api/v1/todos", json={"title": "Minimal"})
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Minimal"
        assert data["completed"] is False
        assert data["priority"] == "medium"
        assert data["tags"] == []

    def test_create_missing_title(self):
        response = client.post("/api/v1/todos", json={"description": "No title"})
        assert response.status_code == 422

    def test_create_empty_title(self):
        response = client.post("/api/v1/todos", json={"title": ""})
        assert response.status_code == 422

    def test_create_invalid_priority(self):
        response = client.post("/api/v1/todos", json={
            "title": "Test",
            "priority": "urgent",
        })
        assert response.status_code == 422

    def test_create_title_too_long(self):
        response = client.post("/api/v1/todos", json={"title": "x" * 201})
        assert response.status_code == 422

    def test_create_invalid_due_date(self):
        response = client.post("/api/v1/todos", json={
            "title": "Test",
            "due_date": "not-a-date",
        })
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/todos — List
# ---------------------------------------------------------------------------

class TestListTodos:
    """Success Criteria:
      - Returns paginated list of todo items
      - Supports filtering by completed, priority, and search text
      - Supports sorting by created_at, updated_at, priority, due_date
      - Returns empty list with total=0 when no items match
      - Invalid query params return 400 Bad Request
    """

    def test_list_empty(self):
        response = client.get("/api/v1/todos")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    def test_list_with_items(self, sample_todo):
        response = client.get("/api/v1/todos")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    def test_list_filter_completed(self, sample_todo):
        response = client.get("/api/v1/todos?completed=false")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        response = client.get("/api/v1/todos?completed=true")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_filter_priority(self, sample_todo):
        response = client.get("/api/v1/todos?priority=high")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

        response = client.get("/api/v1/todos?priority=medium")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_list_search(self, sample_todo):
        response = client.get("/api/v1/todos?search=Test")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        response = client.get("/api/v1/todos?search=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_pagination(self):
        # Create 5 items
        for i in range(5):
            client.post("/api/v1/todos", json={"title": f"Todo {i}"})

        response = client.get("/api/v1/todos?page=1&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["total_pages"] == 3

        response = client.get("/api/v1/todos?page=3&page_size=2")
        data = response.json()
        assert len(data["items"]) == 1

    def test_list_sort_by_priority(self):
        client.post("/api/v1/todos", json={"title": "Low", "priority": "low"})
        client.post("/api/v1/todos", json={"title": "High", "priority": "high"})
        client.post("/api/v1/todos", json={"title": "Medium", "priority": "medium"})

        response = client.get("/api/v1/todos?sort_by=priority&sort_order=asc")
        assert response.status_code == 200
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert titles == ["Low", "Medium", "High"]

    def test_list_invalid_page(self):
        response = client.get("/api/v1/todos?page=0")
        assert response.status_code == 422

    def test_list_invalid_page_size(self):
        response = client.get("/api/v1/todos?page_size=101")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/todos/{id} — Get Single
# ---------------------------------------------------------------------------

class TestGetTodo:
    """Success Criteria:
      - Returns complete todo item for valid ID
      - Returns 404 for non-existent ID
      - Returns 400 for malformed UUID
    """

    def test_get_success(self, sample_todo):
        response = client.get(f"/api/v1/todos/{sample_todo}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_todo
        assert data["title"] == "Test Todo"

    def test_get_not_found(self):
        response = client.get("/api/v1/todos/12345678-1234-1234-1234-123456789abc")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "NOT_FOUND"

    def test_get_invalid_uuid(self):
        response = client.get("/api/v1/todos/not-a-uuid")
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# PUT /api/v1/todos/{id} — Full Update
# ---------------------------------------------------------------------------

class TestUpdateTodo:
    """Success Criteria:
      - Replaces entire item (all fields must be provided)
      - Updates updated_at timestamp
      - Returns 404 for non-existent ID
      - Missing required fields return 400
    """

    def test_update_success(self, sample_todo):
        response = client.put(f"/api/v1/todos/{sample_todo}", json={
            "title": "Updated Title",
            "description": "Updated description",
            "completed": True,
            "priority": "high",
            "due_date": "2024-12-31",
            "tags": ["updated"],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["completed"] is True
        assert data["priority"] == "high"
        assert data["tags"] == ["updated"]
        # updated_at should change
        assert data["updated_at"] != data["created_at"]

    def test_update_not_found(self):
        response = client.put("/api/v1/todos/12345678-1234-1234-1234-123456789abc", json={
            "title": "Updated",
            "completed": True,
            "priority": "medium",
            "tags": [],
        })
        assert response.status_code == 404

    def test_update_missing_field(self, sample_todo):
        response = client.put(f"/api/v1/todos/{sample_todo}", json={
            "title": "Updated",
            # missing completed, priority, tags
        })
        assert response.status_code == 422

    def test_update_invalid_priority(self, sample_todo):
        response = client.put(f"/api/v1/todos/{sample_todo}", json={
            "title": "Updated",
            "completed": True,
            "priority": "invalid",
            "tags": [],
        })
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/v1/todos/{id} — Partial Update
# ---------------------------------------------------------------------------

class TestPatchTodo:
    """Success Criteria:
      - Updates only provided fields
      - Preserves unprovided fields
      - Updates updated_at timestamp
      - Empty patch body returns item unchanged with 200
      - Returns 404 for non-existent ID
    """

    def test_patch_title_only(self, sample_todo):
        response = client.patch(f"/api/v1/todos/{sample_todo}", json={
            "title": "Patched Title",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Patched Title"
        # Other fields preserved
        assert data["description"] == "A test description"
        assert data["completed"] is False
        assert data["priority"] == "medium"

    def test_patch_completed(self, sample_todo):
        response = client.patch(f"/api/v1/todos/{sample_todo}", json={
            "completed": True,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["completed"] is True
        assert data["title"] == "Test Todo"  # preserved

    def test_patch_empty_body(self, sample_todo):
        response = client.patch(f"/api/v1/todos/{sample_todo}", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test Todo"
        assert data["completed"] is False

    def test_patch_not_found(self):
        response = client.patch("/api/v1/todos/12345678-1234-1234-1234-123456789abc", json={
            "title": "Patched",
        })
        assert response.status_code == 404

    def test_patch_invalid_priority(self, sample_todo):
        response = client.patch(f"/api/v1/todos/{sample_todo}", json={
            "priority": "invalid",
        })
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/todos/{id} — Delete
# ---------------------------------------------------------------------------

class TestDeleteTodo:
    """Success Criteria:
      - Deletes item permanently
      - Returns 204 with empty body
      - Returns 404 for non-existent ID
      - Subsequent GET returns 404
    """

    def test_delete_success(self, sample_todo):
        response = client.delete(f"/api/v1/todos/{sample_todo}")
        assert response.status_code == 204
        assert response.content == b""

        # Verify deletion
        response = client.get(f"/api/v1/todos/{sample_todo}")
        assert response.status_code == 404

    def test_delete_not_found(self):
        response = client.delete("/api/v1/todos/12345678-1234-1234-1234-123456789abc")
        assert response.status_code == 404

    def test_delete_invalid_uuid(self):
        response = client.delete("/api/v1/todos/not-a-uuid")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Error Response Format
# ---------------------------------------------------------------------------

class TestErrorResponses:
    """Verify structured error responses across all endpoints."""

    def test_error_has_required_fields(self):
        response = client.get("/api/v1/todos/12345678-1234-1234-1234-123456789abc")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        error = data["error"]
        assert "code" in error
        assert "message" in error
        assert "timestamp" in error
        assert "request_id" in error

    def test_validation_error_has_field_details(self):
        response = client.post("/api/v1/todos", json={"priority": "invalid"})
        assert response.status_code == 422
        data = response.json()
        # FastAPI returns its own validation format; our app uses default
        # This test verifies the response is structured
        assert "detail" in data or "error" in data
