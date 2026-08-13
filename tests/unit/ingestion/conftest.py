"""Real PDFs for the three engineering-drawing cases.

Built here rather than committed as binaries so the fixture states what
makes each file the case it claims to be: the vector sheet really does carry
its text as text, and the scanned one really has no text layer, because it
was rasterised before being written.

Shared between the detector's own tests and the parsing-orchestrator tests,
which need genuine files -- the whole point of the detection is that it
reads the file rather than the loader's output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def _plot_drawing(ax) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for y in (0.35, 0.55, 0.75):
        ax.plot([0.10, 0.60], [y, y], "k-", lw=1.6)
    for line_index, text in enumerate(DRAWING_LINES):
        ax.text(0.08, 0.94 - line_index * 0.03, text, fontsize=7)


@pytest.fixture(scope="session")
def drawing_pdfs(tmp_path_factory) -> dict[str, Path]:
    """One PDF per case, each genuinely of the kind it is named for."""
    matplotlib = pytest.importorskip("matplotlib", reason="fixtures need matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    out = tmp_path_factory.mktemp("drawing_pdfs")

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
    }
