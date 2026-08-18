"""PDFs for every engineering-drawing case, built rather than committed.

Built so each fixture visibly *is* the case it claims to be: the vector
sheet carries its text as text, and the scanned pages have no text layer
because they were rasterised before being written. A committed binary would
assert the same thing without showing why it was true.

Shared by the unit and integration suites. Both need genuine files, because
the whole point of the detection is that it reads the file rather than the
loader's output -- a stubbed path would defeat it entirely.
"""

from __future__ import annotations

from pathlib import Path

# A drawing's text: labels, a schedule and a title block. No sentences.
DRAWING_LINES = [
    "DRAWING NO: S-104",
    "REV: C",
    "SCALE 1:100",
    "ROOF FRAMING PLAN",
    "BEAM SCHEDULE",
    "B-14  ISMB 300  Fe 415  SPAN 6000",
    "B-15  ISMB 400  Fe 415  SPAN 7500",
    "C-01  ISMC 200  Fe 410  BRACING",
    "ALL DIMENSIONS IN MM",
    "BOLTS: M20x60 GRADE 8.8",
]

# A specification's text: prose, with terminated sentences.
PROSE_TEXT = (
    "This specification covers the supply and erection of structural steelwork. "
    "All steel shall conform to IS 2062 E250 unless noted otherwise on the drawings. "
    "Bolted connections shall use property class 8.8 bolts to IS 1367. "
    "Welding shall be carried out by qualified welders in accordance with IS 816. "
    "The contractor shall submit fabrication drawings for approval before work begins. "
    "Surface preparation shall achieve SA 2.5 before the application of primer. "
)

# The prose half of the mixed page. Deliberately *not* PROSE_TEXT: reusing
# the specification here made page 5's prose chunks byte-identical to page
# 1's, and the validator correctly rejected them as duplicates -- which
# looked exactly like the mixed page having failed to produce prose chunks
# at all. Erection notes are their own text on a real sheet anyway.
ERECTION_NOTES = (
    "Base plates shall be set to level on steel packing shims before grouting. "
    "Holding down bolts must be checked against the setting out plan prior to concreting. "
    "Non shrink grout shall be poured from one side only to avoid trapping air. "
    "Column verticality shall be confirmed by theodolite once the grout has cured. "
    "Temporary bracing may only be released after the floor plate has been fixed. "
    "Any damage to the galvanised finish shall be made good with two coats of zinc rich paint. "
)


def _plot_drawing(ax) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for y in (0.35, 0.55, 0.75):
        ax.plot([0.10, 0.60], [y, y], "k-", lw=1.6)
    for line_index, text in enumerate(DRAWING_LINES):
        ax.text(0.08, 0.94 - line_index * 0.03, text, fontsize=7)


