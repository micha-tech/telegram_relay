from rest_framework.pagination import PageNumberPagination


class SignalsPagination(PageNumberPagination):
    """Page number pagination with a client-controlled page size."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
