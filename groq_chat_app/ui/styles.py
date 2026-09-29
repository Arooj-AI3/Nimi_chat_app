"""
ui/styles.py

QSS stylesheets that give the app a modern, ChatGPT-inspired look, in both
dark and light variants. Colors are defined once as constants and interpolated
into the QSS template so the two themes stay visually consistent.
"""

from __future__ import annotations

DARK_COLORS = {
    "bg_primary": "#212121",
    "bg_sidebar": "#171717",
    "bg_input": "#2f2f2f",
    "bg_hover": "#2a2a2a",
    "bg_active": "#343434",
    "bg_bubble_user": "#2f2f2f",
    "bg_bubble_assistant": "#212121",
    "text_primary": "#ececec",
    "text_secondary": "#a3a3a3",
    "text_muted": "#6e6e6e",
    "border": "#3a3a3a",
    "accent": "#10a37f",
    "accent_hover": "#0d8c6d",
    "danger": "#e35d5d",
    "chip_bg": "#3a3a3a",
    "scrollbar": "#4a4a4a",
}

LIGHT_COLORS = {
    "bg_primary": "#ffffff",
    "bg_sidebar": "#f7f7f8",
    "bg_input": "#f0f0f2",
    "bg_hover": "#ececec",
    "bg_active": "#e2e2e2",
    "bg_bubble_user": "#f0f0f2",
    "bg_bubble_assistant": "#ffffff",
    "text_primary": "#1e1e1e",
    "text_secondary": "#5d5d5d",
    "text_muted": "#8e8e8e",
    "border": "#e0e0e0",
    "accent": "#10a37f",
    "accent_hover": "#0d8c6d",
    "danger": "#d64545",
    "chip_bg": "#e5e5e7",
    "scrollbar": "#c9c9c9",
}

