# Generated migration for TeacherProfile payout fields and TeacherPayoutLedger audit fields

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_attendance_escrow_models'),
    ]

    operations = [
        # 1. Add payout account fields to TeacherProfile
        migrations.AddField(
            model_name='teacherprofile',
            name='payout_bank_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text='e.g. Commercial Bank of Ethiopia (CBE), Telebirr, Awash Bank',
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name='teacherprofile',
            name='payout_account_number',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Bank account number or mobile money number',
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name='teacherprofile',
            name='payout_account_holder',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Account holder legal full name',
                max_length=150,
            ),
        ),
        migrations.AddField(
            model_name='teacherprofile',
            name='payout_phone',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Contact telephone for payout confirmation',
                max_length=50,
            ),
        ),

        # 2. Add audit and reference fields to TeacherPayoutLedger
        migrations.AddField(
            model_name='teacherpayoutledger',
            name='disbursed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='disbursed_payout_ledgers',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='teacherpayoutledger',
            name='payout_reference',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Bank transaction reference, receipt number, or FT transfer code',
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name='teacherpayoutledger',
            name='payout_method',
            field=models.CharField(
                blank=True,
                default='BANK_TRANSFER',
                help_text='e.g. BANK_TRANSFER, TELEBIRR, CBE_BIRR, MANUAL',
                max_length=50,
            ),
        ),
    ]
