import pytest
from django.core.exceptions import ImproperlyConfigured

from graphene_shield.conf import shield_settings


def test_misspelled_setting_is_an_error(settings):
    settings.GRAPHENE_SHIELD = {"EXPOSE_EXCEPTION_MESSAGE": True}  # missing "S"
    with pytest.raises(ImproperlyConfigured, match="EXPOSE_EXCEPTION_MESSAGE"):
        _ = shield_settings.EXPOSE_EXCEPTION_MESSAGES
