"""FastAPI application for Todo Items REST API."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from todo_api.models import (
    ErrorDetail,
    ErrorResponse,
    Priority,
    TodoItem,
    TodoItemCreate,
    TodoItemPatch,
    TodoItemUpdate,
    TodoListResponse,
)
from todo_api.storage import _storage

app = FastAPI(
    title="Todo API",
    description="REST API for managing todo items",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

def _make_error_response(code: str, message: str, request_id: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "error": {
            "code": code,
            "message": message,
            "timestamp": now,
            "request_id": request_id,
        }
    }


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    request_id = str(uuid.uuid4())
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_make_error_response("BAD_REQUEST", str(exc), request_id),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_make_error_response(
            "INTERNAL_SERVER_ERROR", "An unexpected error occurred.", request_id
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_uuid(item_id: str) -> None:
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_make_error_response(
                "BAD_REQUEST", f"Invalid UUID format: {item_id}", str(uuid.uuid4())
            ),
        )


def _priority_sort_key(p: Priority) -> int:
    mapping = {Priority.LOW: 0, Priority.MEDIUM: 1, Priority.HIGH: 2}
    return mapping.get(p, 1)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/todos", response_model=TodoItem, status_code=status.HTTP_201_CREATED)
def create_todo(data: TodoItemCreate, request: Request) -> TodoItem:
    """Create a new todo item."""
    item = _storage.create(data)
    request.state.response_headers = {"Location": f"/api/v1/todos/{item.id}"}
    return item


@app.get("/api/v1/todos", response_model=TodoListResponse)
def list_todos(
    completed: Optional[bool] = None,
    priority: Optional[Priority] = None,
    search: Optional[str] = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|priority|due_date)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> TodoListResponse:
    """List todo items with filtering, sorting, and pagination."""
    items: List[TodoItem] = _storage.list_all()

    # Filter by completed
    if completed is not None:
        items = [item for item in items if item.completed == completed]

    # Filter by priority
    if priority is not None:
        items = [item for item in items if item.priority == priority]

    # Search in title and description
    if search:
        search_lower = search.lower()
        items = [
            item
            for item in items
            if (item.title and search_lower in item.title.lower())
            or (item.description and search_lower in item.description.lower())
        ]

    # Sort
    reverse = sort_order == "desc"
    if sort_by == "priority":
        items.sort(key=lambda x: _priority_sort_key(x.priority), reverse=reverse)
    elif sort_by == "due_date":
        # Items without due_date go to the end
        items.sort(
            key=lambda x: (x.due_date is None, x.due_date or ""),
            reverse=reverse,
        )
    else:
        items.sort(key=lambda x: getattr(x, sort_by, ""), reverse=reverse)

    total = len(items)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    start = (page - 1) * page_size
    end = start + page_size
    paginated = items[start:end]

    return TodoListResponse(
        items=paginated,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@app.get("/api/v1/todos/{item_id}", response_model=TodoItem)
def get_todo(item_id: str) -> TodoItem:
    """Get a single todo item by ID."""
    _validate_uuid(item_id)
    item = _storage.get(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_make_error_response(
                "NOT_FOUND", f"Todo item not found: {item_id}", str(uuid.uuid4())
            ),
        )
    return item


@app.put("/api/v1/todos/{item_id}", response_model=TodoItem)
def update_todo(item_id: str, data: TodoItemUpdate) -> TodoItem:
    """Full update of a todo item."""
    _validate_uuid(item_id)
    item = _storage.update(item_id, data)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_make_error_response(
                "NOT_FOUND", f"Todo item not found: {item_id}", str(uuid.uuid4())
            ),
        )
    return item


@app.patch("/api/v1/todos/{item_id}", response_model=TodoItem)
def patch_todo(item_id: str, data: TodoItemPatch) -> TodoItem:
    """Partial update of a todo item."""
    _validate_uuid(item_id)
    item = _storage.patch(item_id, data)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_make_error_response(
                "NOT_FOUND", f"Todo item not found: {item_id}", str(uuid.uuid4())
            ),
        )
    return item


@app.delete("/api/v1/todos/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(item_id: str) -> None:
    """Delete a todo item."""
    _validate_uuid(item_id)
    deleted = _storage.delete(item_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_make_error_response(
                "NOT_FOUND", f"Todo item not found: {item_id}", str(uuid.uuid4())
            ),
        )


# ---------------------------------------------------------------------------
# Middleware to inject Location header on create
# ---------------------------------------------------------------------------

@app.middleware("http")
async def location_header_middleware(request: Request, call_next):
    response = await call_next(request)
    location = getattr(request.state, "response_headers", {}).get("Location")
    if location:
        response.headers["Location"] = location
    return response
