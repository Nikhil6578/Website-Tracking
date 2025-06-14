# Generated by Django 3.0.5 on 2023-09-19 13:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('penseive', '0032_auto_20230810_1725'),
        ('website_tracking', '0013_auto_20230828_1540'),
    ]

    operations = [
        migrations.AddField(
            model_name='webclientsource',
            name='business_events',
            field=models.ManyToManyField(blank=True, to='penseive.BusinessEvent'),
        ),
        migrations.AddField(
            model_name='webclientsource',
            name='themes',
            field=models.ManyToManyField(blank=True, to='penseive.Theme'),
        ),
        migrations.AddField(
            model_name='webupdate',
            name='business_events',
            field=models.ManyToManyField(blank=True, to='penseive.BusinessEvent'),
        ),
        migrations.AddField(
            model_name='webupdate',
            name='themes',
            field=models.ManyToManyField(blank=True, to='penseive.Theme'),
        ),
        migrations.AlterField(
            model_name='webclientsource',
            name='language',
            field=models.CharField(blank=True, choices=[('ja', 'Japanese'), ('en', 'English'), ('ko', 'Korean'), ('zh-cn', 'Simplified Chinese'), ('zh-tw', 'Traditional Chinese'), ('fr', 'French'), ('th', 'Thai'), ('ar', 'Arabic'), ('es', 'Spanish'), ('pt', 'Portuguese'), ('de', 'German'), ('it', 'Italian'), ('bg', 'Bulgarian'), ('cs', 'Czech'), ('da', 'Danish'), ('el', 'Greek'), ('eu', 'Basque'), ('fa', 'Persian'), ('fi', 'Finnish'), ('ga', 'Irish'), ('gl', 'Galician'), ('hi', 'Hindi'), ('hu', 'Hungarian'), ('hy', 'Armenian'), ('lv', 'Latvian'), ('nl', 'Dutch'), ('no', 'Norwegian'), ('ro', 'Romanian'), ('ru', 'Russian'), ('sv', 'Swedish'), ('id', 'Indonesian'), ('tr', 'Turkish'), ('pl', 'Polish'), ('et', 'Estonian'), ('km', 'Khmer'), ('my', 'Burmese'), ('uk', 'Ukrainian'), ('sk', 'Slovak'), ('sr', 'Serbian'), ('vi', 'Vietnamese'), ('sl', 'Slovenian'), ('hr', 'Croatian')], max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='webupdate',
            name='language',
            field=models.CharField(blank=True, choices=[('ja', 'Japanese'), ('en', 'English'), ('ko', 'Korean'), ('zh-cn', 'Simplified Chinese'), ('zh-tw', 'Traditional Chinese'), ('fr', 'French'), ('th', 'Thai'), ('ar', 'Arabic'), ('es', 'Spanish'), ('pt', 'Portuguese'), ('de', 'German'), ('it', 'Italian'), ('bg', 'Bulgarian'), ('cs', 'Czech'), ('da', 'Danish'), ('el', 'Greek'), ('eu', 'Basque'), ('fa', 'Persian'), ('fi', 'Finnish'), ('ga', 'Irish'), ('gl', 'Galician'), ('hi', 'Hindi'), ('hu', 'Hungarian'), ('hy', 'Armenian'), ('lv', 'Latvian'), ('nl', 'Dutch'), ('no', 'Norwegian'), ('ro', 'Romanian'), ('ru', 'Russian'), ('sv', 'Swedish'), ('id', 'Indonesian'), ('tr', 'Turkish'), ('pl', 'Polish'), ('et', 'Estonian'), ('km', 'Khmer'), ('my', 'Burmese'), ('uk', 'Ukrainian'), ('sk', 'Slovak'), ('sr', 'Serbian'), ('vi', 'Vietnamese'), ('sl', 'Slovenian'), ('hr', 'Croatian')], db_index=True, max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='webupdate',
            name='status',
            field=models.IntegerField(choices=[(-1, 'Rejected'), (0, 'Draft'), (1, 'Pending'), (2, 'Published'), (5, 'On Hold'), (3, 'Pending Related Url'), (4, 'Incomplete Fetched'), (6, 'Source Ner'), (8, 'In Dedup Pending'), (9, 'In Dedup Published'), (10, 'Pending Custom Tagging'), (11, 'In Title Dedup Pending'), (12, 'In Title Dedup Published')], db_index=True, default=0),
        ),
    ]