QSS_TEMPLATE = """
QWidget {{
    background-color: {bg_primary};
    color: {text_primary};
    font-family: "Segoe UI", "Noto Sans", "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
}}

/* ---------------- Sidebar ---------------- */
#Sidebar {{
    background-color: {bg_sidebar};
    border-right: 1px solid {border};
}}

#SidebarHeader {{
    background-color: transparent;
}}

QPushButton#NewChatButton {{
    background-color: transparent;
    color: {text_primary};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}}
QPushButton#NewChatButton:hover {{
    background-color: {bg_hover};
}}

QLineEdit#SearchBox {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 10px;
    color: {text_primary};
}}

QListWidget#ThreadList {{
    background-color: transparent;
    border: none;
    outline: 0;
}}
QListWidget#ThreadList::item {{
    padding: 10px 10px;
    border-radius: 8px;
    margin: 2px 4px;
    color: {text_primary};
}}
QListWidget#ThreadList::item:selected {{
    background-color: {bg_active};
}}
QListWidget#ThreadList::item:hover {{
    background-color: {bg_hover};
}}

QPushButton#SidebarIconButton {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
    color: {text_secondary};
}}
QPushButton#SidebarIconButton:hover {{
    background-color: {bg_hover};
    color: {text_primary};
}}

QComboBox#LanguageCombo {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 8px;
    color: {text_primary};
}}
QComboBox#LanguageCombo QAbstractItemView {{
    background-color: {bg_sidebar};
    color: {text_primary};
    selection-background-color: {bg_active};
    border: 1px solid {border};
}}

/* ---------------- Chat area ---------------- */
#ChatScrollArea {{
    background-color: {bg_primary};
    border: none;
}}

#WelcomeTitle {{
    font-size: 26px;
    font-weight: 700;
    color: {text_primary};
}}
#WelcomeSubtitle {{
    font-size: 14px;
    color: {text_secondary};
}}

#MessageBubbleUser {{
    background-color: {bg_bubble_user};
    border-radius: 16px;
}}
#MessageBubbleAssistant {{
    background-color: {bg_bubble_assistant};
    border-radius: 16px;
}}

#SenderLabel {{
    background-color: transparent;
    font-weight: 700;
    font-size: 13px;
    color: {text_secondary};
}}

QTextBrowser#MessageText {{
    background-color: transparent;
    border: none;
    color: {text_primary};
    font-size: 14.5px;
    selection-background-color: {bg_active};
}}

QPushButton#BubbleIconButton {{
    background-color: transparent;
    border: 1px solid {border};
    border-radius: 7px;
    padding: 0px;
    color: {text_secondary};
    font-size: 13px;
}}
QPushButton#BubbleIconButton:hover {{
    background-color: {bg_hover};
    color: {text_primary};
}}
QPushButton#BubbleIconButton:disabled {{
    color: {accent};
    border-color: {accent};
}}

QTextEdit#EditMessageInput {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 8px 10px;
    color: {text_primary};
    font-size: 14.5px;
}}

/* ---------------- Input area ---------------- */
#InputContainer {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 18px;
}}

QTextEdit#MessageInput {{
    background-color: transparent;
    border: none;
    color: {text_primary};
    font-size: 14.5px;
    padding: 4px;
}}

QPushButton#AttachButton, QPushButton#SendButton {{
    border: none;
    border-radius: 16px;
    padding: 6px;
}}
QPushButton#AttachButton {{
    background-color: transparent;
    color: {text_secondary};
}}
QPushButton#AttachButton:hover {{
    background-color: {bg_hover};
    color: {text_primary};
}}
QPushButton#ImageModeButton {{
    border: none;
    border-radius: 16px;
    padding: 6px;
    background-color: transparent;
    color: {text_secondary};
}}
QPushButton#ImageModeButton:hover {{
    background-color: {bg_hover};
    color: {text_primary};
}}
QPushButton#ImageModeButton:checked {{
    background-color: {accent};
    color: #ffffff;
}}
QLabel#ImageThumb {{
    border: 1px solid {border};
    border-radius: 10px;
    background-color: {bg_hover};
}}
QPushButton#ImageSaveButton {{
    background-color: transparent;
    border: 1px solid {border};
    border-radius: 8px;
    color: {text_secondary};
    padding: 3px 10px;
    font-size: 12px;
}}
QPushButton#ImageSaveButton:hover {{
    color: {text_primary};
    background-color: {bg_hover};
}}
QLabel#AttachNote {{
    color: {text_secondary};
    font-size: 12px;
    background-color: transparent;
}}
QWidget#ImageBox {{
    background-color: transparent;
}}
QPushButton#SendButton {{
    background-color: {accent};
    color: #ffffff;
}}
QPushButton#SendButton:hover {{
    background-color: {accent_hover};
}}
QPushButton#SendButton:disabled {{
    background-color: {bg_active};
    color: {text_muted};
}}

/* File chips */
#FileChip {{
    background-color: {chip_bg};
    border-radius: 8px;
    padding: 4px 8px;
}}
#FileChipLabel {{
    color: {text_primary};
    font-size: 12.5px;
}}
QPushButton#FileChipRemove {{
    background-color: transparent;
    border: none;
    color: {text_secondary};
    font-weight: 700;
}}
QPushButton#FileChipRemove:hover {{
    color: {danger};
}}

/* ---------------- Settings dialog ---------------- */
QDialog#SettingsDialog {{
    background-color: {bg_primary};
}}
QLabel#SettingsSectionLabel {{
    font-weight: 700;
    color: {text_primary};
    font-size: 14px;
}}
QLineEdit, QComboBox {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 7px 10px;
    color: {text_primary};
}}
QComboBox QAbstractItemView {{
    background-color: {bg_input};
    color: {text_primary};
    selection-background-color: {bg_active};
}}
QPushButton#PrimaryButton {{
    background-color: {accent};
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton#PrimaryButton:hover {{
    background-color: {accent_hover};
}}
QPushButton#SecondaryButton {{
    background-color: transparent;
    color: {text_primary};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 18px;
}}
QPushButton#SecondaryButton:hover {{
    background-color: {bg_hover};
}}

/* ---------------- Scrollbars ---------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {scrollbar};
    border-radius: 5px;
    min-width: 24px;
}}

QToolTip {{
    background-color: {bg_active};
    color: {text_primary};
    border: 1px solid {border};
    padding: 4px 6px;
}}
"""


def get_stylesheet(theme: str = "dark") -> str:
    """Returns the fully-interpolated QSS string for the requested theme."""
    colors = DARK_COLORS if theme != "light" else LIGHT_COLORS
    return QSS_TEMPLATE.format(**colors)
