import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('gh_permissions', '0009_issue_reset_inheritance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='issue',
            name='is_closed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='issue',
            name='blocked_by',
            field=models.ManyToManyField(
                blank=True, related_name='blocking', to='gh_permissions.issue',
            ),
        ),
        migrations.AlterModelOptions(
            name='issue',
            options={
                'permissions': (
                    ('triage_issue', 'Can triage issue'),
                    ('close_issue', 'Can close issue'),
                ),
            },
        ),
        migrations.CreateModel(
            name='IssueCloseGrant',
            fields=[
                ('id', models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID',
                )),
                ('issue', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='gh_permissions.issue',
                )),
                ('permission', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='auth.permission',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': (
                    models.UniqueConstraint(
                        fields=('user', 'issue'), name='unique_issue_close_grant',
                    ),
                ),
            },
        ),
    ]
