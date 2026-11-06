from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.accounts import views as accounts
from apps.audit.views import AuditLogViewSet
from apps.core.views import healthz, readyz
from apps.menu import views as menu
from apps.orders import views as orders
from apps.pos import views as pos
from apps.pos.webhooks import PosWebhookView
from apps.reports import views as reports
from apps.tenants import views as tenants

router = DefaultRouter()
router.register("organizations", tenants.OrganizationViewSet, basename="organization")
router.register("branches", tenants.BranchViewSet, basename="branch")
router.register("users", accounts.UserViewSet, basename="user")
router.register("memberships", accounts.MembershipViewSet, basename="membership")
router.register("menu/categories", menu.MenuCategoryViewSet, basename="menu-category")
router.register("menu/items", menu.MenuItemViewSet, basename="menu-item")
router.register("branch-menu-items", menu.BranchMenuItemViewSet, basename="branch-menu-item")
router.register("orders", orders.OrderViewSet, basename="order")
router.register("refunds", orders.RefundViewSet, basename="refund")
router.register("pos/providers", pos.PosProviderViewSet, basename="pos-provider")
router.register("pos/connections", pos.PosConnectionViewSet, basename="pos-connection")
router.register("pos/sync-events", pos.SyncEventViewSet, basename="sync-event")
router.register("pos/imports", pos.ImportJobViewSet, basename="pos-import")
router.register("pos/reconciliations", pos.ReconciliationViewSet, basename="reconciliation")
router.register("audit-log", AuditLogViewSet, basename="audit-log")

api = [
    path("auth/login/", accounts.LoginView.as_view(), name="login"),
    path("auth/refresh/", accounts.RefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", accounts.LogoutView.as_view(), name="logout"),
    path("auth/me/", accounts.MeView.as_view(), name="me"),
    path("auth/mfa/setup/", accounts.MfaSetupView.as_view(), name="mfa-setup"),
    path("auth/mfa/confirm/", accounts.MfaConfirmView.as_view(), name="mfa-confirm"),
    path("auth/password/forgot/", accounts.PasswordForgotView.as_view(), name="password-forgot"),
    path("auth/password/reset/", accounts.PasswordResetView.as_view(), name="password-reset"),
    path("auth/password/change/", accounts.PasswordChangeView.as_view(), name="password-change"),
    path("roles/", accounts.RoleViewSet.as_view(), name="roles"),
    path("branches/<uuid:branch_id>/menu/", menu.BranchMenuView.as_view(), name="branch-menu"),
    path("reports/sales-summary/", reports.SalesSummaryView.as_view(), name="report-sales"),
    path("reports/top-items/", reports.TopItemsView.as_view(), name="report-top-items"),
    path("reports/payment-methods/", reports.PaymentMethodsView.as_view(), name="report-payments"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("healthz", healthz),
    path("readyz", readyz),
    path("admin/", admin.site.urls),
    path("api/v1/", include(api)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("webhooks/pos/<str:connection_id>/", PosWebhookView.as_view(), name="pos-webhook"),
]
