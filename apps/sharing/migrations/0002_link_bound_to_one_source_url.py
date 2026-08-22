from django.db import migrations, models


class Migration(migrations.Migration):
    """A shared link is now bound to one specific playlist/track chosen by
    the admin at creation time (apps.sharing.admin.SharedImportLinkAdmin.
    create_link), rather than letting the visitor submit arbitrary URLs —
    `max_items_per_submission` accordingly no longer applies (there is no
    visitor-side submission to cap; MAX_TRACKS_PER_SOURCE still caps how
    many tracks a single playlist resolves into, same as any other URL).

    The one existing row in any environment that reached this migration
    was pre-launch test data with maximum_uses=0 (permanently exhausted,
    never actually usable) — the one-off '' default below only exists to
    satisfy the NOT NULL backfill for that row; it is not a real URL.
    """

    dependencies = [
        ("sharing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sharedimportlink",
            name="source_url",
            field=models.URLField(
                default="",
                max_length=2048,
                help_text="The playlist/album/track visitors of this link will import. Fixed at creation time.",
            ),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="sharedimportlink",
            name="max_items_per_submission",
        ),
    ]
