from rest_framework import serializers


class TenantSerializer(serializers.ModelSerializer):
    """organization is never taken from the payload; the view injects it."""

    @property
    def organization(self):
        return self.context["request"].organization
