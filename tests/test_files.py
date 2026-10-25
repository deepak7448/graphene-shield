import io
import os
import zipfile
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject, TextStringObject

from graphene_shield import files
from graphene_shield.messages import FileErrorMessages

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
HDR = b"%PDF-1.7\n1 0 obj\n"


def make_pdf(js=None, content=None, metadata=None):
    """A real, parseable PDF; optionally with an /OpenAction JavaScript action."""
    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    if content is not None:
        stream = StreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
    if js is not None:
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject(js)}
        )
    if metadata:
        writer.add_metadata(metadata)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


PDF = make_pdf()
PDF_JS = make_pdf(js="app.alert(1)")


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return buf.getvalue()


# ── Type detection ──


def test_detects_signature_based_types():
    assert files.detect_file_type(PDF)[0] == "PDF"
    assert files.detect_file_type(PNG)[0] == "PNG"
    assert files.detect_file_type(b"GIF89a....")[0] == "GIF"
    assert files.detect_file_type(b"a,b,c\n1,2,3\n")[0] == "TXT"


def test_detects_every_jpeg_variant():
    for marker in (b"\xe0", b"\xe1", b"\xe2", b"\xdb", b"\xee"):
        assert files.detect_file_type(b"\xff\xd8\xff" + marker + b"\x00" * 20)[0] == "JPEG"


def test_detects_ooxml_and_rejects_plain_zip():
    assert files.detect_file_type(_zip(["[Content_Types].xml", "word/document.xml"]))[0] == "DOCX"
    assert files.detect_file_type(_zip(["[Content_Types].xml", "xl/workbook.xml"]))[0] == "XLSX"
    assert files.detect_file_type(_zip(["payload.exe"]))[0] is None
    assert files.detect_file_type(b"PK\x03\x04not-really-a-zip")[0] is None


def test_rejects_macro_enabled_office_files():
    docm = _zip(["[Content_Types].xml", "word/document.xml", "word/vbaProject.bin"])
    xlsm = _zip(["[Content_Types].xml", "xl/workbook.xml", "xl/vbaProject.bin"])
    assert files.detect_file_type(docm)[0] is None
    assert files.detect_file_type(xlsm)[0] is None


def test_markup_is_never_plain_text():
    for doc in (
        b"<html>\n<script>alert(1)</script>\n</html>",
        b'<?xml version="1.0"?>\n<svg onload="alert(1)"/>',
        b"name,notes\nx,<script>alert(1)</script>\n",
    ):
        assert files.detect_file_type(doc)[0] is None


def test_rejects_pdf_header_without_structure_and_tiny_input():
    assert files.detect_file_type(b"%PDF-garbage")[0] is None
    assert files.detect_file_type(b"ab") == (None, None)


def test_detect_rewinds_file_objects():
    upload = SimpleUploadedFile("a.pdf", PDF)
    upload.read(3)
    assert files.detect_file_type(upload)[0] == "PDF"
    assert upload.tell() == 0


# ── PDF active content ──


def test_pdf_active_content():
    assert files.contains_pdf_active_content(PDF) is False
    assert files.contains_pdf_active_content(PDF_JS) is True
    # Digital signatures are not checked: a "signed" PDF with JS is still rejected.
    signed = PDF_JS + b"\n<< /Type /Sig /ByteRange [0 1 2 3] >>"
    assert files.contains_pdf_active_content(signed) is True


def test_compact_syntax_without_spaces_is_detected():
    # A link annotation's action, written without whitespace. Each name is
    # preceded by a non-delimiter letter, which the old prefix check required.
    data = HDR + b"<</Type/Annot/A<</S/JavaScript/JS(app.alert(1))>>>>endobj"
    assert files._scanned_pdf_has_active_content(data) is True


def test_hex_escaped_names_are_detected():
    data = HDR + b"<< /S /J#61vaScript /J#53 (app.alert(1)) >> endobj"
    assert files._scanned_pdf_has_active_content(data) is True


def test_marker_must_be_a_whole_name():
    assert files._scanned_pdf_has_active_content(HDR + b"<< /JSX 1 /AAPL 2 >>") is False


def test_javascript_inside_compressed_object_stream_is_detected():
    body = zlib.compress(b"<< /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>")
    data = (
        HDR
        + b"<< /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode /Length %d >>\nstream\n" % len(body)
        + body
        + b"\nendstream\nendobj\n"
    )
    assert files._scanned_pdf_has_active_content(data) is True


