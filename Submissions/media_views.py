from django.http import FileResponse, Http404, HttpResponseRedirect
from django.conf import settings
import os
import boto3
import logging

logger = logging.getLogger(__name__)


def serve_media(request, path):
    """
    Serve media files with support for both S3 and local storage.
    - If USE_S3=True: Redirect to signed S3 URL
    - If USE_S3=False: Serve from local disk
    Used for project documents and downloadable files
    """
    
    # If S3 is enabled, redirect to signed S3 URL
    if settings.USE_S3:
        try:
            s3_client = boto3.client('s3', region_name=settings.AWS_S3_REGION_NAME)
            
            # Construct S3 key (avoid double 'media/' prefix)
            s3_key = path if path.startswith('media/') else f'media/{path}'
            
            # Generate signed URL (valid for 1 hour)
            signed_url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': s3_key
                },
                ExpiresIn=3600  # 1 hour expiry
            )
            
            logger.info(f"Generated signed S3 URL for: media/{path}")
            return HttpResponseRedirect(signed_url)
            
        except Exception as e:
            logger.error(f"Error generating S3 signed URL for {path}: {str(e)}")
            raise Http404(f"File not found: {str(e)}")
    
    # Local disk fallback (for development/Docker Compose)
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    
    # Security: prevent directory traversal
    real_path = os.path.abspath(file_path)
    media_root = os.path.abspath(settings.MEDIA_ROOT)
    
    if not real_path.startswith(media_root):
        raise Http404("File not found")
    
    if not os.path.exists(file_path):
        raise Http404("File not found")
    
    if not os.path.isfile(file_path):
        raise Http404("File not found")
    
    try:
        response = FileResponse(open(file_path, 'rb'), as_attachment=True)
        response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
        return response
    except FileNotFoundError:
        raise Http404("File not found")
