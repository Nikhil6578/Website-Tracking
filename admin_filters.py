
from django.contrib.admin import SimpleListFilter
from django.utils.translation import ugettext_lazy as _

from contify.subscriber.models import CompanyPreferences
from contify.website_tracking.models import WebClientSource


def get_client_map(client_id=None):
    if not hasattr(get_client_map, "client_map"):
        get_client_map.client_map = dict(
            CompanyPreferences.objects
            .filter(company__active=True, active=True)
            .values_list("id", "company__name")
            .order_by("company__name")
        )

    if client_id:
        return get_client_map.client_map.get(client_id)

    return get_client_map.client_map


class ClientFilter(SimpleListFilter):
    title = _("Client")
    # Parameter for the filter that will be used in the URL query.
    parameter_name = 'client_id'

    def lookups(self, request, model_admin):
        return tuple(get_client_map().items())

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(client_id=self.value())


class DiffContentClientFilter(SimpleListFilter):
    title = _("Client")
    # Parameter for the filter that will be used in the URL query.
    parameter_name = 'client_id'

    def lookups(self, request, model_admin):
        return tuple(get_client_map().items())

    def queryset(self, request, queryset):
        if self.value():
            web_source_ids = set(
                WebClientSource.objects
                .filter(client_id=self.value())
                .values_list("source_id", flat=True)
            )
            if not web_source_ids:
                return queryset.none()

            return queryset.filter(
                new_snapshot__web_source_id__in=web_source_ids
            )


class DiffContentWebSourceFilter(SimpleListFilter):
    title = _("WebSource")

    # Parameter for the filter that will be used in the URL query.
    parameter_name = 'websource_id'

    def lookups(self, request, model_admin):
        return

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(
                new_snapshot__web_source_id=self.value()
            )


class SourceClientFilter(SimpleListFilter):
    title = _("Client")
    # Parameter for the filter that will be used in the URL query.
    parameter_name = 'client_id'

    def lookups(self, request, model_admin):
        return tuple(get_client_map().items())

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(webclientsource__client_id=self.value())
