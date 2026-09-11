# Generated migration for SupportedBank model

import uuid
from django.db import migrations, models


def seed_initial_supported_banks(apps, schema_editor):
    SupportedBank = apps.get_model('users', 'SupportedBank')
    initial_banks = [
        ("Commercial Bank of Ethiopia (CBE)", "CBE", "BANK", "Account Number", 1),
        ("Telebirr", "TELEBIRR", "MOBILE_WALLET", "Phone Number", 2),
        ("Awash Bank", "AWASH", "BANK", "Account Number", 3),
        ("Dashen Bank", "DASHEN", "BANK", "Account Number", 4),
        ("Bank of Abyssinia", "BOA", "BANK", "Account Number", 5),
        ("Wegagen Bank", "WEGAGEN", "BANK", "Account Number", 6),
        ("Hibret Bank", "HIBRET", "BANK", "Account Number", 7),
        ("Cooperative Bank of Oromia (Coop)", "COOP", "BANK", "Account Number", 8),
        ("Zemen Bank", "ZEMEN", "BANK", "Account Number", 9),
    ]
    for name, code, p_type, label, order in initial_banks:
        SupportedBank.objects.get_or_create(
            name=name,
            defaults={
                "code": code,
                "provider_type": p_type,
                "account_number_label": label,
                "display_order": order,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0023_teacher_payout_and_ledger_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupportedBank',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(help_text='Display name, e.g. Commercial Bank of Ethiopia (CBE)', max_length=120, unique=True)),
                ('code', models.CharField(blank=True, help_text='Short identifier or code, e.g. CBE, TELEBIRR', max_length=50)),
                ('provider_type', models.CharField(choices=[('BANK', 'Commercial Bank'), ('MOBILE_WALLET', 'Mobile Wallet / Telebirr'), ('OTHER', 'Other Authorized Provider')], default='BANK', max_length=30)),
                ('account_number_label', models.CharField(default='Account Number', help_text='Placeholder/label, e.g. Account Number or Phone Number', max_length=80)),
                ('is_active', models.BooleanField(default=True, help_text='Whether teachers can select this bank for payouts')),
                ('display_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Supported Bank',
                'verbose_name_plural': 'Supported Banks',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.RunPython(seed_initial_supported_banks, reverse_code=migrations.RunPython.noop),
    ]