def test_object_stream_that_cannot_be_inflated_fails_closed():
    data = HDR + b"<< /Type /ObjStm /Filter /ASCII85Decode >>\nstream\n<~garbage~>\nendstream"
    assert files._scanned_pdf_has_active_content(data) is True


def test_compression_bomb_fails_closed(monkeypatch):
    monkeypatch.setattr(files, "MAX_PDF_INFLATED_BYTES", 1000)
    body = zlib.compress(b" " * 10_000)
    data = HDR + b"<< /Type /ObjStm /Filter /FlateDecode >>\nstream\n" + body + b"\nendstream"
    assert files._scanned_pdf_has_active_content(data) is True


def test_marker_bytes_inside_image_data_are_not_flagged():
    # Binary stream data (images) often contains "/JS " by chance; only dictionaries matter.
    noise = os.urandom(200_000) + b" /JS (x) /AA << /OpenAction "
    assert files.contains_pdf_active_content(make_pdf(content=noise)) is False


def test_javascript_anywhere_in_a_large_pdf_is_detected():
    # Larger than the old 1 MB head + 200 KB tail scan window.
    big = make_pdf(js="app.alert(1)", metadata={"/Producer": "x" * 3_000_000})
    assert len(big) > 3_000_000
    assert files.contains_pdf_active_content(big) is True


def test_unparseable_pdf_fails_closed():
    assert files.contains_pdf_active_content(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\nxref\n%%EOF")


def test_pdf_scan_on_upload_rewinds():
    upload = SimpleUploadedFile("a.pdf", PDF_JS)
    assert files.contains_pdf_active_content(upload) is True
    assert upload.tell() == 0


# ── validate_file ──


def test_validate_file_paths():
    assert files.validate_file(None) is FileErrorMessages.FILE_REQUIRED
    assert files.validate_file(SimpleUploadedFile("a.pdf", PDF)) is None
    assert (
        files.validate_file(SimpleUploadedFile("a.pdf", PDF), max_file_size=5)
        is FileErrorMessages.FILE_TOO_LARGE
    )
    assert (
        files.validate_file(SimpleUploadedFile("a.png", PNG)) is FileErrorMessages.INVALID_FILE_TYPE
    )
    assert files.validate_file(SimpleUploadedFile("a.png", PNG), allowed_types=["png"]) is None
    assert (
        files.validate_file(SimpleUploadedFile("a.pdf", PDF_JS))
        is FileErrorMessages.ACTIVE_CONTENT_DETECTED
    )
    assert (
        files.validate_file(
            SimpleUploadedFile("a.bin", b"\x00\x01\x02\x03"), validate_content=False
        )
        is None
    )


def test_empty_allowed_types_allows_nothing():
    assert (
        files.validate_file(SimpleUploadedFile("a.pdf", PDF), allowed_types=[])
        is FileErrorMessages.INVALID_FILE_TYPE
    )


def test_validate_file_default_limit():
    big = PDF + b" " * files.DEFAULT_MAX_FILE_SIZE
    assert files.validate_file(SimpleUploadedFile("a.pdf", big)) is FileErrorMessages.FILE_TOO_LARGE
    assert (
        files.validate_file(SimpleUploadedFile("a.pdf", big), max_file_size=None)
        is FileErrorMessages.FILE_TOO_LARGE
    )
    assert files.validate_file(SimpleUploadedFile("a.pdf", big), check_file_size=False) is None


def test_the_word_stream_does_not_hide_a_dictionary():
    # "stream" only starts a stream body right after a dictionary's ">>". Otherwise
    # a name like /upstream could make the scanner skip the /OpenAction after it.
    data = HDR + b"<< /Name /upstream\n /OpenAction << /S /JavaScript >> >> endstream"
    assert files._scanned_pdf_has_active_content(data) is True


def test_validate_file_reads_the_upload_once():
    class CountingUpload(SimpleUploadedFile):
        reads = 0

        def read(self, *args, **kwargs):
            CountingUpload.reads += 1
            return super().read(*args, **kwargs)

    assert files.validate_file(CountingUpload("a.pdf", PDF)) is None
    assert CountingUpload.reads == 1
