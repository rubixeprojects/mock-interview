from django.contrib import admin
from django.apps import apps
from .models import (
    ProjectRegistry, Team, StudentIdentity,
    S3ToLocalMoveLog, LocalFileDeletionLog, CapstoneClientCompletionLog,
    RegistrationWhitelist
)

# Custom admin for ProjectRegistry
@admin.register(ProjectRegistry)
class ProjectRegistryAdmin(admin.ModelAdmin):
    list_display = ('project_id', 'course', 'project_name', 'project_type')
    list_filter = ('course', 'project_type')
    search_fields = ('project_id', 'project_name', 'course')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('project_id', 'course', 'project_name', 'project_type')
        }),
        ('Documentation & Data', {
            'fields': ('project_document',),
            'description': 'Upload project spec/doc'
        }),
        ('Configuration', {
            'fields': ('llm_input_prompt', 'expected_submission'),
            'classes': ('collapse',)
        }),
    )

# Custom admin for StudentIdentity
@admin.register(StudentIdentity)
class StudentIdentityAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'phone')
    search_fields = ('email', 'name')
    list_filter = ('email',)

# Custom admin for Team
@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('team_id', 'student', 'team_count', 'team_preference')
    search_fields = ('team_id', 'student__email', 'student__name')
    list_filter = ('team_preference', 'team_count')
    raw_id_fields = ('student',)  # ← Use search field instead of dropdown
    # team_id is a FK target (Student.TeamID references it via ON UPDATE CASCADE).
    # Editing it here would cascade-update every member's TeamID.
    # Changes must go through the per-student student_update_api instead.
    readonly_fields = ('team_id',)


# ============================================================================
# DAG OPERATION LOGS - Custom Admin Classes
# ============================================================================

@admin.register(S3ToLocalMoveLog)
class S3ToLocalMoveLogAdmin(admin.ModelAdmin):
    """Admin interface for S3 to Local file movement logs (DAG 1)"""
    list_display = ('team_id', 'project_id', 'status', 'timestamp', 'file_size_bytes')
    list_filter = ('status', 'timestamp')
    search_fields = ('team_id', 'project_id', 'original_s3_path', 'local_path')
    readonly_fields = ('timestamp', 'original_s3_path', 'local_path')
    
    fieldsets = (
        ('File Identifiers', {
            'fields': ('submission', 'team_id', 'project_id')
        }),
        ('Path Information', {
            'fields': ('original_s3_path', 'local_path')
        }),
        ('File Metadata', {
            'fields': ('file_size_bytes', 'attempt_count')
        }),
        ('Status & Timestamp', {
            'fields': ('status', 'timestamp', 'error_message')
        }),
    )


@admin.register(LocalFileDeletionLog)
class LocalFileDeletionLogAdmin(admin.ModelAdmin):
    """Admin interface for local file deletion logs (DAG 2)"""
    list_display = ('move_log', 'status', 'deleted_at')
    list_filter = ('status', 'deleted_at')
    search_fields = ('local_path', 'move_log__team_id', 'move_log__project_id')
    readonly_fields = ('deleted_at', 'local_path')
    
    fieldsets = (
        ('File Reference', {
            'fields': ('move_log', 'local_path')
        }),
        ('Deletion Status', {
            'fields': ('status', 'deleted_at', 'error_message')
        }),
    )


@admin.register(CapstoneClientCompletionLog)
class CapstoneClientCompletionLogAdmin(admin.ModelAdmin):
    """Admin interface for completed projects CSV logs (DAG 3)"""
    list_display = ('student_email', 'team_id', 'status', 'generated_at')
    list_filter = ('status', 'generated_at')
    search_fields = ('student_email', 'student_name', 'team_id', 'batch_id')
    readonly_fields = ('generated_at', 'student_email', 'student_name', 'team_id')
    
    fieldsets = (
        ('Student Information', {
            'fields': ('student', 'student_email', 'student_name', 'team_id')
        }),
        ('CSV Details', {
            'fields': ('csv_s3_key', 'batch_id')
        }),
        ('Status & Timestamp', {
            'fields': ('status', 'generated_at', 'error_message')
        }),
    )


# Custom admin for RegistrationWhitelist
@admin.register(RegistrationWhitelist)
class RegistrationWhitelistAdmin(admin.ModelAdmin):
    list_display = ('email', 'course', 'added_at')
    list_filter = ('course', 'added_at')
    search_fields = ('email', 'course')
    readonly_fields = ('added_at',)
    
    fieldsets = (
        ('Registration Entry', {
            'fields': ('email', 'course')
        }),
        ('Metadata', {
            'fields': ('added_at',),
            'classes': ('collapse',)
        }),
    )


# Get all other models from current app and register them
app_models = apps.get_app_config('Submissions').get_models()
for model in app_models:
    # Skip models we already registered
    if model.__name__ in [
        'ProjectRegistry', 'Team', 'StudentIdentity',
        'S3ToLocalMoveLog', 'LocalFileDeletionLog', 'CapstoneClientCompletionLog',
        'RegistrationWhitelist'
    ]:
        continue
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
