from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.admin_login_api, name='admin_login'),
    path('logout/', views.admin_logout_api, name='admin_logout'),
    path('status/', views.admin_status_api, name='admin_status'),
    path('admins/', views.manage_admins_api, name='manage_admins'),
    path('audit-logs/', views.audit_logs_api, name='audit_logs'),
]
