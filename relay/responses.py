from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

def ok(data=None, message=None, status=200):
    return Response({"success": True, "data": data, "message": message}, status=status)

def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return Response({"success": False, "data": None, "message": "Internal server error", "error": {"code": "INTERNAL_ERROR"}}, status=500)
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    response.data = {"success": False, "data": None, "message": str(detail or "Request validation failed"), "error": {"code": getattr(exc, "default_code", "REQUEST_ERROR"), "details": response.data if not detail else None}}
    return response
