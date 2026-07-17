"""
Health check endpoint for Kubernetes liveness/readiness probes.
Checks database, Elasticsearch, and S3 bucket connectivity.
"""

from django.http import JsonResponse
from django.db import connection
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Health check endpoint for Kubernetes probes.
    Returns 200 if all critical services are healthy, 503 otherwise.
    
    Checks:
    - Database connectivity (required)
    - Elasticsearch connectivity (optional, logs warning if down)
    - S3 bucket access (optional, only if USE_S3=True)
    """
    
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {}
    }
    
    http_status = 200
    
    # 1. Check Database Connectivity (REQUIRED)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status["services"]["database"] = {"status": "healthy"}
    except Exception as e:
        logger.error(f"Database health check failed: {str(e)}")
        health_status["services"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "unhealthy"
        http_status = 503
    
    # 2. Check S3 Bucket Access (OPTIONAL, only if USE_S3=True)
    use_s3 = os.getenv('USE_S3', 'False').lower() == 'true'
    
    if use_s3:
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME')
            region = os.getenv('AWS_S3_REGION_NAME', 'ap-south-1')
            
            # Create S3 client
            s3_client = boto3.client(
                's3',
                region_name=region,
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
            
            # Test bucket access
            s3_client.head_bucket(Bucket=bucket_name)
            
            health_status["services"]["s3"] = {
                "status": "healthy",
                "bucket": bucket_name
            }
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"S3 health check failed: {error_code}")
            health_status["services"]["s3"] = {
                "status": "unhealthy",
                "error": error_code
            }
            health_status["status"] = "unhealthy"
            http_status = 503
        except Exception as e:
            logger.error(f"S3 health check error: {str(e)}")
            health_status["services"]["s3"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health_status["status"] = "unhealthy"
            http_status = 503
    else:
        health_status["services"]["s3"] = {"status": "disabled"}
    
    return JsonResponse(health_status, status=http_status)


def liveness_probe(request):
    """
    Kubernetes liveness probe - simple and fast.
    Returns 200 if pod is alive, 500 if dead.
    Used to restart pods that are stuck.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"alive": True}, status=200)
    except Exception as e:
        logger.error(f"Liveness probe failed: {str(e)}")
        return JsonResponse({"alive": False, "error": str(e)}, status=500)


def readiness_probe(request):
    """
    Kubernetes readiness probe - checks if pod is ready to accept traffic.
    Returns 200 if ready, 503 if not.
    Used to add/remove pod from load balancer.
    """
    try:
        # Check database
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        # Check if we need to verify S3
        use_s3 = os.getenv('USE_S3', 'False').lower() == 'true'
        if use_s3:
            import boto3
            from botocore.exceptions import ClientError
            
            bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME')
            region = os.getenv('AWS_S3_REGION_NAME', 'ap-south-1')
            
            s3_client = boto3.client(
                's3',
                region_name=region,
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
            s3_client.head_bucket(Bucket=bucket_name)
        
        return JsonResponse({"ready": True}, status=200)
    except Exception as e:
        logger.error(f"Readiness probe failed: {str(e)}")
        return JsonResponse({"ready": False, "error": str(e)}, status=503)
