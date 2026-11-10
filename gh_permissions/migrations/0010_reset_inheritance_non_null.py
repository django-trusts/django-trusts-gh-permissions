# Issue.reset_inheritance was nullable. A NULL path condition is Unknown
# and stops the along= walk, which reversed the old "null inherits"
# behavior. Backfill NULL to false, then require the column. False
# continues the walk. True cuts grants inherited from ancestors.

from django.db import migrations, models


def backfill_null_reset_inheritance(apps, schema_editor):
    """Stored NULL inherits. Make that false before the column rejects NULL."""
    Issue = apps.get_model('gh_permissions', 'Issue')
    Issue.objects.using(schema_editor.connection.alias).filter(
        reset_inheritance__isnull=True,
    ).update(reset_inheritance=False)


class Migration(migrations.Migration):

    dependencies = [
        ('gh_permissions', '0009_issue_reset_inheritance'),
    ]

    operations = [
        migrations.RunPython(
            backfill_null_reset_inheritance,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='issue',
            name='reset_inheritance',
            field=models.BooleanField(default=False),
        ),
    ]
