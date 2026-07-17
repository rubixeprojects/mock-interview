# Generated migration for course alias system
# Handles AI, AEN, AIE all mapping to AIE course

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0024_safe_checkpoint'),
    ]

    operations = [
        # Step 1: Create CourseAlias table
        migrations.CreateModel(
            name='CourseAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('alias', models.CharField(db_column='alias', max_length=10, unique=True)),
                ('course', models.ForeignKey(db_column='course_code', on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='Submissions.course', to_field='code')),
            ],
            options={
                'db_table': 'submissions_course_alias',
            },
        ),
        # Step 2: Add index on alias column
        migrations.AddIndex(
            model_name='coursealias',
            index=models.Index(fields=['alias'], name='submissions_c_alias_idx'),
        ),
        # Step 3: Insert course aliases
        # All these aliases (AI, AEN, AIE) map to the canonical AIE course
        migrations.RunPython(
            code=lambda apps, schema_editor: _insert_course_aliases(apps, schema_editor),
            reverse_code=lambda apps, schema_editor: None,  # No-op on rollback
        ),
    ]


def _insert_course_aliases(apps, schema_editor):
    """
    Insert course alias mappings.
    Maps AI, AEN, AIE all to the AIE course.
    
    These represent:
    - AI: Team ID format (from manage command)
    - AEN: Whitelist format (from Google Sheets via Apps Script)
    - AIE: Canonical course code (in database)
    """
    CourseAlias = apps.get_model('Submissions', 'CourseAlias')
    Course = apps.get_model('Submissions', 'Course')
    
    try:
        # Get or create AIE course
        aie_course, _ = Course.objects.get_or_create(
            code='AIE',
            defaults={'name': 'AI and Emerging Tech'}
        )
        
        # Create aliases: AI, AEN, AIE all map to AIE
        aliases = ['AI', 'AEN', 'AIE']
        for alias_code in aliases:
            CourseAlias.objects.get_or_create(
                alias=alias_code,
                defaults={'course': aie_course}
            )
        
        print(f"✅ Created {len(aliases)} course aliases for AIE")
    except Exception as e:
        print(f"⚠️  Error creating course aliases: {str(e)}")
        # Don't fail migration - aliases might already exist
        pass
