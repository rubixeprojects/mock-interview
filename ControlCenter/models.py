from django.db import models
import hashlib
import os

class AdminAccount(models.Model):
    ROLE_CHOICES = (
        ('SuperAdmin', 'Super Admin'),
        ('Admin', 'Regular Admin'),
    )
    
    email = models.EmailField(unique=True, primary_key=True)
    name = models.CharField(max_length=255)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='Admin')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    def set_password(self, raw_password):
        salt = os.urandom(16).hex()
        hash_obj = hashlib.pbkdf2_hmac('sha256', raw_password.encode('utf-8'), salt.encode('utf-8'), 100000)
        self.password_hash = f"{salt}${hash_obj.hex()}"

    def check_password(self, raw_password):
        if not self.password_hash or '$' not in self.password_hash:
            return False
        salt, hash_hex = self.password_hash.split('$')
        hash_obj = hashlib.pbkdf2_hmac('sha256', raw_password.encode('utf-8'), salt.encode('utf-8'), 100000)
        return hash_obj.hex() == hash_hex

    def __str__(self):
        return f"{self.name} ({self.email})"

class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    admin_email = models.EmailField()
    action = models.CharField(max_length=50) 
    target_model = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100, null=True)
    details = models.JSONField() 
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp} - {self.admin_email} - {self.action}"
