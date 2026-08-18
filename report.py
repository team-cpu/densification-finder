"""
The parcel data sheet — the detail view as a PDF.

One parcel per sheet — a page or two, depending on how many assumptions were
overridden — carrying the same three blocks the screen shows and the whole
calculation path, so a figure in the document can be traced back to the
assumption it came from. Deliberately plain: this is a working paper Philipp
prints, marks up, and takes into a meeting, not a brochure.

Sources and the confirmation marks travel with it. A residual land value on
letterhead is exactly the kind of number that gets quoted back six weeks later,
by which time nobody remembers that the sale price behind it was a canton-wide
median rather than an appraisal.
"""
import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import economics as E

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#5b5b5b")
RULE = colors.HexColor("#c8c8c8")
BAND = colors.HexColor("#f2f2f2")

_BASE = getSampleStyleSheet()
STYLES = {
    "title": ParagraphStyle(
        "dtitle", parent=_BASE["Title"], fontSize=16, leading=20,
        alignment=0, textColor=INK, spaceAfter=2,
    ),
    "subtitle": ParagraphStyle(
        "dsubtitle", parent=_BASE["Normal"], fontSize=9.5, leading=13,
        textColor=MUTED, spaceAfter=10,
    ),
    "heading": ParagraphStyle(
        "dheading", parent=_BASE["Heading2"], fontSize=11, leading=14,
        textColor=INK, spaceBefore=10, spaceAfter=4,
    ),
    "cell": ParagraphStyle("dcell", parent=_BASE["Normal"], fontSize=9, leading=11.5),
    "cellright": ParagraphStyle(
        "dcellright", parent=_BASE["Normal"], fontSize=9, leading=11.5, alignment=TA_RIGHT
    ),
    "note": ParagraphStyle(
        "dnote", parent=_BASE["Normal"], fontSize=7.5, leading=10, textColor=MUTED
    ),
}


_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _rich(value):
    """One string, two renderers.

    The blocks are written once and shown on screen as markdown, so a value can
    carry `[Gemeinde](https://…)` or `**Harte Beschränkung:**`. Reportlab parses
    its own markup instead, and printed the asterisks and brackets verbatim —
    the document looked like source code where the screen looked finished.

    Escaping comes first: these strings are parsed as XML, and an ampersand in a
    zone name aborts the build with a parse error rather than a wrong character.
    """
    out = escape(str(value))
    out = _LINK.sub(lambda m: f'<link href="{m.group(2)}" color="#1a4fa0">{m.group(1)}</link>', out)
    return _BOLD.sub(r"<b>\1</b>", out)


def _table(rows, widths):
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
            ]
        )
    )
    return table


def _facts(rows, width):
    """Label/value pairs — blocks A and B."""
    body = [
        [Paragraph(_rich(label), STYLES["cell"]), Paragraph(_rich(value), STYLES["cell"])]
        for label, value in rows
    ]
    return _table(body, [width * 0.42, width * 0.58])


def _calculation(steps, width):
    """Block C, one row per step, with the formula next to the figure so the
    document is checkable without the app that produced it."""
    body = []
    for step in steps:
        value = (
            f"{step.value:,.0f} m²".replace(",", "’")
            if step.unit == "m²"
            else f"CHF {E.chf(step.value)}"
        )
        body.append(
            [
                Paragraph(_rich(step.label), STYLES["cell"]),
                Paragraph(_rich(step.formula), STYLES["cell"]),
                Paragraph(value, STYLES["cellright"]),
            ]
        )
    table = Table(body, colWidths=[width * 0.30, width * 0.42, width * 0.28], hAlign="LEFT")
    last = len(body) - 1
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
                ("BACKGROUND", (0, last), (-1, last), BAND),
                ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
                ("LINEABOVE", (0, last), (-1, last), 0.75, INK),
            ]
        )
    )
    return table


def build(title, subtitle, blocks, steps, notes, printed_at=None):
    """Render one parcel data sheet and return the PDF bytes.

    `blocks` is [(heading, [(label, value), …])] — blocks A and B as they stand
    on screen, including any value the user overrode. `steps` is the path
    `economics.residual` produced, unmodified: the document must not recompute
    anything, or it could disagree with the screen it claims to reproduce.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Verdichtungspotenzial-Finder",
        subject="Parzellen-Datenblatt",
    )
    width = doc.width
    printed = printed_at or datetime.now()

    story = [
        Paragraph(title, STYLES["title"]),
        Paragraph(subtitle, STYLES["subtitle"]),
    ]
    for heading, rows in blocks:
        story.append(
            KeepTogether(
                [Paragraph(heading, STYLES["heading"]), _facts(rows, width)]
            )
        )
    story.append(Paragraph("Residualwertrechnung", STYLES["heading"]))
    story.append(_calculation(steps, width))

    story.append(Spacer(1, 8))
    for note in notes:
        story.append(Paragraph(_rich(note), STYLES["note"]))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Erstellt am {printed:%d.%m.%Y %H:%M} mit dem "
            "Verdichtungspotenzial-Finder. Screening-Rechnung auf Basis "
            "öffentlicher Register — keine Bewertung, keine Machbarkeitsstudie "
            "und keine Zusicherung einer Baubewilligung.",
            STYLES["note"],
        )
    )

    doc.build(story)
    return buffer.getvalue()
