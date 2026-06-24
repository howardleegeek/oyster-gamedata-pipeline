#!/usr/bin/env python3
"""
S3 Presigned URL Backend Endpoint

Provides presigned URLs for multipart S3 uploads with automatic URL refresh
on expiry.
"""

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from flask import Flask, jsonify, request

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
S3_BUCKET = os.environ.get("S3_BUCKET", "oyster-clips")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
URL_EXPIRY_SECONDS = int(os.environ.get("URL_EXPIRY_SECONDS", "3600"))
MAX_CHUNKS = 10000

# In-memory store for multipart uploads (in production, use Redis or database)
multipart_uploads: Dict[str, Dict[str, Any]] = {}


@dataclass
class MultipartUpload:
    """Represents a multipart upload in progress."""
    upload_id: str
    session_id: str
    file_size: int
    sha256: str
    created_at: float
    expires_at: float
    parts: Dict[int, Dict[str, str]] = field(default_factory=dict)
    completed: bool = False
    
    def is_expired(self) -> bool:
        """Check if the upload has expired."""
        return time.time() > self.expires_at
    
    def add_part(self, part_number: int, etag: str):
        """Record a successfully uploaded part."""
        self.parts[part_number] = {"ETag": etag}
    
    def get_parts(self) -> List[Dict]:
        """Get list of parts for completion."""
        return [
            {"PartNumber": num, "ETag": data["ETag"]}
            for num, data in sorted(self.parts.items())
        ]


def generate_presigned_url(upload_id: str, part_number: int, session_id: str) -> str:
    """
    Generate a presigned URL for uploading a part.
    
    In production, this would use boto3 to generate actual S3 presigned URLs.
    For this implementation, we generate mock URLs that include the necessary
    parameters for the upload.
    """
    # Generate a signed URL with embedded credentials
    # In production, use boto3's generate_presigned_url
    
    expiry = int(time.time() + URL_EXPIRY_SECONDS)
    
    # Create a mock presigned URL
    # Format: endpoint/bucket/session_id/upload_id/part_number?expiry=...&signature=...
    signature = hashlib.sha256(
        f"{session_id}:{upload_id}:{part_number}:{expiry}:{S3_SECRET_KEY}".encode()
    ).hexdigest()[:16]
    
    url = (f"{S3_ENDPOINT}/{S3_BUCKET}/uploads/{session_id}/"
           f"{upload_id}/part/{part_number}?"
           f"X-Amz-Algorithm=AWS4-HMAC-SHA256&"
           f"X-Amz-Credential={S3_ACCESS_KEY}/{S3_REGION}/s3/aws4_request&"
           f"X-Amz-Date={datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}&"
           f"X-Amz-Expires={URL_EXPIRY_SECONDS}&"
           f"X-Amz-SignedHeaders=host&"
           f"X-Amz-Signature={signature}")
    
    return url


def refresh_presigned_url(upload_id: str, part_number: int, session_id: str) -> str:
    """
    Refresh a presigned URL, generating a new one with fresh expiry.
    This is called when the original URL is about to expire or has expired.
    """
    return generate_presigned_url(upload_id, part_number, session_id)


