from django.db import migrations, models


class Migration(migrations.Migration):
    """Reverses 0002: a link is an invite that lets its holder submit their
    own URLs (like a normal user, just without an account), not a link
    bound to one fixed playlist chosen in advance — clarified after
    building 0002, which had gone the other way. See SharedImportLink's
    docstring and apps.sharing.views for the resulting guest-facing
    submission + status/downloads view.
    """

    dependencies = [
        ("sharing", "0002_link_bound_to_one_source_url"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="sharedimportlink",
            name="source_url",
        ),
        migrations.AddField(
            model_name="sharedimportlink",
            name="max_items_per_submission",
            field=models.PositiveIntegerField(default=25),
        ),
    ]
