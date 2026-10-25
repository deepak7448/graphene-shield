SECRET_KEY = "test-secret-key-that-is-long-enough-for-hs256"
DEBUG = False
USE_TZ = True
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "graphene_django",
    "tests.testapp",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ROOT_URLCONF = "tests.urls"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
GRAPHENE = {"SCHEMA": "tests.schema.schema"}
