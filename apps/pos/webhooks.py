"""Webhook receiver: verify, store the raw event, answer 200 fast. All real
work happens later in a Celery worker."""
import logging
import uuid

from django.conf import settings
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.core import rls

from . import services
from .adapters.base import AdapterError, WebhookRejected, get_adapter
from .models import PosConnection

log = logging.getLogger(__name__)


class WebhookThrottle(SimpleRateThrottle):
    scope = "webhook"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": view.kwargs.get("connection_id")}


class PosWebhookView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [WebhookThrottle]
    parser_classes = []  # we need the raw body for signature verification

    def post(self, request, connection_id):
        raw = request._request
        if len(raw.body) > settings.WEBHOOK_MAX_BODY_BYTES:
            return Response({"detail": "Payload too large."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        try:
            cid = uuid.UUID(str(connection_id))
        except ValueError:
            raise Http404
        with rls.bypass():  # system lookup by id; the org then comes from the connection itself
            conn = (PosConnection.all_objects.select_related("provider", "branch", "organization")
                    .filter(pk=cid).first())
        if conn is None or conn.status != "active" or conn.sync_mode != "webhook" or not conn.provider.is_active \
                or conn.organization.status != "active":
            raise Http404
        try:
            adapter = get_adapter(conn.provider.code)
            if not adapter.verify_webhook(raw, conn):
                return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)
            events = adapter.parse_webhook(raw, conn)
        except WebhookRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        except AdapterError:
            raise Http404
        with rls.tenant_context(conn.organization_id):
            ids = services.store_raw_events(conn, events, "webhook")
            services.enqueue_processing(ids, "webhook")
        return Response({"received": len(events), "queued": len(ids)})
