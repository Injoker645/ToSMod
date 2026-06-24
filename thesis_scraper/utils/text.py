"""
HTML text cleaning for platform APIs that return HTML (e.g. YouTube textDisplay).
Decodes HTML entities (&#39; → ', &amp; → &) and normalizes tags (<br> → newline).
Uses stdlib html.unescape for correct decoding of any language and Unicode/emojis.
"""
import html
import re
from typing import Optional


def clean_html_text(text: Optional[str]) -> str:
    """
    Decode HTML entities and normalize common tags to plain text.

    - Uses html.unescape() for entities: &#39; → ', &amp; → &, &lt; → <, etc.
      Works correctly for any language and Unicode/emojis (entities decode to
      the right code points).
    - Replaces <br>, <br/>, <br /> (case-insensitive) with newline.
    - Strips any remaining HTML tags so no <a href="..."> etc. remain.

    Safe to call on None or already-plain text (returns "" or unchanged content).
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # Decode HTML entities first (&#39; → ', &amp; → &, etc.)
    out = html.unescape(text)
    # Normalize line breaks
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    out = re.sub(r"<[^>]+>", "", out)
    # Normalize whitespace: collapse multiple newlines/spaces if desired (optional)
    return out.strip()
