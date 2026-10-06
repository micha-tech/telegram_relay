import contextvars
import uuid

request_id_var = contextvars.ContextVar("request_id", default="")

class RequestIDMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:64]
        token = request_id_var.set(request.request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            return response
        finally: request_id_var.reset(token)

