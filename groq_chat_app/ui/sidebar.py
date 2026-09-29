"""
ui/sidebar.py

The collapsible left sidebar: "New Chat" button, a search box, the list of
chat threads (with rename/delete via context menu), and a language switcher
combo box pinned to the bottom.

The expanded and collapsed states are two separate pages of a QStackedWidget
rather than a pile of individually hidden/shown widgets sharing one layout.
That keeps the collapse toggle bullet-proof: the sidebar's own width may
shrink to a slim icon rail, but it is never fully hidden, so there is always
a visible control to bring it back.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import SUPPORTED_LANGUAGES, Translator


class Sidebar(QWidget):
    """
    Signals:
        new_chat_requested()
        thread_selected(str): emitted with the thread_id
        thread_deleted(str): emitted with the thread_id
        thread_renamed(str, str): emitted with (thread_id, new_title)
        language_changed(str): emitted with the new language code
        collapse_toggled(): emitted when the collapse button is clicked
        settings_requested()
    """

    new_chat_requested = Signal()
    thread_selected = Signal(str)
    thread_deleted = Signal(str)
    thread_renamed = Signal(str, str)
    language_changed = Signal(str)
    collapse_toggled = Signal()
    settings_requested = Signal()

    EXPANDED_WIDTH = 280
    COLLAPSED_WIDTH = 60

    def __init__(self, translator: Translator, current_language: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.translator = translator
        self._expanded = True
        self.setFixedWidth(self.EXPANDED_WIDTH)
        self._build_ui(current_language)

    # ------------------------------------------------------------------ #
    def _build_ui(self, current_language: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(10)

        # Header row: collapse toggle + title. This row is always present
        # (never hidden) so the toggle is always reachable.
        header = QWidget()
        header.setObjectName("SidebarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 0, 4, 0)

        self.title_label = QLabel(self.translator.t("app_title"))
        self.title_label.setStyleSheet("font-weight: 700; font-size: 15px;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.collapse_button = QPushButton("⟨⟨")
        self.collapse_button.setObjectName("SidebarIconButton")
        self.collapse_button.setFixedSize(28, 28)
        self.collapse_button.setCursor(Qt.PointingHandCursor)
        self.collapse_button.setToolTip(self.translator.t("collapse_sidebar"))
        self.collapse_button.clicked.connect(self._toggle_collapsed)
        header_layout.addWidget(self.collapse_button)

        layout.addWidget(header)

        # A two-page stack: page 0 is the full sidebar body, page 1 is a
        # slim icon-only rail shown when collapsed.
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_expanded_page(current_language))
        self.stack.addWidget(self._build_collapsed_page())

        self._items_by_id: Dict[str, QListWidgetItem] = {}

    def _build_expanded_page(self, current_language: str) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)

        # New chat button
        self.new_chat_button = QPushButton(f"＋  {self.translator.t('new_chat')}")
        self.new_chat_button.setObjectName("NewChatButton")
        self.new_chat_button.setCursor(Qt.PointingHandCursor)
        self.new_chat_button.clicked.connect(self.new_chat_requested.emit)
        page_layout.addWidget(self.new_chat_button)

        # Search box
        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText(self.translator.t("search_chats"))
        self.search_box.textChanged.connect(self._filter_threads)
        page_layout.addWidget(self.search_box)

        # Thread list
        self.thread_list = QListWidget()
        self.thread_list.setObjectName("ThreadList")
        self.thread_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.thread_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thread_list.customContextMenuRequested.connect(self._show_context_menu)
        self.thread_list.itemClicked.connect(self._on_item_clicked)
        page_layout.addWidget(self.thread_list, 1)

        # Footer: settings + language selector
        footer = QVBoxLayout()
        footer.setSpacing(8)

        self.settings_button = QPushButton(f"⚙  {self.translator.t('settings')}")
        self.settings_button.setObjectName("NewChatButton")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        footer.addWidget(self.settings_button)

        lang_row = QHBoxLayout()
        self.language_label = QLabel(self.translator.t("language"))
        lang_row.addWidget(self.language_label)
        lang_row.addStretch(1)
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("LanguageCombo")
        for code, name in SUPPORTED_LANGUAGES.items():
            self.language_combo.addItem(name, userData=code)
        index = self.language_combo.findData(current_language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self.language_combo)
        footer.addLayout(lang_row)

        page_layout.addLayout(footer)
        return page

    def _build_collapsed_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.setAlignment(Qt.AlignHCenter)

        self.collapsed_new_chat_button = QPushButton("＋")
        self.collapsed_new_chat_button.setObjectName("SidebarIconButton")
        self.collapsed_new_chat_button.setFixedSize(36, 36)
        self.collapsed_new_chat_button.setCursor(Qt.PointingHandCursor)
        self.collapsed_new_chat_button.setToolTip(self.translator.t("new_chat"))
        self.collapsed_new_chat_button.clicked.connect(self.new_chat_requested.emit)
        page_layout.addWidget(self.collapsed_new_chat_button, 0, Qt.AlignHCenter)

        page_layout.addStretch(1)

        self.collapsed_settings_button = QPushButton("⚙")
        self.collapsed_settings_button.setObjectName("SidebarIconButton")
        self.collapsed_settings_button.setFixedSize(36, 36)
        self.collapsed_settings_button.setCursor(Qt.PointingHandCursor)
        self.collapsed_settings_button.setToolTip(self.translator.t("settings"))
        self.collapsed_settings_button.clicked.connect(self.settings_requested.emit)
        page_layout.addWidget(self.collapsed_settings_button, 0, Qt.AlignHCenter)

        return page

    # ------------------------------------------------------------------ #
    # Collapse / expand (a slim icon rail — the sidebar itself is NEVER
    # fully hidden, so there is always a way to bring it back).
    # ------------------------------------------------------------------ #
    def _toggle_collapsed(self) -> None:
        self._expanded = not self._expanded
        self.setFixedWidth(self.EXPANDED_WIDTH if self._expanded else self.COLLAPSED_WIDTH)
        self.stack.setCurrentIndex(0 if self._expanded else 1)
        self.title_label.setText(self.translator.t("app_title") if self._expanded else "")

        self.collapse_button.setText("⟨⟨" if self._expanded else "⟩⟩")
        self.collapse_button.setToolTip(
            self.translator.t("collapse_sidebar") if self._expanded else self.translator.t("expand_sidebar")
        )
        self.collapse_toggled.emit()

    # ------------------------------------------------------------------ #
    # Thread list management
    # ------------------------------------------------------------------ #
    def set_threads(self, threads: List[dict], active_id: Optional[str] = None) -> None:
        """threads: list of {"id": str, "title": str} in most-recent-first order."""
        self.thread_list.clear()
        self._items_by_id.clear()
        for thread in threads:
            item = QListWidgetItem(thread["title"] or self.translator.t("untitled_chat"))
            item.setData(Qt.UserRole, thread["id"])
            self.thread_list.addItem(item)
            self._items_by_id[thread["id"]] = item
            if thread["id"] == active_id:
                self.thread_list.setCurrentItem(item)

    def set_active_thread(self, thread_id: str) -> None:
        item = self._items_by_id.get(thread_id)
        if item:
            self.thread_list.setCurrentItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        thread_id = item.data(Qt.UserRole)
        if thread_id:
            self.thread_selected.emit(thread_id)

    def _filter_threads(self, text: str) -> None:
        text_lower = text.lower().strip()
        for i in range(self.thread_list.count()):
            item = self.thread_list.item(i)
            item.setHidden(text_lower not in item.text().lower())

    def _show_context_menu(self, position) -> None:
        item = self.thread_list.itemAt(position)
        if item is None:
            return
        thread_id = item.data(Qt.UserRole)

        menu = QMenu(self)
        rename_action = menu.addAction(self.translator.t("rename_chat"))
        delete_action = menu.addAction(self.translator.t("delete_chat"))
        chosen = menu.exec(self.thread_list.mapToGlobal(position))

        if chosen == rename_action:
            self._rename_thread(thread_id, item)
        elif chosen == delete_action:
            self._delete_thread(thread_id, item)

    def _rename_thread(self, thread_id: str, item: QListWidgetItem) -> None:
        new_title, ok = QInputDialog.getText(
            self, self.translator.t("rename_chat"), self.translator.t("rename_chat"),
            text=item.text(),
        )
        if ok and new_title.strip():
            item.setText(new_title.strip())
            self.thread_renamed.emit(thread_id, new_title.strip())

    def _delete_thread(self, thread_id: str, item: QListWidgetItem) -> None:
        confirm = QMessageBox.question(
            self,
            self.translator.t("confirm_delete_title"),
            self.translator.t("confirm_delete_body"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            row = self.thread_list.row(item)
            self.thread_list.takeItem(row)
            self._items_by_id.pop(thread_id, None)
            self.thread_deleted.emit(thread_id)

    # ------------------------------------------------------------------ #
    def _on_language_changed(self, index: int) -> None:
        code = self.language_combo.itemData(index)
        if code:
            self.language_changed.emit(code)

    def retranslate(self) -> None:
        self.title_label.setText(self.translator.t("app_title") if self._expanded else "")
        self.new_chat_button.setText(f"＋  {self.translator.t('new_chat')}")
        self.search_box.setPlaceholderText(self.translator.t("search_chats"))
        self.settings_button.setText(f"⚙  {self.translator.t('settings')}")
        self.language_label.setText(self.translator.t("language"))
        self.collapsed_new_chat_button.setToolTip(self.translator.t("new_chat"))
        self.collapsed_settings_button.setToolTip(self.translator.t("settings"))
        self.collapse_button.setToolTip(
            self.translator.t("collapse_sidebar") if self._expanded else self.translator.t("expand_sidebar")
        )
