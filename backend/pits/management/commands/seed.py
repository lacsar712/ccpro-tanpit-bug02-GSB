from django.core.management.base import BaseCommand

from pits.seed import seed_demo


class Command(BaseCommand):
    help = "写入鞣场示范数据"

    def handle(self, *args, **options):
        seed_demo()
        self.stdout.write("seed done")
