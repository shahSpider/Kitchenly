from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


class Conflict(Exception):
    pass


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
    if isinstance(exc, IntegrityError):
        return Response({"detail": "Conflicts with an existing record."}, status=status.HTTP_409_CONFLICT)
    response = drf_handler(exc, context)
    return response
