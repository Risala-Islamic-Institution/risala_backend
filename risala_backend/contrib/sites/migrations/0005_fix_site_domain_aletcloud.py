"""
Databases that already applied 0003 have the placeholder domain "risala.com",
which the project doesn't own. Re-run the update so the Site row points at the
real aletcloud domain (used in account/password-reset email links).
"""
from django.conf import settings
from django.db import migrations


def update_site_forward(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.update_or_create(
        id=settings.SITE_ID,
        defaults={
            "domain": "risala.app.aletcloud.com",
            "name": "Risala",
        },
    )


def update_site_backward(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.update_or_create(
        id=settings.SITE_ID,
        defaults={
            "domain": "risala.com",
            "name": "Risala_Backend",
        },
    )


class Migration(migrations.Migration):

    dependencies = [("sites", "0004_alter_options_ordering_domain")]

    operations = [migrations.RunPython(update_site_forward, update_site_backward)]
