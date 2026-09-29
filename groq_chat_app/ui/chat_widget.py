"""
ui/chat_widget.py

The central chat viewport: a scrollable list of message bubbles plus a
ChatGPT-style rounded input bar with an attach button, file chips, and a send
button. Assistant replies render as formatted Markdown (headings, lists,
bold/italic, code blocks). Every message carries a Copy action; assistant
messages also get Share + Regenerate, and your own messages get Edit.
Emits signals that main_window.py wires up to the Groq API worker.
"""

from __future__ import annotations

import urllib.parse
from typing import List, Optional

import shutil
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QClipboard, QDesktopServices, QGuiApplication, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from api.file_handler import AttachedFile, extract_file, is_supported_extension
from config import MAX_ATTACHMENTS_PER_MESSAGE, MAX_IMAGES_PER_REQUEST, SUPPORTED_ATTACHMENT_EXTENSIONS, Translator

# A light, theme-agnostic style for rendered Markdown content (code blocks,
# quotes, links) that reads fine on both the dark and light themes.
_MARKDOWN_CSS = """
body { margin: 0; }
p { margin: 0 0 8px 0; }
p:last-child { margin-bottom: 0; }
h1, h2, h3 { margin: 10px 0 6px 0; }
ul, ol { margin: 4px 0 8px 22px; padding: 0; }
li { margin-bottom: 2px; }
pre {
    background-color: rgba(127, 127, 127, 0.16);
    border-radius: 8px;
    padding: 10px 12px;
    margin: 8px 0;
}
code {
    background-color: rgba(127, 127, 127, 0.16);
    border-radius: 4px;
    padding: 1px 4px;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 13px;
}
pre code { background-color: transparent; padding: 0; }
blockquote {
    border-left: 3px solid rgba(127, 127, 127, 0.45);
    margin: 6px 0;
    padding-left: 10px;
    color: palette(mid);
}
a { color: #10a37f; text-decoration: none; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid rgba(127, 127, 127, 0.35); padding: 4px 8px; }
"""


class MessageInput(QTextEdit):
    """A QTextEdit that emits `submitted` on Enter and inserts a newline on Shift+Enter."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class FileChip(QFrame):
    """A small pill widget showing an attached file's name with a remove button."""

    removed = Signal(str)  # emits the file path

    def __init__(self, attached: AttachedFile, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FileChip")
        self.attached = attached

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        display_name = attached.name if len(attached.name) <= 24 else attached.name[:21] + "..."
        icon = "⚠" if not attached.is_valid else ("🖼" if attached.is_image else "📎")
        label = QLabel(f"{icon} {display_name}")
        label.setObjectName("FileChipLabel")
        if attached.error:
            label.setToolTip(attached.error)
        elif attached.truncated:
            label.setToolTip("Long document: only the first part is sent to fit model limits.")
        layout.addWidget(label)

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("FileChipRemove")
        remove_btn.setFixedSize(18, 18)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.removed.emit(attached.path))
        layout.addWidget(remove_btn)


