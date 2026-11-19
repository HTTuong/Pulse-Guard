"""Errors as Problem Details (RFC 9457) with a stable `code` (docs/api.md, Errors)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

# Database constraint name -> (status, code, title). A race that slips past a check in code
# still ends here, so it becomes a 409 with the right code and never a 500 (docs/database.md).
CONSTRAINT_CODES: dict[str, tuple[int, str, str]] = {
    "session_one_open_per_patient_uq": (
        409, "PATIENT_HAS_OPEN_SESSION", "Patient already has an open session"),
    "bed_stay_no_overlap_per_bed": (409, "BED_OCCUPIED", "Bed is occupied"),
    "patient_mrn_key": (409, "MRN_TAKEN", "Record number is already used"),
    "device_serial_key": (409, "SERIAL_TAKEN", "Serial number is already registered"),
    "app_user_email_key": (409, "EMAIL_TAKEN", "Email is already used"),
    "assignment_no_overlap_per_device": (
        409, "DEVICE_NOT_AVAILABLE", "Device is assigned to another session"),
}  # fmt: skip


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors


def not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", "Not found", f"No {what} with this id")


def forbidden(detail: str) -> ApiError:
    return ApiError(403, "FORBIDDEN", "Forbidden", detail)


def problem(error: ApiError) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"/problems/{error.code.lower().replace('_', '-')}",
        "title": error.title,
        "status": error.status,
        "code": error.code,
    }
    if error.detail:
        body["detail"] = error.detail
    if error.errors:
        body["errors"] = error.errors
    return JSONResponse(body, status_code=error.status, media_type=PROBLEM_JSON)


def field_name(location: tuple[Any, ...]) -> str:
    parts = [str(p) for p in location if p not in ("body", "path", "query")]
    return ".".join(parts) or "body"


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return problem(error)

    @app.exception_handler(RequestValidationError)
    def handle_validation(_: Request, error: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": field_name(tuple(e["loc"])), "message": str(e["msg"])} for e in error.errors()
        ]
        return problem(ApiError(400, "VALIDATION_FAILED", "Invalid request", errors=errors))

    @app.exception_handler(StarletteHTTPException)
    def handle_http(_: Request, error: StarletteHTTPException) -> JSONResponse:
        codes = {401: "UNAUTHENTICATED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        code = codes.get(error.status_code, f"HTTP_{error.status_code}")
        return problem(ApiError(error.status_code, code, str(error.detail)))

    @app.exception_handler(Exception)
    def handle_unexpected(_: Request, __: Exception) -> JSONResponse:
        # Never a stack trace or SQL in the body (docs/api.md); the server log has the details.
        return problem(ApiError(500, "INTERNAL", "Internal error"))
