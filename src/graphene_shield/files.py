"""Content-based upload validation.

File types are detected from magic bytes and container structure, never from
the client-supplied name or content type. ``python-magic`` (``pip install
graphene-shield[files]``) improves detection of legacy Office and text
files; everything else works without it.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
import zlib
from collections.abc import Iterable
from typing import IO, Any

from graphene_shield.messages import FileErrorMessages

try:
    import magic
except ImportError:  # pragma: no cover - exercised only without the extra
    magic = None

try:
    import pypdf
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject
except ImportError:  # pragma: no cover - exercised only without the extra
    pypdf = None

logger = logging.getLogger("graphene_shield.files")

DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024
DEFAULT_ALLOWED_TYPES = frozenset({"PDF"})
# Upper bound on bytes inflated from a PDF's compressed streams (zip-bomb guard).
# A PDF whose streams inflate past this is treated as unsafe.
MAX_PDF_INFLATED_BYTES = 64 * 1024 * 1024
# Upper bound on PDF objects visited by the parser-based check.
MAX_PDF_OBJECTS = 500_000

JPEG_HEADER = b"\xff\xd8\xff"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

MIME_TYPES = {
    "PDF": "application/pdf",
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "ICO": "image/x-icon",
    "TXT": "text/plain",
    "DOC": "application/msword",
    "XLS": "application/vnd.ms-excel",
    "PPT": "application/vnd.ms-powerpoint",
    "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "PPTX": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

PDF_ACTIVE_CONTENT_MARKERS = (
    b"/javascript",
    b"/js",
    b"/openaction",
    b"/aa",
    b"/richmedia",
    b"/launch",
    b"/embeddedfile",
    b"/embeddedfiles",
    b"/xfa",
    b"/submitform",
    b"/importdata",
    b"/gotor",
    b"/gotoe",
)
# Text that must never be accepted as a plain-text upload: browsers may render it.
MARKUP_MARKERS = (b"<html", b"<script", b"<svg", b"<?xml", b"<!doctype", b"<iframe", b"<body")
# A PDF name ends at whitespace or a delimiter (ISO 32000-1 §7.2.2).
_PDF_NAME_ESCAPE = re.compile(rb"#([0-9a-fA-F]{2})")
# One pass over the text for every marker; each must be a whole name (/JS, not /JSX).
_ACTIVE_MARKER_RE = re.compile(
    rb"(?:"
    + b"|".join(re.escape(m) for m in PDF_ACTIVE_CONTENT_MARKERS)
    + rb")(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)"
)


def _sniff_mime(data: bytes) -> str | None:
    if magic is None:
        return None
    try:
        return magic.from_buffer(data, mime=True)
    except Exception:
        return None


def _read_all(file: bytes | IO[bytes]) -> bytes:
    if isinstance(file, (bytes, bytearray)):
        return bytes(file)
    if hasattr(file, "seek"):
        file.seek(0)
    data = file.read() or b""
    if hasattr(file, "seek"):
        file.seek(0)
    return data


def looks_like_markup(data: bytes) -> bool:
    """True for HTML/SVG/XML-ish content, which a browser could execute."""
    sample = data[:4096].lower()
    return any(m in sample for m in MARKUP_MARKERS)


def looks_like_text(data: bytes) -> bool:
    """UTF-8 decodable, no NUL bytes, and contains a delimiter or newline (CSV/TXT)."""
    try:
        sample = data[:2048].decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte char may be cut at the sample boundary.
        try:
            sample = data[:2044].decode("utf-8")
        except UnicodeDecodeError:
            return False
    if "\0" in sample:
        return False
    return any(d in sample for d in (",", ";", "\t", "\n"))


def detect_file_type(file: bytes | IO[bytes]) -> tuple[str | None, str | None]:
    """Return ``(TYPE, mime)`` such as ``("PDF", "application/pdf")``, or ``(None, mime)``.

    Recognised types: PDF, PNG, JPEG, GIF, ICO, TXT, DOC, XLS, PPT, DOCX, XLSX, PPTX.
    A file whose header claims a format but whose structure doesn't match is rejected.
    libmagic (``python-magic``) is only consulted when the signature checks can't
    decide, e.g. for legacy Office files.
    """
    data = _read_all(file)
    if len(data) < 4:
        return None, None

    header = data[:8]

    if data.startswith(b"%PDF-"):
        if b"obj" in data[:1024] or b"xref" in data or b"%%EOF" in data[-1024:]:
            return "PDF", MIME_TYPES["PDF"]
        return None, _sniff_mime(data)
    if data.startswith(PNG_HEADER):
        return "PNG", MIME_TYPES["PNG"]
    if data.startswith(JPEG_HEADER):
        return "JPEG", MIME_TYPES["JPEG"]
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF", MIME_TYPES["GIF"]
    if data.startswith(b"\x00\x00\x01\x00"):
        return "ICO", MIME_TYPES["ICO"]

    if header.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError):
            return None, _sniff_mime(data)
        # Macro-enabled documents (.docm/.xlsm/.pptm) carry VBA; never accept them.
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            return None, _sniff_mime(data)
        if "[Content_Types].xml" in names:
            for prefix, type_name in (("word/", "DOCX"), ("xl/", "XLSX"), ("ppt/", "PPTX")):
                if any(n.startswith(prefix) for n in names):
                    return type_name, MIME_TYPES[type_name]
        return None, _sniff_mime(data)

    if header.startswith(OLE_HEADER):
        lowered = (_sniff_mime(data) or "").lower()
        if "msword" in lowered:
            return "DOC", MIME_TYPES["DOC"]
        if "excel" in lowered or "spreadsheet" in lowered:
            return "XLS", MIME_TYPES["XLS"]
        if "powerpoint" in lowered or "presentation" in lowered:
            return "PPT", MIME_TYPES["PPT"]
        return None, _sniff_mime(data)

    if looks_like_markup(data):
        return None, _sniff_mime(data)
    if looks_like_text(data):
        return "TXT", MIME_TYPES["TXT"]
    mime_type = _sniff_mime(data)
    if mime_type in ("text/plain", "text/csv"):
        return "TXT", mime_type

    # Every binary format we accept has a signature that was checked above, so a
    # MIME guess alone is never trusted.
    return None, _sniff_mime(data)


def _unescape_names(data: bytes) -> bytes:
    # Names may hide characters as #xx escapes: /J#61vaScript == /JavaScript.
    return _PDF_NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), data)


def _has_active_marker(data: bytes) -> bool:
    if b"#" in data:
        data = _unescape_names(data)
    return _ACTIVE_MARKER_RE.search(data.lower()) is not None


def _split_streams(data: bytes) -> tuple[list[bytes], list[tuple[int, int, int]]]:
    """Separate stream bodies from the rest of the file in one linear pass.

    Returns the text outside stream bodies, and ``(dict_start, body_start,
    body_end)`` offsets for each stream (bodies aren't copied). The ``stream``
    keyword only counts when it directly follows a dictionary's ``>>``.
    """
    outside: list[bytes] = []
    streams: list[tuple[int, int, int]] = []
    segment_start = search = 0
    while (i := data.find(b"stream", search)) != -1:
        body = i + 6
        if data.startswith(b"\r\n", body):
            body += 2
        elif data.startswith((b"\n", b"\r"), body):
            body += 1
        else:
            search = body
            continue
        if not data[max(segment_start, i - 64) : i].rstrip().endswith(b">>"):
            search = body  # the word "stream", not a stream keyword
            continue
        end = data.find(b"endstream", body)
        if end == -1:
            end = len(data)
        outside.append(data[segment_start:i])
        streams.append((max(segment_start, i - 1024), body, end))
        segment_start = search = end + 9
    outside.append(data[segment_start:])
    return outside, streams


_ACTIVE_NAMES = frozenset(m.decode().lower() for m in PDF_ACTIVE_CONTENT_MARKERS)


def _parsed_pdf_has_active_content(data: bytes) -> bool:
    """Walk every object reachable from the trailer (object streams included).

    Fails closed: unparseable or undecryptable files count as active content.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return True  # can't inspect a password-protected file
        stack = [reader.trailer]
        seen: set[tuple[int, int]] = set()
        visited = 0
        while stack:
            obj = stack.pop()
            visited += 1
            if visited > MAX_PDF_OBJECTS:
                return True
            if isinstance(obj, IndirectObject):
                key = (obj.idnum, obj.generation)
                if key in seen:
                    continue
                seen.add(key)
                obj = obj.get_object()
            if isinstance(obj, DictionaryObject):  # StreamObject is a DictionaryObject too
                for name, value in obj.items():
                    if name.lower() in _ACTIVE_NAMES:
                        return True
                    if isinstance(value, NameObject) and value.lower() in _ACTIVE_NAMES:
                        return True  # e.g. /S /JavaScript, /S /Launch
                    stack.append(value)
            elif isinstance(obj, ArrayObject):
                stack.extend(obj)
        return False
    except Exception:
        logger.warning("Could not parse PDF for active-content check", exc_info=True)
        return True


def _scanned_pdf_has_active_content(data: bytes) -> bool:
    """Byte-level check of the PDF's dictionaries.

    Stream bodies are skipped (random image bytes would otherwise match markers),
    except object streams, which hold dictionaries and are inflated and scanned.
    Fails closed for object streams that can't be inflated or inflate too far.
    """
    outside, streams = _split_streams(data)
    if _has_active_marker(b"\n".join(outside)):
        return True

    budget = MAX_PDF_INFLATED_BYTES
    for dict_start, body_start, body_end in streams:
        stream_dict = data[dict_start:body_start]
        if b"#" in stream_dict:
            stream_dict = _unescape_names(stream_dict)
        if b"/objstm" not in stream_dict.lower():
            continue
        inflater = zlib.decompressobj()
        try:
            inflated = inflater.decompress(data[body_start:body_end], budget + 1)
        except zlib.error:
            return True  # not Flate: its dictionaries can't be inspected
        budget -= len(inflated)
        if budget < 0 or inflater.unconsumed_tail:
            return True
        if _has_active_marker(inflated):
            return True
    return False


def contains_pdf_active_content(file: bytes | IO[bytes]) -> bool:
    """Detect JavaScript, auto-actions, launch/submit actions, XFA and embedded files.

    With ``pypdf`` installed (``graphene-shield[files]``) every object the
    PDF reader would use is inspected, including compressed object streams and
    #-escaped names; a byte-level scan runs as well. Without it only the
    byte-level scan runs, which is a heuristic. Either way the check fails
    closed on files it can't inspect. Digital signatures are not checked: a
    signed PDF with active content is rejected like any other. The file is
    rewound afterwards.
    """
    data = _read_all(file)
    if _scanned_pdf_has_active_content(data):
        return True
    return pypdf is not None and _parsed_pdf_has_active_content(data)


def _file_size(file: Any) -> int:
    size = getattr(file, "size", None)
    if size is not None:
        return size
    if isinstance(file, (bytes, bytearray)):
        return len(file)
    pos = file.tell()
    file.seek(0, io.SEEK_END)
    size = file.tell()
    file.seek(pos)
    return size


def validate_file(
    file: Any,
    allowed_types: Iterable[str] | None = None,
    max_file_size: int | None = None,
    check_file_size: bool = True,
    validate_content: bool = True,
    error_messages: Any = None,
) -> Any | None:
    """Validate an upload. Returns the failing error message object, or ``None`` if valid.

    ``max_file_size`` defaults to ``DEFAULT_MAX_FILE_SIZE`` (10 MB) and
    ``allowed_types`` to ``DEFAULT_ALLOWED_TYPES`` (PDF) when omitted or None.
    ``error_messages`` is any object with FILE_REQUIRED, FILE_TOO_LARGE,
    INVALID_FILE_TYPE and ACTIVE_CONTENT_DETECTED attributes. An empty
    ``allowed_types`` allows nothing.
    """
    messages = error_messages or FileErrorMessages
    if not file:
        return messages.FILE_REQUIRED

    if max_file_size is None:
        max_file_size = DEFAULT_MAX_FILE_SIZE
    if check_file_size and _file_size(file) > max_file_size:
        return messages.FILE_TOO_LARGE
    if not validate_content:
        return None

    if allowed_types is None:
        allowed_types = DEFAULT_ALLOWED_TYPES
    allowed = {t.upper() for t in allowed_types}
    data = _read_all(file)  # read once; both checks below work on the bytes
    detected, _mime = detect_file_type(data)
    if not detected or detected not in allowed:
        return messages.INVALID_FILE_TYPE
    if detected == "PDF" and contains_pdf_active_content(data):
        return messages.ACTIVE_CONTENT_DETECTED
    return None
