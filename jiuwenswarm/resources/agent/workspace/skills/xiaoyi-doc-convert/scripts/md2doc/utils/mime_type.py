"""MIME type utilities for md2doc skill."""


def get_file_extension(url: str) -> str:
    """Extract file extension from URL.

    Args:
        url: URL string

    Returns:
        Lowercase file extension without dot
    """
    base_url = url.split('?')[0]
    parts = base_url.split('.')
    if len(parts) > 1:
        return parts[-1].lower()
    return ""


def get_mime_type_from_url(image_url: str) -> str:
    """Get MIME type from image URL based on extension.

    Args:
        image_url: Image URL

    Returns:
        MIME type string
    """
    file_ext = get_file_extension(image_url)
    if not file_ext:
        return ''
    if 'svg' in file_ext:
        return 'image/svg+xml'
    if 'jpg' in file_ext:
        return 'image/jpeg'
    return f'image/{file_ext}'
