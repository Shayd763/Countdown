"""Small helpers about a listing's category."""

from .models import PropertyCategory, RawListing


def is_residential(listing: RawListing) -> bool:
    return listing.category == PropertyCategory.RESIDENTIAL
