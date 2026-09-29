"""
ui/main_window.py

The top-level QMainWindow. Owns:
    * The Sidebar (thread list + language switcher).
    * The ChatWidget (message viewport + input bar).
    * The Settings dialog (Groq + OpenRouter keys, models, theme).
    * Chat thread state (in-memory + persisted to disk via config.save_history).
    * Chat (Groq) and image (OpenRouter) worker lifecycles: sending, streaming, cancelling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QComboBox,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from api.file_handler import attachments_to_records
from api.groq_client import ChatWorker
from api.intent import detect_image_intent
from api.messages import build_api_messages
from api.pipeline import ImageWorker
from config import (
    AVAILABLE_MODELS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    IMAGE_MODELS,
    VISION_MODELS,
    Translator,
    load_history,
    load_settings,
    save_history,
    save_settings,
)
from ui.chat_widget import ChatWidget
from ui.sidebar import Sidebar
from ui.styles import get_stylesheet


class SettingsDialog(QDialog):
    """Modal dialog for editing the API key, base URL, model, and theme."""

    def __init__(self, translator: Translator, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.translator = translator
        self.setWindowTitle(translator.t("settings"))
        self.setMinimumWidth(420)
        self._settings = dict(settings)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)

        self.api_key_input = QLineEdit(self._settings.get("api_key", ""))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("gsk_...")
        form.addRow("Groq " + self.translator.t("api_key"), self.api_key_input)

        self.base_url_input = QLineEdit(self._settings.get("base_url", ""))
        form.addRow("Groq Base URL", self.base_url_input)

        self.model_combo = self._make_combo(AVAILABLE_MODELS, self._settings.get("model", AVAILABLE_MODELS[0]))
        form.addRow(self.translator.t("model"), self.model_combo)

        self.vision_combo = self._make_combo(VISION_MODELS, self._settings.get("vision_model", VISION_MODELS[0]))
        form.addRow(self.translator.t("vision_model"), self.vision_combo)

        self.or_key_input = QLineEdit(self._settings.get("openrouter_api_key", ""))
        self.or_key_input.setEchoMode(QLineEdit.Password)
        self.or_key_input.setPlaceholderText("sk-or-v1-...")
        form.addRow(self.translator.t("openrouter_key"), self.or_key_input)

        self.image_combo = self._make_combo(IMAGE_MODELS, self._settings.get("image_model", IMAGE_MODELS[0]))
        form.addRow(self.translator.t("image_model"), self.image_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem(self.translator.t("dark_theme"), userData="dark")
        self.theme_combo.addItem(self.translator.t("light_theme"), userData="light")
        theme_index = self.theme_combo.findData(self._settings.get("theme", "dark"))
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)
        form.addRow(self.translator.t("theme"), self.theme_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton(self.translator.t("save"), QDialogButtonBox.AcceptRole)
        cancel_btn = buttons.addButton(self.translator.t("cancel"), QDialogButtonBox.RejectRole)
        save_btn.setObjectName("PrimaryButton")
        cancel_btn.setObjectName("SecondaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_combo(options: List[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)  # editable so a renamed/new model never needs a code change
        combo.addItems(options)
        if current and current not in options:
            combo.addItem(current)
        combo.setCurrentText(current)
        return combo

    def result_settings(self) -> dict:
        updated = dict(self._settings)
        updated["api_key"] = self.api_key_input.text().strip()
        updated["base_url"] = self.base_url_input.text().strip() or "https://api.groq.com/openai/v1"
        updated["model"] = self.model_combo.currentText().strip() or AVAILABLE_MODELS[0]
        updated["vision_model"] = self.vision_combo.currentText().strip() or VISION_MODELS[0]
        updated["openrouter_api_key"] = self.or_key_input.text().strip()
        updated["image_model"] = self.image_combo.currentText().strip() or IMAGE_MODELS[0]
        updated["theme"] = self.theme_combo.currentData()
        return updated


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.translator = Translator(self.settings.get("language", "en"))

        self.threads: List[Dict] = load_history()
        if not self.threads:
            self.threads = [self._make_new_thread()]
        self.active_thread_id: str = self.threads[0]["id"]

        self.worker: Optional[QThread] = None          # the generation currently shown in the UI
        self._workers: set = set()                      # strong refs until each QThread has really finished

        self.setWindowTitle(self.translator.t("app_title"))
        self.resize(self.settings.get("window_width", 1200), self.settings.get("window_height", 800))

        self._build_ui()
        self._apply_theme()
        self._apply_layout_direction()
        self._refresh_sidebar()
        self._load_active_thread_into_view()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar(self.translator, self.translator.language)
        self.sidebar.new_chat_requested.connect(self._on_new_chat)
        self.sidebar.thread_selected.connect(self._on_thread_selected)
        self.sidebar.thread_deleted.connect(self._on_thread_deleted)
        self.sidebar.thread_renamed.connect(self._on_thread_renamed)
        self.sidebar.language_changed.connect(self._on_language_changed)
        self.sidebar.settings_requested.connect(self._on_settings_requested)
        root_layout.addWidget(self.sidebar)

        self.chat_widget = ChatWidget(self.translator)
        self.chat_widget.message_submitted.connect(self._on_message_submitted)
        self.chat_widget.message_edited.connect(self._on_message_edited)
        self.chat_widget.regenerate_requested.connect(self._on_regenerate)
        self.chat_widget.stop_requested.connect(self._on_stop_requested)
        root_layout.addWidget(self.chat_widget, 1)

        self.setCentralWidget(central)

    def _apply_theme(self) -> None:
        app = QGuiApplication.instance()
        app.setStyleSheet(get_stylesheet(self.settings.get("theme", "dark")))

    def _apply_layout_direction(self) -> None:
        app = QGuiApplication.instance()
        app.setLayoutDirection(Qt.RightToLeft if self.translator.is_rtl() else Qt.LeftToRight)

    # ------------------------------------------------------------------ #
    # Thread helpers
    # ------------------------------------------------------------------ #
    def _make_new_thread(self) -> Dict:
        return {
            "id": str(uuid.uuid4()),
            "title": self.translator.t("untitled_chat") if hasattr(self, "translator") else "New conversation",
            "messages": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get_thread(self, thread_id: str) -> Optional[Dict]:
        for thread in self.threads:
            if thread["id"] == thread_id:
                return thread
        return None

    def _refresh_sidebar(self) -> None:
        display = [{"id": t["id"], "title": t["title"]} for t in self.threads]
        self.sidebar.set_threads(display, active_id=self.active_thread_id)

    def _persist(self) -> None:
        save_history(self.threads)
        save_settings(self.settings)

    def _load_active_thread_into_view(self) -> None:
        thread = self._get_thread(self.active_thread_id)
        self.chat_widget.clear_messages()
        if not thread:
            return
        if thread["messages"]:
            self.chat_widget.welcome_widget.setVisible(False)
        for message in thread["messages"]:
            attachments = message.get("attachments") or []
            if message["role"] == "user":
                images = [a["image"] for a in attachments if a.get("kind") == "image"]
            else:
                images = list(message.get("images") or [])
            names = [a["name"] for a in attachments if a.get("kind") == "document"]
            self.chat_widget.add_message(
                message["role"], message.get("content", ""), images=images, attach_names=names
            )

    # ------------------------------------------------------------------ #
    # Sidebar event handlers
    # ------------------------------------------------------------------ #
    def _on_new_chat(self) -> None:
        self._stop_generation_if_running()
        thread = self._make_new_thread()
        self.threads.insert(0, thread)
        self.active_thread_id = thread["id"]
        self._refresh_sidebar()
        self._load_active_thread_into_view()
        self._persist()

    def _on_thread_selected(self, thread_id: str) -> None:
        if thread_id == self.active_thread_id:
            return
        self._stop_generation_if_running()
        self.active_thread_id = thread_id
        self._load_active_thread_into_view()

    def _on_thread_deleted(self, thread_id: str) -> None:
        if thread_id == self.active_thread_id:
            self._stop_generation_if_running()
        self.threads = [t for t in self.threads if t["id"] != thread_id]
        if not self.threads:
            self.threads = [self._make_new_thread()]
        if thread_id == self.active_thread_id:
            self.active_thread_id = self.threads[0]["id"]
            self._load_active_thread_into_view()
        self._refresh_sidebar()
        self._persist()

    def _on_thread_renamed(self, thread_id: str, new_title: str) -> None:
        thread = self._get_thread(thread_id)
        if thread:
            thread["title"] = new_title
            self._persist()

    def _on_language_changed(self, code: str) -> None:
        self.translator.set_language(code)
        self.settings["language"] = code
        self._apply_layout_direction()
        self.setWindowTitle(self.translator.t("app_title"))
        self.sidebar.retranslate()
        self.chat_widget.retranslate()
        self._refresh_sidebar()
        self._persist()

    def _on_settings_requested(self) -> None:
        dialog = SettingsDialog(self.translator, self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            updated = dialog.result_settings()
            theme_changed = updated.get("theme") != self.settings.get("theme")
            self.settings.update(updated)
            if theme_changed:
                self._apply_theme()
            self._persist()

    # ------------------------------------------------------------------ #
    # Sending: routes each message to chat/vision (Groq) or image generation (OpenRouter)
    # ------------------------------------------------------------------ #
    def _on_message_submitted(self, text: str, attachments: list, force_image: bool) -> None:
        wants_image, cleaned = detect_image_intent(text)
        mode = "image" if (force_image or wants_image) else "chat"
        if not self._check_keys(mode):
            return

        thread = self._get_thread(self.active_thread_id)
        if thread is None:
            return

        thread["messages"].append(
            {
                "role": "user",
                "content": cleaned,
                "attachments": attachments_to_records(attachments),
                "mode": mode,
            }
        )
        if thread["title"] in (self.translator.t("untitled_chat"), "New conversation") and cleaned:
            thread["title"] = cleaned[:40] + ("..." if len(cleaned) > 40 else "")
            self._refresh_sidebar()

        self._persist()
        self._start_for_mode(thread, mode)

    def _on_message_edited(self, index: int, new_text: str) -> None:
        """A user edited an earlier message: drop everything after it and re-run from there."""
        self._stop_generation_if_running()
        thread = self._get_thread(self.active_thread_id)
        if thread is None or index >= len(thread["messages"]):
            return

        old = thread["messages"][index]
        mode = old.get("mode", "chat")
        if not self._check_keys(mode):
            return

        thread["messages"] = thread["messages"][:index]
        thread["messages"].append(
            {
                "role": "user",
                "content": new_text,
                "attachments": old.get("attachments", []),  # edits keep the original files
                "mode": mode,
            }
        )
        self._persist()
        self._start_for_mode(thread, mode)

    def _on_regenerate(self, index: int) -> None:
        """Re-run the model for the assistant message at `index`, discarding it and anything after."""
        self._stop_generation_if_running()
        thread = self._get_thread(self.active_thread_id)
        if thread is None or index <= 0 or index > len(thread["messages"]):
            return

        dropped = thread["messages"][index] if index < len(thread["messages"]) else {}
        mode = dropped.get("mode") or ("image" if dropped.get("kind") == "image" else "chat")
        if not self._check_keys(mode):
            return

        thread["messages"] = thread["messages"][:index]
        if not thread["messages"] or thread["messages"][-1].get("role") != "user":
            return
        self._persist()
        self._start_for_mode(thread, mode)

    def _check_keys(self, mode: str) -> bool:
        """Verify the key needed for `mode`; open Settings if it is missing."""
        if mode == "image":
            ok = bool(self.settings.get("openrouter_api_key", "").strip())
            title, body = self.translator.t("no_or_key_title"), self.translator.t("no_or_key_body")
        else:
            ok = bool(self.settings.get("api_key", "").strip())
            title, body = self.translator.t("no_api_key_title"), self.translator.t("no_api_key_body")
        if not ok:
            QMessageBox.warning(self, title, body)
            self._on_settings_requested()
        return ok

    def _start_for_mode(self, thread: Dict, mode: str) -> None:
        if mode == "image":
            self._start_image_worker(thread)
        else:
            self._start_chat_worker(thread)

    # ------------------------------------------------------------------ #
    # Chat / vision worker (Groq)
    # ------------------------------------------------------------------ #
    def _start_chat_worker(self, thread: Dict) -> None:
        api_messages, has_images = build_api_messages(thread["messages"])
        # Only the vision model can read images; everything else uses the text model.
        model = self.settings.get("vision_model") if has_images else self.settings.get("model")

        self.chat_widget.begin_assistant_message()
        worker = ChatWorker(
            api_key=self.settings.get("api_key", "").strip(),
            base_url=self.settings.get("base_url", "https://api.groq.com/openai/v1"),
            model=model,
            messages=api_messages,
            max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            fallback_model=self.settings.get("fallback_model"),
            thread_id=thread["id"],
        )
        worker.chunk_received.connect(lambda d, w=worker: self._on_chunk(w, d))
        worker.notice.connect(lambda n, w=worker: self._on_notice(w, n))
        worker.completed.connect(lambda t, w=worker: self._on_chat_completed(w, t))
        worker.error.connect(lambda m, partial, w=worker: self._on_chat_error(w, m, partial))
        self._launch(worker)

    def _on_chunk(self, w: QThread, delta: str) -> None:
        if self._is_visible(w):
            self.chat_widget.append_assistant_delta(delta)

    def _on_notice(self, w: QThread, text: str) -> None:
        if self._is_visible(w):
            self.chat_widget.set_assistant_status(text)

    def _on_chat_completed(self, w: QThread, full_text: str) -> None:
        thread = self._get_thread(w.thread_id)
        if thread is not None:
            if full_text.strip():
                thread["messages"].append({"role": "assistant", "content": full_text, "mode": "chat"})
                shown = full_text
            else:
                shown = (
                    "⚠ The model returned no text. It may have used its whole token budget thinking — "
                    "try again, or raise GROQ_MAX_OUTPUT_TOKENS in .env."
                )
                thread["messages"].append({"role": "assistant", "kind": "error", "content": shown, "mode": "chat"})
            self._persist()
            if self._is_visible(w):
                self.chat_widget.end_assistant_message(shown)
        self._release(w)

    def _on_chat_error(self, w: QThread, message: str, partial: str) -> None:
        self._record_error(w, message, partial, mode="chat")

    # ------------------------------------------------------------------ #
    # Image worker (OpenRouter; optionally Groq writes the prompt from a PDF/image first)
    # ------------------------------------------------------------------ #
    def _start_image_worker(self, thread: Dict) -> None:
        user_msg = thread["messages"][-1]
        atts = user_msg.get("attachments") or []
        doc_text = "\n\n".join(a.get("text", "") for a in atts if a.get("kind") == "document")
        image_paths = [a["image"] for a in atts if a.get("kind") == "image"]

        self.chat_widget.begin_assistant_message(self.translator.t("generating_image"))
        worker = ImageWorker(
            settings=dict(self.settings),
            user_text=user_msg.get("content", ""),
            doc_text=doc_text,
            image_paths=image_paths,
            thread_id=thread["id"],
        )
        worker.status.connect(lambda key, w=worker: self._on_image_status(w, key))
        worker.completed.connect(lambda prompt, paths, w=worker: self._on_image_completed(w, prompt, paths))
        worker.error.connect(lambda m, w=worker: self._on_image_error(w, m))
        self._launch(worker)

    def _on_image_status(self, w: QThread, key: str) -> None:
        if key.startswith("prompt_writer_failed:"):
            w.warning = key.split(":", 1)[1]  # shown after the result
            return
        if self._is_visible(w):
            self.chat_widget.set_assistant_status(self.translator.t(key))

    def _on_image_completed(self, w: QThread, prompt: str, paths: list) -> None:
        thread = self._get_thread(w.thread_id)
        if thread is not None:
            content = (
                f"**{self.translator.t('image_caption')}**\n\n"
                f"*{self.translator.t('prompt_used')}:* {prompt}"
            )
            warning = getattr(w, "warning", "")
            if warning:
                content += f"\n\n⚠ Prompt writer unavailable, used your text directly. ({warning[:160]})"
            thread["messages"].append(
                {"role": "assistant", "kind": "image", "content": content, "images": paths, "mode": "image"}
            )
            self._persist()
            if self._is_visible(w):
                self.chat_widget.end_assistant_message(content, images=paths)
        self._release(w)

    def _on_image_error(self, w: QThread, message: str) -> None:
        self._record_error(w, message, "", mode="image")

    # ------------------------------------------------------------------ #
    # Shared worker plumbing
    # ------------------------------------------------------------------ #
    def _launch(self, worker: QThread) -> None:
        self.worker = worker
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))  # QThread's built-in signal
        worker.start()

    def _is_visible(self, w: QThread) -> bool:
        """True if `w` is the generation the user is looking at right now."""
        return w is self.worker and w.thread_id == self.active_thread_id and not getattr(w, "_cancelled", False)

    def _release(self, w: QThread) -> None:
        if w is self.worker:
            self.worker = None

    def _record_error(self, w: QThread, message: str, partial: str, mode: str) -> None:
        if getattr(w, "_cancelled", False):
            self._release(w)
            return
        thread = self._get_thread(w.thread_id)
        if thread is not None:
            shown = (partial.rstrip() + "\n\n" if partial.strip() else "") + f"⚠ {message}"
            # Stored so bubble indices always match message indices (edit/regenerate rely on it),
            # but flagged so it is never sent back to the model.
            thread["messages"].append({"role": "assistant", "kind": "error", "content": shown, "mode": mode})
            self._persist()
            if self._is_visible(w):
                self.chat_widget.show_error_message(message, partial)
        self._release(w)

    def _on_stop_requested(self) -> None:
        w = self.worker
        if w is None:
            return
        w.stop()
        if isinstance(w, ImageWorker):
            # An HTTP image request can't be aborted mid-flight: discard its result instead.
            thread = self._get_thread(w.thread_id)
            if thread is not None:
                thread["messages"].append(
                    {"role": "assistant", "kind": "error", "content": "⏹ Stopped.", "mode": "image"}
                )
                self._persist()
            self.chat_widget.show_error_message("Stopped.")
            self.worker = None
        # ChatWorker: it emits `completed` with the partial text, handled normally above.

    def _stop_generation_if_running(self) -> None:
        """Called when the user leaves the running chat. Never blocks the UI thread."""
        w = self.worker
        if w is None:
            return
        w.stop()
        # Detach from the view; the worker's result is still saved to ITS OWN thread.
        self.worker = None

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        for w in list(self._workers):
            w.stop()
        for w in list(self._workers):
            if not w.wait(2000):
                w.terminate()  # last resort at app exit (e.g. a hung HTTP call)
                w.wait(500)
        self.settings["window_width"] = self.width()
        self.settings["window_height"] = self.height()
        self._persist()
        super().closeEvent(event)