def build_drawing_pdfs(out: Path) -> dict[str, Path]:
    """One PDF per case, each genuinely of the kind it is named for."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    out.mkdir(parents=True, exist_ok=True)

    # 1. Plotted from CAD: text is real text.
    with PdfPages(out / "vector.pdf") as pdf:
        fig, ax = plt.subplots(figsize=(11.7, 8.3))
        _plot_drawing(ax)
        pdf.savefig(fig)
        plt.close(fig)

    # 2. Scanned: the same sheet rasterised, so the text layer is gone. This
    #    is what a plan-chest scan or a photocopy actually is.
    fig, ax = plt.subplots(figsize=(11.7, 8.3))
    _plot_drawing(ax)
    fig.savefig(out / "_raster.png", dpi=110)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11.7, 8.3))
    ax.imshow(plt.imread(out / "_raster.png"))
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(out / "scanned.pdf", format="pdf", dpi=110)
    plt.close(fig)

    # 3. A specification. The control: if this stops being prose, the
    #    routing is a blanket behaviour change rather than a fix.
    with PdfPages(out / "prose.pdf") as pdf:
        fig, ax = plt.subplots(figsize=(8.3, 11.7))
        ax.axis("off")
        wrapped, line = [], ""
        for word in (PROSE_TEXT * 3).split():
            if len(line) + len(word) > 70:
                wrapped.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        wrapped.append(line)
        for i, text in enumerate(wrapped[:44]):
            ax.text(0.05, 0.96 - i * 0.021, text, fontsize=8)
        pdf.savefig(fig)
        plt.close(fig)

    (out / "_raster.png").unlink(missing_ok=True)
    return {
        "vector": out / "vector.pdf",
        "scanned": out / "scanned.pdf",
        "prose": out / "prose.pdf",
        "mixed": _build_mixed_document(out, plt, PdfPages),
    }


# One PDF, five pages, five kinds of content -- the case a document-level
# classifier cannot express. Page 4 is genuinely rasterised, so it genuinely
# has no text layer while the other four do.
MIXED_PAGE_KINDS = {
    1: "prose",  # specification text
    2: "prose",  # a beam schedule, parsed as a table
    3: "vector_drawing",  # plotted CAD sheet
    4: "scanned_drawing",  # the same sheet, scanned
    5: "mixed",  # detail drawing above, erection notes below
}

TABLE_ROWS = [
    "Mark      Section     Grade    Length    Qty   Mass",
    "B-14      ISMB 300    Fe 415   6000      12    2844",
    "B-15      ISMB 400    Fe 415   7500      8     3912",
    "C-01      ISMC 200    Fe 410   3600      24    1670",
    "C-02      ISMC 250    Fe 410   3600      16    1420",
    "A-01      ISA 75x75   Fe 410   2400      40     528",
    "T-01      ISWB 350    Fe 415   9000      4     2610",
]


def _wrap(text: str, width: int = 70, limit: int = 40) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    return out[:limit]


def _prose_page(ax) -> None:
    ax.axis("off")
    for i, line in enumerate(_wrap(PROSE_TEXT * 3)):
        ax.text(0.05, 0.96 - i * 0.022, line, fontsize=8)


def _table_page(ax) -> None:
    ax.axis("off")
    ax.text(0.05, 0.95, "BEAM AND COLUMN SCHEDULE", fontsize=9)
    for i, row in enumerate(TABLE_ROWS):
        ax.text(0.05, 0.90 - i * 0.03, row, fontsize=8, family="monospace")
    for i in range(len(TABLE_ROWS) + 1):
        ax.plot([0.04, 0.85], [0.915 - i * 0.03, 0.915 - i * 0.03], "k-", lw=0.4)


def _sheet_page(ax) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.plot([0.02, 0.98, 0.98, 0.02, 0.02], [0.02, 0.02, 0.98, 0.98, 0.02], "k-", lw=1.2)
    for y in (0.35, 0.55, 0.75):
        ax.plot([0.10, 0.60], [y, y], "k-", lw=1.6)
    for x in (0.10, 0.35, 0.60):
        ax.plot([x, x], [0.35, 0.75], "k-", lw=1.0)
    ax.annotate("", xy=(0.10, 0.28), xytext=(0.60, 0.28), arrowprops={"arrowstyle": "<->"})
    ax.text(0.33, 0.30, "6000", fontsize=7)
    for i, line in enumerate(DRAWING_LINES[:8]):
        ax.text(0.08, 0.94 - i * 0.03, line, fontsize=7)


def _mixed_page(ax) -> None:
    """Detail drawing on the upper half, erection notes on the lower."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.plot([0.02, 0.98, 0.98, 0.02, 0.02], [0.52, 0.52, 0.98, 0.98, 0.52], "k-", lw=1.0)
    for y in (0.65, 0.78):
        ax.plot([0.10, 0.55], [y, y], "k-", lw=1.4)
    ax.text(0.06, 0.95, "DRAWING NO: S-207", fontsize=7)
    ax.text(0.06, 0.92, "REV: A   SCALE 1:50", fontsize=7)
    ax.text(0.06, 0.89, "BASE PLATE DETAIL", fontsize=7)
    ax.text(0.06, 0.60, "PLATE 400x400x20  Fe 410", fontsize=7)
    ax.text(0.06, 0.57, "4 NOS M24 ANCHOR BOLTS", fontsize=7)
    ax.text(0.05, 0.46, "NOTES ON BASE PLATE ERECTION", fontsize=8)
    for i, line in enumerate(_wrap(ERECTION_NOTES * 2, width=78, limit=14)):
        ax.text(0.05, 0.42 - i * 0.026, line, fontsize=7)


def _build_mixed_document(out: Path, plt, pdf_pages) -> Path:
    path = out / "mixed_document.pdf"
    with pdf_pages(path) as pdf:
        for builder in (_prose_page, _table_page, _sheet_page):
            fig, ax = plt.subplots(figsize=(8.3, 11.7))
            builder(ax)
            pdf.savefig(fig)
            plt.close(fig)

        # Page 4 rasterised first, so this page alone loses its text layer.
        fig, ax = plt.subplots(figsize=(8.3, 11.7))
        _sheet_page(ax)
        fig.savefig(out / "_p4.png", dpi=120)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8.3, 11.7))
        ax.imshow(plt.imread(out / "_p4.png"))
        ax.axis("off")
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        pdf.savefig(fig)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8.3, 11.7))
        _mixed_page(ax)
        pdf.savefig(fig)
        plt.close(fig)

    (out / "_p4.png").unlink(missing_ok=True)
    return path
