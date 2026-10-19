import io

from PIL import Image

MEDIA_TYPES = {"png": "image/png", "webp": "image/webp", "jpeg": "image/jpeg"}


def check(format: str, scale: float):
    if format not in MEDIA_TYPES:
        raise ValueError(f"format must be one of {', '.join(MEDIA_TYPES)}")
    if not 0 < scale <= 1:
        raise ValueError("scale must be in (0, 1]")


def encode(png: bytes, format: str = "png", scale: float = 1.0, quality: int = 80) -> bytes:
    """Re-encodes a PNG screenshot, optionally scaled down. WebP and JPEG are a fraction of the size, which is most
    of a screenshot's latency over the network and its token cost for a model."""
    check(format, scale)
    if format == "png" and scale == 1:
        return png
    image = Image.open(io.BytesIO(png))
    if scale < 1:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS
        )
    if format == "jpeg":
        image = image.convert("RGB")
    out = io.BytesIO()
    options: dict = {"quality": max(1, min(int(quality), 100))}
    if format == "webp":
        # method 2 encodes about twice as fast as the default 4 for a few percent more bytes
        options["method"] = 2
    image.save(out, format=format.upper(), **options)
    return out.getvalue()
