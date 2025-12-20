import sys

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QGridLayout, QVBoxLayout
from PyQt5.QtGui import QPixmap, QPainter, QBrush, QPen
from PyQt5.QtCore import Qt


PATTERNS = [
    ("NoBrush",       Qt.NoBrush),
    ("SolidPattern",  Qt.SolidPattern),
    ("Dense1Pattern", Qt.Dense1Pattern),
    ("Dense2Pattern", Qt.Dense2Pattern),
    ("Dense3Pattern", Qt.Dense3Pattern),
    ("Dense4Pattern", Qt.Dense4Pattern),
    ("Dense5Pattern", Qt.Dense5Pattern),
    ("Dense6Pattern", Qt.Dense6Pattern),
    ("Dense7Pattern", Qt.Dense7Pattern),
    ("HorPattern",    Qt.HorPattern),
    ("VerPattern",    Qt.VerPattern),
    ("CrossPattern",  Qt.CrossPattern),
    ("BDiagPattern",  Qt.BDiagPattern),
    ("FDiagPattern",  Qt.FDiagPattern),
    ("DiagCrossPattern", Qt.DiagCrossPattern),
]


def make_swatch(brush_style, size=80):
    """
    Create a QPixmap swatch showing the given brush style.
    Background: white
    Pattern: black
    Border: gray
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.white)

    painter = QPainter(pixmap)

    # Fill with the pattern
    brush = QBrush(Qt.black, brush_style)
    painter.setBrush(brush)
    painter.setPen(Qt.NoPen)
    painter.drawRect(0, 0, size, size)

    # Draw a border for clarity
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(Qt.gray, 1))
    painter.drawRect(0, 0, size - 1, size - 1)

    painter.end()
    return pixmap


class PatternGallery(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Qt Brush Pattern Gallery")
        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        layout.setSpacing(12)

        cols = 4  # number of columns in the grid

        for i, (name, style) in enumerate(PATTERNS):
            row = i // cols
            col = i % cols

            swatch = make_swatch(style, size=80)

            # Vertical layout: swatch on top, label below
            cell_layout = QVBoxLayout()
            label_img = QLabel()
            label_img.setPixmap(swatch)
            label_img.setFixedSize(80, 80)
            label_img.setToolTip(name)

            label_text = QLabel(name)
            label_text.setAlignment(Qt.AlignCenter)

            cell_layout.addWidget(label_img)
            cell_layout.addWidget(label_text)

            # Wrap in a QWidget so we can add to the grid
            cell_widget = QWidget()
            cell_widget.setLayout(cell_layout)

            layout.addWidget(cell_widget, row, col)

        self.setLayout(layout)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = PatternGallery()
    w.show()
    sys.exit(app.exec_())
