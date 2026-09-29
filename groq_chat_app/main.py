"""
main.py

Entry point for the Groq Chat desktop application.

Run with:
    python main.py

Copy .env.example to .env and set GROQ_API_KEY (chat, vision, PDFs) and
OPENROUTER_API_KEY (image generation). Both can also be entered later in the
in-app Settings dialog.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Groq Chat")
    app.setOrganizationName("GroqChatApp")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
