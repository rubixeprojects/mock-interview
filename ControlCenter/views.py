from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json
from .models import AdminAccount, AuditLog

@csrf_exempt
def admin_login_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip().lower()
            password = data.get('password', '')
            
            admin = AdminAccount.objects.filter(email=email, is_active=True).first()
            if admin and admin.check_password(password):
                request.session['admin_email'] = admin.email
                request.session['admin_role'] = admin.role
                request.session['is_admin'] = True  # Backward compatibility
                admin.last_login = timezone.now()
                admin.save(update_fields=['last_login'])
                
                log_admin_action(admin.email, "LOGIN", "AdminAccount", admin.email, {"message": "Logged in"})
                
                return JsonResponse({
                    "success": True,
                    "admin": {
                        "email": admin.email,
                        "name": admin.name,
                        "role": admin.role
                    }
                })
            return JsonResponse({"success": False, "error": "Invalid email or password"}, status=401)
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)
    return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)

@csrf_exempt
def admin_logout_api(request):
    email = request.session.get('admin_email')
    if email:
        log_admin_action(email, "LOGOUT", "AdminAccount", email, {})
    request.session.flush()
    return JsonResponse({"success": True})

def admin_status_api(request):
    email = request.session.get('admin_email')
    if email:
        admin = AdminAccount.objects.filter(email=email).first()
        if admin and admin.is_active:
            return JsonResponse({
                "authenticated": True,
                "admin": {
                    "email": admin.email,
                    "name": admin.name,
                    "role": admin.role
                }
            })
    return JsonResponse({"authenticated": False})

@csrf_exempt
def manage_admins_api(request):
    # Only SuperAdmins can manage other admins
    curr_email = request.session.get('admin_email')
    curr_role = request.session.get('admin_role')
    
    if not curr_email or curr_role != 'SuperAdmin':
        return JsonResponse({"success": False, "error": "Unauthorized"}, status=403)
    
    if request.method == 'GET':
        admins = list(AdminAccount.objects.all().values('email', 'name', 'role', 'is_active', 'created_at', 'last_login'))
        return JsonResponse({"success": True, "admins": admins})
    
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip().lower()
            name = data.get('name', '')
            password = data.get('password')
            role = data.get('role', 'Admin')
            is_active = data.get('is_active', True)
            
            admin, created = AdminAccount.objects.update_or_create(
                email=email,
                defaults={
                    "name": name,
                    "role": role,
                    "is_active": is_active
                }
            )
            if password:
                admin.set_password(password)
                admin.save()
            
            action = "CREATE_ADMIN" if created else "UPDATE_ADMIN"
            log_admin_action(curr_email, action, "AdminAccount", email, {"role": role, "is_active": is_active})
            
            return JsonResponse({"success": True, "message": f"Admin {email} {'created' if created else 'updated'}"})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)
            
    return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)

@csrf_exempt
def audit_logs_api(request):
    curr_email = request.session.get('admin_email')
    if not curr_email:
        return JsonResponse({"success": False, "error": "Unauthorized"}, status=403)
    
    logs = list(AuditLog.objects.all()[:200].values()) # Last 200 logs
    return JsonResponse({"success": True, "logs": logs})


# Helper Utility
def log_admin_action(admin_email, action, target_model, target_id, details, request=None):
    ip = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
            
    AuditLog.objects.create(
        admin_email=admin_email,
        action=action,
        target_model=target_model,
        target_id=str(target_id),
        details=details,
        ip_address=ip
    )