@app.route("/api/upload/init", methods=["POST"])
def init_multipart():
    """
    Initialize a multipart upload.
    
    Request body:
    {
        "session_id": "clip-20260517-150000",
        "file_size": 1073741824,
        "sha256": "abc123..."
    }
    
    Response:
    {
        "upload_id": "uuid",
        "session_id": "clip-20260517-150000",
        "chunk_size": 8388608,
        "total_chunks": 128,
        "expires_at": 1234567890
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Missing request body"}), 400
        
        session_id = data.get("session_id")
        file_size = data.get("file_size")
        sha256 = data.get("sha256")
        
        if not session_id:
            return jsonify({"error": "Missing session_id"}), 400
        if not file_size or file_size <= 0:
            return jsonify({"error": "Invalid file_size"}), 400
        if not sha256:
            return jsonify({"error": "Missing sha256"}), 400
        
        # Check if upload already exists and is valid
        if session_id in multipart_uploads:
            existing = multipart_uploads[session_id]
            if not existing.is_expired() and not existing.completed:
                # Return existing upload info
                return jsonify({
                    "upload_id": existing.upload_id,
                    "session_id": session_id,
                    "chunk_size": 8 * 1024 * 1024,
                    "total_chunks": (file_size + 8 * 1024 * 1024 - 1) // (8 * 1024 * 1024),
                    "expires_at": existing.expires_at,
                    "message": "Using existing upload"
                })
        
        # Create new multipart upload
        upload_id = str(uuid.uuid4())
        created_at = time.time()
        expires_at = created_at + URL_EXPIRY_SECONDS
        
        upload = MultipartUpload(
            upload_id=upload_id,
            session_id=session_id,
            file_size=file_size,
            sha256=sha256,
            created_at=created_at,
            expires_at=expires_at
        )
        
        multipart_uploads[session_id] = upload
        
        chunk_size = 8 * 1024 * 1024  # 8MB
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        
        logger.info(f"Initialized multipart upload: {session_id} ({upload_id}), "
                   f"{total_chunks} chunks")
        
        return jsonify({
            "upload_id": upload_id,
            "session_id": session_id,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "expires_at": expires_at
        })
        
    except Exception as e:
        logger.error(f"Error initializing multipart upload: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload/url", methods=["POST"])
def get_presigned_url():
    """
    Get a presigned URL for uploading a chunk.
    
    Request body:
    {
        "session_id": "clip-20260517-150000",
        "upload_id": "uuid",
        "part_number": 1
    }
    
    Response:
    {
        "url": "https://...",
        "expires_at": 1234567890
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Missing request body"}), 400
        
        session_id = data.get("session_id")
        upload_id = data.get("upload_id")
        part_number = data.get("part_number")
        
        if not session_id:
            return jsonify({"error": "Missing session_id"}), 400
        if not upload_id:
            return jsonify({"error": "Missing upload_id"}), 400
        if not part_number or part_number < 1:
            return jsonify({"error": "Invalid part_number"}), 400
        
        # Find the upload
        if session_id not in multipart_uploads:
            return jsonify({"error": "Upload not found"}), 404
        
        upload = multipart_uploads[session_id]
        
        # Verify upload_id matches
        if upload.upload_id != upload_id:
            return jsonify({"error": "Upload ID mismatch"}), 400
        
        # Check if expired
        if upload.is_expired():
            # Refresh the upload
            upload.expires_at = time.time() + URL_EXPIRY_SECONDS
            logger.info(f"Refreshed expired upload: {session_id}")
        
        # Check if part already uploaded (for resume)
        if part_number in upload.parts:
            # Return URL anyway - client can use it to verify/retry
            pass
        
        # Generate presigned URL
        url = generate_presigned_url(upload_id, part_number, session_id)
        
        return jsonify({
            "url": url,
            "expires_at": upload.expires_at,
            "part_number": part_number
        })
        
    except Exception as e:
        logger.error(f"Error getting presigned URL: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload/complete", methods=["POST"])
def complete_multipart():
    """
    Complete a multipart upload.
    
    Request body:
    {
        "session_id": "clip-20260517-150000",
        "upload_id": "uuid",
        "parts": [
            {"PartNumber": 1, "ETag": "etag1"},
            {"PartNumber": 2, "ETag": "etag2"},
            ...
        ]
    }
    
    Response:
    {
        "session_id": "clip-20260517-150000",
        "sha256": "abc123...",
        "location": "https://..."
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Missing request body"}), 400
        
        session_id = data.get("session_id")
        upload_id = data.get("upload_id")
        parts = data.get("parts", [])
        
        if not session_id:
            return jsonify({"error": "Missing session_id"}), 400
        if not upload_id:
            return jsonify({"error": "Missing upload_id"}), 400
        if not parts:
            return jsonify({"error": "No parts provided"}), 400
        
        # Find the upload
        if session_id not in multipart_uploads:
            return jsonify({"error": "Upload not found"}), 404
        
        upload = multipart_uploads[session_id]
        
        # Verify upload_id matches
        if upload.upload_id != upload_id:
            return jsonify({"error": "Upload ID mismatch"}), 400
        
        # Record all parts
        for part in parts:
            part_number = part.get("PartNumber")
            etag = part.get("ETag")
            if part_number and etag:
                upload.add_part(part_number, etag)
        
        # Mark as completed
        upload.completed = True
        
        # In production, this would call S3's complete_multipart_upload
        # and verify the final object's SHA256
        
        location = f"{S3_BUCKET}/uploads/{session_id}/{upload_id}"
        
        logger.info(f"Completed multipart upload: {session_id}, "
                   f"{len(upload.parts)} parts")
        
        return jsonify({
            "session_id": session_id,
            "upload_id": upload_id,
            "sha256": upload.sha256,
            "location": location,
            "parts_count": len(upload.parts)
        })
        
    except Exception as e:
        logger.error(f"Error completing multipart upload: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload/abort", methods=["POST"])
def abort_multipart():
    """
    Abort a multipart upload.
    
    Request body:
    {
        "session_id": "clip-20260517-150000",
        "upload_id": "uuid"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Missing request body"}), 400
        
        session_id = data.get("session_id")
        upload_id = data.get("upload_id")
        
        if not session_id or not upload_id:
            return jsonify({"error": "Missing session_id or upload_id"}), 400
        
        if session_id in multipart_uploads:
            upload = multipart_uploads[session_id]
            if upload.upload_id == upload_id:
                del multipart_uploads[session_id]
                logger.info(f"Aborted multipart upload: {session_id}")
                return jsonify({"message": "Upload aborted"})
        
        return jsonify({"error": "Upload not found"}), 404
        
    except Exception as e:
        logger.error(f"Error aborting multipart upload: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload/status/<session_id>", methods=["GET"])
def get_upload_status(session_id: str):
    """Get the status of an upload."""
    if session_id not in multipart_uploads:
        return jsonify({"error": "Upload not found"}), 404
    
    upload = multipart_uploads[session_id]
    
    return jsonify({
        "session_id": session_id,
        "upload_id": upload.upload_id,
        "file_size": upload.file_size,
        "sha256": upload.sha256,
        "parts_count": len(upload.parts),
        "completed": upload.completed,
        "expires_at": upload.expires_at,
        "is_expired": upload.is_expired()
    })


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "active_uploads": len(multipart_uploads),
        "timestamp": datetime.utcnow().isoformat()
    })


def cleanup_expired_uploads():
    """Clean up expired uploads periodically."""
    expired = [
        session_id for session_id, upload in multipart_uploads.items()
        if upload.is_expired() and not upload.completed
    ]
    
    for session_id in expired:
        del multipart_uploads[session_id]
        logger.info(f"Cleaned up expired upload: {session_id}")
    
    return len(expired)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    
    logger.info(f"Starting S3 presigned URL server on port {port}")
    logger.info(f"S3 endpoint: {S3_ENDPOINT}")
    logger.info(f"S3 bucket: {S3_BUCKET}")
    
    app.run(host="0.0.0.0", port=port, debug=debug)
