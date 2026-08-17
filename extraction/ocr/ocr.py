from pathlib import Path
import base64

def get_media_type(file_path: str) -> str:
    """Detects media type based on file extension."""
    ext = Path(file_path).suffix.lower()
    if ext in [".png"]:
        return "image/png"
    elif ext in [".pdf"]:
        return "application/pdf"
    elif ext in [".webp"]:
        return "image/webp"
    elif ext in [".gif"]:
        return "image/gif"
    return "image/jpeg"

def prepare_image_payload(file_path: str) -> tuple[str, str]:
    """Reads file and returns (base64_string, media_type)."""
    media_type = get_media_type(file_path)
    with open(file_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    return image_b64, media_type