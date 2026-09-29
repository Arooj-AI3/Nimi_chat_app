# Groq Chat — desktop AI assistant (chat · vision · PDF · image generation)

A ChatGPT-style PySide6 app:

| You do this | What happens | Provider |
|---|---|---|
| Type a question | Streaming text answer | Groq (`openai/gpt-oss-120b`) |
| Attach a PDF / .txt / .csv / .py … and ask | Answer grounded in the file | Groq |
| Attach a photo / screenshot and ask about it | Image understanding + OCR | Groq (`qwen/qwen3.8-27b`) |
| "draw a logo for my cafe" or click 🎨 or type `/image …` | Generates an image | OpenRouter (`inclusionai/ming-image-0.1-design`) |
| Attach a PDF + "make an image from these requirements" | Groq distils the PDF into an image prompt, OpenRouter renders it | both |
| Attach a photo + "make a poster like this" | Vision model describes it → image is generated from that description | both |

## Setup
```bash
python -m venv .venv && .venv\Scripts\activate     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # macOS/Linux: cp .env.example .env
# edit .env: GROQ_API_KEY and OPENROUTER_API_KEY
python main.py
```
Keys can also be typed into **Settings** inside the app. Keys that come from `.env` are never copied into the app's settings file.

## Using it
* **📎 / drag & drop** files onto the window (max 6 files, max 3 images per message).
* **🎨** turns the next message into an image request (or just write "generate/draw/create … image/logo/poster…", or start with `/image`). `/chat` forces a normal text answer.
* **■** stops a running answer. **↻** regenerates (images too). **✎** edits an earlier message.
* Generated images are shown inline; click to open, **Save image** to export. They are stored in `~/.groq_chat_app/images/`.

## Why you saw "limit" errors and random failures (all fixed)
1. **Dead models.** Groq shut down `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` (2026-08-16), `qwen3-32b` and Llama-4 Scout (2026-07-17), and older ones. The app defaulted to a dead model. Defaults and the model list are now current, and old saved settings are migrated automatically.
2. **Images were not supported at all** — they were silently ignored or pushed through a text-only model. Images now go to Groq's vision model (`qwen/qwen3.8-27b`): max **3 images per request**, each costs ~**2048 tokens**, so they are downscaled and only re-sent when relevant.
3. **Whole history re-sent every turn** → per-minute token limits. Requests are now budgeted (`MAX_CONTEXT_CHARS`), documents are capped (`MAX_ATTACHMENT_CHARS`), and old attachments are replaced by a placeholder.
4. **Error messages were guessed from substrings** ("401" anywhere → "bad key"). Errors are now classified by HTTP status and include the provider's real message (per-minute vs daily limit, too many images, model retired …).
5. On 429 / 413 / model-missing, a text request is retried once on `GROQ_FALLBACK_MODEL`.

## Other red flags fixed
* `finished(str)` signal shadowed `QThread.finished` → renamed.
* Switching chat mid-answer appended the reply to the **wrong chat**, and the QThread could be destroyed while running. Replies now belong to their own chat; threads are tracked until they really finish.
* Follow-up questions about a PDF lost the PDF (only the first turn had it). The latest document now stays in context.
* Error bubbles broke edit/regenerate (bubble index ≠ message index). Errors are stored (never sent to the model) so indices stay aligned.
* Invalid attachments were dropped silently — you now get a clear message (too large, scanned PDF with no text, password-protected, corrupt image).
* PyPDF2 (deprecated) → `pypdf`. Scanned PDFs are detected instead of sending an empty prompt.
* No request timeout; no Stop button; Enter could not be told apart from Stop — all fixed.
* `settings.json` / `history.json` were written non-atomically (crash = corrupted file) → atomic writes; settings file is `0600`.
* Reload showed the full extracted PDF text as the user message → now shows the file name.
* Stale `XAI_API_KEY` docs, unpinned requirements, no `.gitignore`, no tests, no README.

## Image models — important
`inclusionai/ming-image-0.1-design-layer2` **does not exist**. OpenRouter's real slugs:
* `inclusionai/ming-image-0.1-design` — text → image (**default**)
* `inclusionai/ming-image-0.1-design-layer` — splits an existing design into RGBA layers; needs exactly one input image, cannot create a picture from text.

Any OpenRouter image model that works with `POST /api/v1/images` can be typed into Settings → Image Model.

## Security
* **Never commit `.env`** (it is git-ignored). Revoke any key that was ever pasted into a chat, ticket or commit and create a new one.
* Upload data goes only to Groq/OpenRouter.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
41 tests cover intent detection, error mapping, PDF/image handling, request budgeting, the OpenRouter client, settings migration, and headless end-to-end UI flows (chat, vision, fallback, text→image, PDF→image, error handling).

## Tunables (`.env`)
`GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `GROQ_VISION_MODEL`, `GROQ_MAX_OUTPUT_TOKENS`, `OPENROUTER_IMAGE_MODEL`, `OPENROUTER_IMAGE_EDIT_MODEL`, `MAX_ATTACHMENT_CHARS`, `MAX_CONTEXT_CHARS`.
If you still hit per-minute limits on Groq's free tier, lower `MAX_ATTACHMENT_CHARS` / `GROQ_MAX_OUTPUT_TOKENS`.
