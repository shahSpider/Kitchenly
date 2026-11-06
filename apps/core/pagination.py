from rest_framework.pagination import CursorPagination, LimitOffsetPagination


class DefaultPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200


class PlacedAtCursorPagination(CursorPagination):
    """Keyset pagination for big append-heavy tables: O(1) at any depth."""
    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = ("-placed_at", "-id")


class ReceivedAtCursorPagination(PlacedAtCursorPagination):
    ordering = ("-received_at", "-id")


class CreatedAtCursorPagination(PlacedAtCursorPagination):
    ordering = ("-created_at", "-id")
