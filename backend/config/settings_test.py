"""测试用设置：不依赖 PostgreSQL，改用文件型 sqlite（跨连接/跨线程共享）。"""

from .settings import *  # noqa: F401,F403

from django.db.backends.signals import connection_created


def _sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")


connection_created.connect(_sqlite_pragmas)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "test_tanpit.sqlite3"),
        # immediate：并发写在 BEGIN 时排队（等 busy_timeout），避免 sqlite 读锁升级死锁；
        # 仅测试用，生产是 PostgreSQL，无此问题
        "OPTIONS": {"timeout": 30, "transaction_mode": "immediate"},
        "TEST": {"NAME": str(BASE_DIR / "test_tanpit_run.sqlite3")},
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR"}},
}