class _AutoHeightMarkdownView(QTextBrowser):
    """A read-only, borderless QTextBrowser that renders Markdown and always
    grows/shrinks its height to fit its content instead of scrolling."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MessageText")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenExternalLinks(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().setDefaultStyleSheet(_MARKDOWN_CSS)
        self.document().setDocumentMargin(2)
        self.document().documentLayout().documentSizeChanged.connect(self._sync_height)

    def set_markdown_text(self, text: str) -> None:
        self.setMarkdown(text or "")
        self.document().setTextWidth(self.viewport().width() or self.width())
        self._sync_height()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._sync_height()

    def _sync_height(self, *_args) -> None:
        height = int(self.document().size().height())
        self.setFixedHeight(max(22, height + 6))


class ImageThumb(QWidget):
    """A scaled preview of an image on disk. Click to open; optional Save button."""

    def __init__(self, path: str, translator: Translator, allow_save: bool, max_width: int = 420, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ImageBox")
        self._path = path
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.label = QLabel()
        self.label.setObjectName("ImageThumb")
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.setToolTip(path)
        pix = QPixmap(path)
        if pix.isNull():
            self.label.setText("Image preview unavailable\n" + Path(path).name)
        else:
            if pix.width() > max_width:
                pix = pix.scaledToWidth(max_width, Qt.SmoothTransformation)
            self.label.setPixmap(pix)
        self.label.mousePressEvent = lambda _e: QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))
        layout.addWidget(self.label, 0, Qt.AlignLeft)

        if allow_save:
            btn = QPushButton("⬇ " + translator.t("save_image"))
            btn.setObjectName("ImageSaveButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda: self._save(translator))
            layout.addWidget(btn, 0, Qt.AlignLeft)

    def _save(self, translator: Translator) -> None:
        suffix = Path(self._path).suffix or ".png"
        target, _ = QFileDialog.getSaveFileName(
            self, translator.t("save_image_title"), f"image{suffix}", f"Image (*{suffix})"
        )
        if target:
            try:
                shutil.copyfile(self._path, target)
            except OSError as exc:
                QMessageBox.warning(self, translator.t("save_image"), str(exc))


class MessageBubble(QFrame):
    """
    Renders a single chat message (user or assistant) with formatted Markdown,
    a sender label, and role-appropriate actions (Copy, Edit, Share, Regenerate).

    Signals:
        edit_saved(int, str): a user message was edited -> (bubble index, new text)
        regenerate_clicked(int): the regenerate button was clicked on an
            assistant message -> (bubble index)
    """

    edit_saved = Signal(int, str)
    regenerate_clicked = Signal(int)

    def __init__(
        self,
        role: str,
        text: str,
        translator: Translator,
        index: int = 0,
        images: Optional[List[str]] = None,
        attach_names: Optional[List[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.index = index
        self._translator = translator
        self._raw_text = text
        self._edit_container: Optional[QWidget] = None
        self.setObjectName("MessageBubbleUser" if role == "user" else "MessageBubbleAssistant")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(18, 12, 18, 12)
        self._outer.setSpacing(6)

        header = QHBoxLayout()
        sender_key = "you" if role == "user" else "assistant"
        sender_label = QLabel(translator.t(sender_key))
        sender_label.setObjectName("SenderLabel")
        header.addWidget(sender_label)
        header.addStretch(1)

        self.copy_btn = self._make_icon_button("⧉", translator.t("copy"))
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        header.addWidget(self.copy_btn)

        self.edit_btn: Optional[QPushButton] = None
        self.share_btn: Optional[QPushButton] = None
        self.regen_btn: Optional[QPushButton] = None

        if role == "user":
            self.edit_btn = self._make_icon_button("✎", translator.t("edit"))
            self.edit_btn.clicked.connect(self._enter_edit_mode)
            header.addWidget(self.edit_btn)
        else:
            self.share_btn = self._make_icon_button("↗", translator.t("share"))
            self.share_btn.clicked.connect(self._share_message)
            header.addWidget(self.share_btn)

            self.regen_btn = self._make_icon_button("↻", translator.t("regenerate"))
            self.regen_btn.clicked.connect(lambda: self.regenerate_clicked.emit(self.index))
            header.addWidget(self.regen_btn)

        self._outer.addLayout(header)

        self.text_view = _AutoHeightMarkdownView()
        self._outer.addWidget(self.text_view)
        self._has_attachments = bool(images or attach_names)
        self._render_display()

        self._images_box: Optional[QWidget] = None
        self.set_images(images or [])
        if attach_names:
            note = QLabel("📎 " + ", ".join(attach_names))
            note.setObjectName("AttachNote")
            note.setWordWrap(True)
            self._outer.addWidget(note)

    # ------------------------------------------------------------------ #
    def _make_icon_button(self, glyph: str, tooltip: str) -> QPushButton:
        btn = QPushButton(glyph)
        btn.setObjectName("BubbleIconButton")
        btn.setFixedSize(26, 26)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        return btn

    def set_images(self, paths: List[str]) -> None:
        """Show image previews under the text (replaces any previous ones)."""
        if self._images_box is not None:
            self._outer.removeWidget(self._images_box)
            self._images_box.deleteLater()
            self._images_box = None
        if not paths:
            return
        box = QWidget()
        box.setObjectName("ImageBox")
        row = QVBoxLayout(box)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(8)
        for path in paths:
            row.addWidget(ImageThumb(path, self._translator, allow_save=(self.role == "assistant")))
        self._outer.addWidget(box)
        self._images_box = box

    def set_status(self, text: str) -> None:
        """Placeholder text while an assistant reply is pending (never overwrites real output)."""
        if not self._raw_text:
            self.text_view.set_markdown_text(f"_{text}_")

    def _render_display(self) -> None:
        if self.role == "assistant" and not self._raw_text:
            self.text_view.set_markdown_text(f"_{self._translator.t('thinking')}_")
        elif self.role == "user" and not self._raw_text and self._has_attachments:
            self.text_view.set_markdown_text(f"_{self._translator.t('attached_files_label')}_")
        else:
            self.text_view.set_markdown_text(self._raw_text)

    def _flash_button(self, btn: QPushButton, glyph: str) -> None:
        original = btn.text()
        btn.setText(glyph)
        btn.setEnabled(False)

        def _restore() -> None:
            btn.setText(original)
            btn.setEnabled(True)

        QTimer.singleShot(1100, _restore)

    def _copy_to_clipboard(self) -> None:
        clipboard: QClipboard = QGuiApplication.clipboard()
        clipboard.setText(self._raw_text)
        self._flash_button(self.copy_btn, "✓")

    def _share_message(self) -> None:
        clipboard: QClipboard = QGuiApplication.clipboard()
        clipboard.setText(self._raw_text)
        subject = urllib.parse.quote(self._translator.t("app_title"))
        body = urllib.parse.quote(self._raw_text)
        QDesktopServices.openUrl(QUrl(f"mailto:?subject={subject}&body={body}"))
        self._flash_button(self.share_btn, "✓")

    # ------------------------------------------------------------------ #
    # Streaming / content updates
    # ------------------------------------------------------------------ #
    def append_text(self, delta: str) -> None:
        """Used while streaming: appends a text delta to the bubble's content."""
        self._raw_text += delta
        self._render_display()

    def set_text(self, text: str) -> None:
        self._raw_text = text
        self._render_display()

    # ------------------------------------------------------------------ #
    # Edit mode (user messages only)
    # ------------------------------------------------------------------ #
    def _enter_edit_mode(self) -> None:
        if self._edit_container is not None:
            return

        self.text_view.setVisible(False)
        self.copy_btn.setVisible(False)
        if self.edit_btn is not None:
            self.edit_btn.setVisible(False)

        self._edit_container = QWidget()
        v = QVBoxLayout(self._edit_container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.edit_input = QTextEdit(self._raw_text)
        self.edit_input.setObjectName("EditMessageInput")
        self.edit_input.setMinimumHeight(60)
        v.addWidget(self.edit_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton(self._translator.t("cancel"))
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._cancel_edit)
        save_btn = QPushButton(self._translator.t("save"))
        save_btn.setObjectName("PrimaryButton")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save_edit)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        v.addLayout(btn_row)

        self._outer.addWidget(self._edit_container)
        self.edit_input.setFocus()

    def _cancel_edit(self) -> None:
        if self._edit_container is not None:
            self._edit_container.deleteLater()
            self._edit_container = None
        self.text_view.setVisible(True)
        self.copy_btn.setVisible(True)
        if self.edit_btn is not None:
            self.edit_btn.setVisible(True)

    def _save_edit(self) -> None:
        new_text = self.edit_input.toPlainText().strip()
        if not new_text:
            return
        self._cancel_edit()
        self.set_text(new_text)
        self.edit_saved.emit(self.index, new_text)


