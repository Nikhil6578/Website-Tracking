# Generated by Django 3.0.5 on 2021-01-25 20:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website_tracking', '0003_auto_20210122_1423'),
    ]

    operations = [
        migrations.AlterField(
            model_name='webupdate',
            name='status',
            field=models.IntegerField(choices=[(-1, 'Rejected'), (0, 'Draft'), (1, 'Pending'), (2, 'Published'), (5, 'On Hold'), (3, 'Pending Related Url'), (4, 'Incomplete Fetched'), (6, 'Source Ner')], db_index=True, default=0),
        ),
    ]
