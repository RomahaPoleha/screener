from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'email_verified', 'can_view_stats', 'last_activity', 'created_at')
    list_filter = ('email_verified', 'can_view_stats')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at', 'verification_token', 'token_created')
    raw_id_fields = ('user',)