class ChatWidget(QWidget):
    """
    The main chat viewport: scrollable message list + input bar.

    Signals:
        message_submitted(str, list, bool): emitted with (user_text, [AttachedFile, ...],
            image_mode_on) when the user sends a new message.
        message_edited(int, str): emitted with (message_index, new_text) when
            the user edits and saves one of their own earlier messages.
        regenerate_requested(int): emitted with (message_index) when the user
            asks to regenerate an assistant reply.
        stop_requested(): emitted when the user clicks "stop generating".
    """

    message_submitted = Signal(str, list, bool)  # text, [AttachedFile], force_image
    message_edited = Signal(int, str)
    regenerate_requested = Signal(int)
    stop_requested = Signal()

    def __init__(self, translator: Translator, parent=None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.attached_files: List[AttachedFile] = []
        self.current_assistant_bubble: Optional[MessageBubble] = None
        self._bubbles: List[MessageBubble] = []
        self._is_generating = False

        self.setAcceptDrops(True)
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Scrollable message area ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ChatScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(60, 24, 60, 24)
        self.messages_layout.setSpacing(16)
        self.messages_layout.addStretch(1)

        self.scroll_area.setWidget(self.messages_container)
        root.addWidget(self.scroll_area, 1)

        # --- Welcome placeholder (shown when there are no messages yet) ---
        self.welcome_widget = self._build_welcome_widget()
        self.messages_layout.insertWidget(0, self.welcome_widget)

        # --- Input bar ---
        root.addWidget(self._build_input_bar())

    def _build_welcome_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)

        title = QLabel(self.translator.t("welcome_title"))
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel(self.translator.t("welcome_subtitle"))
        subtitle.setObjectName("WelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        widget.setContentsMargins(0, 80, 0, 80)
        return widget

    def _build_input_bar(self) -> QWidget:
        wrapper = QWidget()
        self._input_wrapper_layout = QVBoxLayout(wrapper)
        self._input_wrapper_layout.setContentsMargins(60, 8, 60, 20)
        self._input_wrapper_layout.setSpacing(6)

        # File chips row
        self.chips_row = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_row)
        self.chips_layout.setContentsMargins(4, 0, 4, 0)
        self.chips_layout.setSpacing(6)
        self.chips_layout.addStretch(1)
        self.chips_row.setVisible(False)
        self._input_wrapper_layout.addWidget(self.chips_row)

        # Input container (rounded pill with attach + text + send)
        self.input_container = QFrame()
        self.input_container.setObjectName("InputContainer")
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)

        self.attach_button = QPushButton("📎")
        self.attach_button.setObjectName("AttachButton")
        self.attach_button.setFixedSize(36, 36)
        self.attach_button.setCursor(Qt.PointingHandCursor)
        self.attach_button.setToolTip(self.translator.t("attach_file"))
        self.attach_button.clicked.connect(self._on_attach_clicked)
        input_layout.addWidget(self.attach_button)

        self.image_mode_button = QPushButton("🎨")
        self.image_mode_button.setObjectName("ImageModeButton")
        self.image_mode_button.setCheckable(True)
        self.image_mode_button.setFixedSize(36, 36)
        self.image_mode_button.setCursor(Qt.PointingHandCursor)
        self.image_mode_button.setToolTip(self.translator.t("image_mode"))
        input_layout.addWidget(self.image_mode_button)

        self.message_input = MessageInput()
        self.message_input.setObjectName("MessageInput")
        self.message_input.setPlaceholderText(self.translator.t("type_message"))
        self.message_input.setFixedHeight(40)
        self.message_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.message_input.submitted.connect(self._on_enter_pressed)
        self.message_input.textChanged.connect(self._auto_grow_input)
        input_layout.addWidget(self.message_input, 1)

        self.send_button = QPushButton("➤")
        self.send_button.setObjectName("SendButton")
        self.send_button.setFixedSize(36, 36)
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.setToolTip(self.translator.t("send"))
        self.send_button.clicked.connect(self._on_send_button_clicked)
        input_layout.addWidget(self.send_button)

        self._input_wrapper_layout.addWidget(self.input_container)
        return wrapper

    def _auto_grow_input(self) -> None:
        doc_height = int(self.message_input.document().size().height())
        new_height = max(40, min(doc_height + 16, 200))
        self.message_input.setFixedHeight(new_height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Keep side margins proportional to the available width so the
        # layout stays comfortable to read at any window size.
        margin = max(16, min(60, int(self.width() * 0.08)))
        self.messages_layout.setContentsMargins(margin, 24, margin, 24)
        self._input_wrapper_layout.setContentsMargins(margin, 8, margin, 20)

    # ------------------------------------------------------------------ #
    # Attachments
    # ------------------------------------------------------------------ #
    def _on_attach_clicked(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_ATTACHMENT_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.translator.t("attach_file"),
            "",
            f"Supported files ({exts})",
        )
        self._attach_paths(paths)

    def _attach_paths(self, paths: List[str]) -> None:
        problems: List[str] = []
        for path in paths:
            problem = self._attach_path(path)
            if problem:
                problems.append(problem)
        if problems:
            QMessageBox.warning(self, self.translator.t("attach_problem"), "\n".join(problems))

    def _attach_path(self, path: str) -> Optional[str]:
        """Attach one file. Returns a human-readable problem string, or None on success."""
        name = Path(path).name
        if not is_supported_extension(path):
            return f"{name}: {self.translator.t('unsupported_file')}"
        if len(self.attached_files) >= MAX_ATTACHMENTS_PER_MESSAGE:
            return f"{name}: max {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message."
        if any(a.path == path for a in self.attached_files):
            return None  # already attached
        attached = extract_file(path)
        if attached.is_image and sum(1 for a in self.attached_files if a.is_image) >= MAX_IMAGES_PER_REQUEST:
            return f"{name}: at most {MAX_IMAGES_PER_REQUEST} images per message (Groq vision limit)."
        if not attached.is_valid:
            return f"{name}: {attached.error}"
        self.attached_files.append(attached)
        chip = FileChip(attached)
        chip.removed.connect(self._remove_attachment)
        self.chips_layout.insertWidget(self.chips_layout.count() - 1, chip)
        self.chips_row.setVisible(True)
        return None

    # Drag & drop files onto the window
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self._attach_paths(paths)
        event.acceptProposedAction()

    def _remove_attachment(self, path: str) -> None:
        self.attached_files = [a for a in self.attached_files if a.path != path]
        for i in range(self.chips_layout.count()):
            item = self.chips_layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, FileChip) and widget.attached.path == path:
                widget.deleteLater()
                break
        if not self.attached_files:
            self.chips_row.setVisible(False)

    def _clear_attachments(self) -> None:
        self.attached_files = []
        while self.chips_layout.count() > 1:  # keep the trailing stretch
            item = self.chips_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        self.chips_row.setVisible(False)

    # ------------------------------------------------------------------ #
    # Sending / receiving messages
    # ------------------------------------------------------------------ #
    def _on_enter_pressed(self) -> None:
        """Enter sends; it must never act as the Stop button."""
        if not self._is_generating:
            self._on_send_clicked()

    def _on_send_button_clicked(self) -> None:
        if self._is_generating:
            self.stop_requested.emit()
        else:
            self._on_send_clicked()

    def _on_send_clicked(self) -> None:
        if self._is_generating:
            return
        text = self.message_input.toPlainText().strip()
        attachments = [a for a in self.attached_files if a.is_valid]
        if not text and not attachments:
            return

        force_image = self.image_mode_button.isChecked()
        self.welcome_widget.setVisible(False)
        images = [a.image_path for a in attachments if a.is_image]
        names = [a.name for a in attachments if not a.is_image]
        self.add_message("user", text, images=images, attach_names=names)

        self.message_input.clear()
        self._clear_attachments()
        self.image_mode_button.setChecked(False)  # one-shot, like ChatGPT's "Create image"

        self.message_submitted.emit(text, attachments, force_image)

    def add_message(
        self,
        role: str,
        text: str,
        images: Optional[List[str]] = None,
        attach_names: Optional[List[str]] = None,
    ) -> MessageBubble:
        index = len(self._bubbles)
        bubble = MessageBubble(
            role, text, self.translator, index=index, images=images, attach_names=attach_names
        )
        bubble.edit_saved.connect(self._handle_edit_saved)
        bubble.regenerate_clicked.connect(self._handle_regenerate_clicked)
        self._bubbles.append(bubble)
        # insert before the trailing stretch item
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _truncate_bubbles(self, keep_count: int) -> None:
        """Removes every rendered bubble beyond `keep_count`, e.g. after an
        edit or a regenerate request invalidates everything that followed."""
        while len(self._bubbles) > keep_count:
            bubble = self._bubbles.pop()
            self.messages_layout.removeWidget(bubble)
            bubble.deleteLater()

    def _handle_edit_saved(self, index: int, new_text: str) -> None:
        if self._is_generating:
            return
        self._truncate_bubbles(index + 1)
        self.message_edited.emit(index, new_text)

    def _handle_regenerate_clicked(self, index: int) -> None:
        if self._is_generating:
            return
        self._truncate_bubbles(index)
        self.regenerate_requested.emit(index)

    def _set_generating(self, generating: bool) -> None:
        self._is_generating = generating
        self.send_button.setEnabled(True)  # doubles as the Stop button while generating
        self.send_button.setText("■" if generating else "➤")
        self.send_button.setToolTip(self.translator.t("stop" if generating else "send"))

    def begin_assistant_message(self, status: str = "") -> None:
        self.current_assistant_bubble = self.add_message("assistant", "")
        if status:
            self.current_assistant_bubble.set_status(status)
        self._set_generating(True)

    def set_assistant_status(self, text: str) -> None:
        if self.current_assistant_bubble is not None:
            self.current_assistant_bubble.set_status(text)

    def append_assistant_delta(self, delta: str) -> None:
        if self.current_assistant_bubble is not None:
            self.current_assistant_bubble.append_text(delta)
            self._scroll_to_bottom()

    def end_assistant_message(self, full_text: str = "", images: Optional[List[str]] = None) -> None:
        if self.current_assistant_bubble is not None:
            if full_text:
                self.current_assistant_bubble.set_text(full_text)
            if images:
                self.current_assistant_bubble.set_images(images)
        self.current_assistant_bubble = None
        self._set_generating(False)
        self._scroll_to_bottom()

    def show_error_message(self, message: str, partial: str = "") -> str:
        """Show an error in the pending bubble (keeping any partial text). Returns the text shown."""
        shown = (partial.rstrip() + "\n\n" if partial.strip() else "") + f"⚠ {message}"
        if self.current_assistant_bubble is not None:
            self.current_assistant_bubble.set_text(shown)
        else:
            self.add_message("assistant", shown)
        self.current_assistant_bubble = None
        self._set_generating(False)
        return shown

    def clear_messages(self) -> None:
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget and widget is not self.welcome_widget:
                widget.deleteLater()
        self.messages_layout.insertWidget(0, self.welcome_widget)
        self.welcome_widget.setVisible(True)
        self._clear_attachments()
        self._bubbles = []
        self.current_assistant_bubble = None
        self._set_generating(False)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def retranslate(self) -> None:
        self.message_input.setPlaceholderText(self.translator.t("type_message"))
        self.attach_button.setToolTip(self.translator.t("attach_file"))
        self.image_mode_button.setToolTip(self.translator.t("image_mode"))
        self.send_button.setToolTip(self.translator.t("stop" if self._is_generating else "send"))
        self.welcome_widget.deleteLater()
        self.welcome_widget = self._build_welcome_widget()
        self.messages_layout.insertWidget(0, self.welcome_widget)
