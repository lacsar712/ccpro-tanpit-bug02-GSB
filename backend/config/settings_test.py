"""测试用配置：sqlite 即可，无需本机 Postgres。"""

from config.settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
