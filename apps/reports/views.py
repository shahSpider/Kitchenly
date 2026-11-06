"""Heavy reports run on the read replica when REPLICA_DATABASE_URL is set."""
import zoneinfo
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import rls
from apps.core.api import TenantViewMixin
from apps.orders.models import Order, OrderItem, Payment


class BaseReportView(TenantViewMixin, APIView):
    resource = "report"
    branch_attr = None
    MAX_DAYS = 366

    def params(self, request):
        org = request.organization
        tz = zoneinfo.ZoneInfo(org.timezone)
        today = timezone.now().astimezone(tz).date()
        try:
            end = datetime.strptime(request.query_params["to"], "%Y-%m-%d").date() if "to" in request.query_params else today
            start = (datetime.strptime(request.query_params["from"], "%Y-%m-%d").date()
                     if "from" in request.query_params else end - timedelta(days=29))
        except ValueError:
            raise ValidationError("Dates must be YYYY-MM-DD.")
        if start > end or (end - start).days > self.MAX_DAYS:
            raise ValidationError(f"Range must be ordered and at most {self.MAX_DAYS} days.")
        allowed = request.access.branches_for("report", "read")
        branch = request.query_params.get("branch")
        branch_ids = None if allowed is None else set(allowed)
        if branch:
            if branch_ids is not None and branch not in {str(b) for b in branch_ids}:
                raise ValidationError({"branch": "Not available to you."})
            branch_ids = {branch}
        return tz, datetime.combine(start, time.min, tz), datetime.combine(end, time.max, tz), branch_ids

    def orders(self, alias, start, end, branch_ids):
        qs = Order.objects.using(alias).filter(placed_at__range=(start, end)).exclude(status="voided")
        return qs if branch_ids is None else qs.filter(branch_id__in=branch_ids)


class SalesSummaryView(BaseReportView):
    def get(self, request):
        tz, start, end, branch_ids = self.params(request)
        group = request.query_params.get("group_by", "day")
        if group not in ("day", "branch"):
            raise ValidationError({"group_by": "Use 'day' or 'branch'."})
        with rls.reporting_session() as alias:
            qs = self.orders(alias, start, end, branch_ids)
            totals = qs.aggregate(orders=Count("id"), gross=Sum("subtotal_minor"), tax=Sum("tax_minor"),
                                  discounts=Sum("discount_minor"), total=Sum("total_minor"))
            key = TruncDate("placed_at", tzinfo=tz) if group == "day" else F("branch_id")
            rows = list(qs.annotate(k=key).values("k").annotate(orders=Count("id"), total=Sum("total_minor"))
                        .order_by("k"))
        return Response({
            "currency": request.organization.currency, "from": start.date(), "to": end.date(),
            "totals": {k: (v or 0) for k, v in totals.items()},
            "groups": [{group: str(r["k"]), "orders": r["orders"], "total_minor": r["total"] or 0} for r in rows],
        })


class TopItemsView(BaseReportView):
    def get(self, request):
        _, start, end, branch_ids = self.params(request)
        limit = min(int(request.query_params.get("limit", 10) or 10), 100)
        with rls.reporting_session() as alias:
            order_ids = self.orders(alias, start, end, branch_ids).values("id")
            rows = (OrderItem.objects.using(alias).filter(order_id__in=order_ids)
                    .values("name_snapshot", "sku_snapshot")
                    .annotate(quantity=Sum("quantity"), revenue=Sum("line_total_minor")).order_by("-revenue")[:limit])
            data = list(rows)
        return Response({"currency": request.organization.currency, "items": [
            {"name": r["name_snapshot"], "sku": r["sku_snapshot"], "quantity": r["quantity"],
             "revenue_minor": r["revenue"]} for r in data]})


class PaymentMethodsView(BaseReportView):
    def get(self, request):
        _, start, end, branch_ids = self.params(request)
        with rls.reporting_session() as alias:
            order_ids = self.orders(alias, start, end, branch_ids).values("id")
            rows = list(Payment.objects.using(alias).filter(order_id__in=order_ids, status__in=["captured",
                        "partially_refunded"]).values("method").annotate(count=Count("id"), total=Sum("amount_minor"))
                        .order_by("-total"))
        return Response({"currency": request.organization.currency, "methods": [
            {"method": r["method"], "count": r["count"], "total_minor": r["total"]} for r in rows]})
