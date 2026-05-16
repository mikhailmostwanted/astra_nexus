from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.dom_probe import evaluate_script
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver.selectors import PROMPT_INPUT_SELECTORS

logger = logging.getLogger(__name__)


class NoDriverArtifactInputPromptBoxNotFoundError(NoDriverProviderError):
    status = "artifact_input_prompt_box_not_found"
    user_message = "Поле ввода ChatGPT (composer) не найдено для загрузки артефакта."
    action = "Проверь, что ChatGPT открыт и интерфейс загрузки доступен."


class ArtifactUploader:
    def __init__(self, tab: Any, workspace_path: Path | None = None) -> None:
        self.tab = tab
        self.workspace_path = workspace_path
        self.debug_data: dict[str, Any] = {}

    async def upload(self, file_paths: list[Path]) -> bool:
        """
        Загружает файлы в ChatGPT Web.
        """
        if not file_paths:
            return True

        self.debug_data["timestamp"] = datetime.now(UTC).isoformat()
        self.debug_data["files_to_upload"] = [str(p) for p in file_paths]

        # 1. Поиск composer root и кнопки загрузки
        probe_result = await self._probe_upload_elements()
        self.debug_data["initial_probe"] = probe_result

        if not probe_result.get("composer_found"):
            # Сохраняем расширенный дебаг перед ошибкой
            await self._dump_debug_info("artifact_input_upload_failed_no_composer")
            raise NoDriverArtifactInputPromptBoxNotFoundError(details=probe_result)

        # 2. Если есть прямой input[type=file], используем его
        file_input_selector = probe_result.get("file_input_selector")
        if file_input_selector:
            logger.info(f"Found direct file input: {file_input_selector}")
            success = await self._handle_file_input(file_input_selector, file_paths)
            if success:
                await self._dump_debug_info("artifact_input_upload_success_direct")
                return True

        # 3. Попытка нажать на кнопку "+" или "Attach"
        attach_button_selector = probe_result.get("attach_button_selector")
        if attach_button_selector:
            logger.info(f"Clicking attach button: {attach_button_selector}")
            await self._click_selector(attach_button_selector)
            await asyncio.sleep(1.0)  # Ждем появления меню или инпута

            # Повторный поиск после клика
            probe_after_click = await self._probe_upload_elements()
            self.debug_data["probe_after_attach_click"] = probe_after_click

            file_input_selector = probe_after_click.get("file_input_selector")
            if file_input_selector:
                success = await self._handle_file_input(file_input_selector, file_paths)
                if success:
                    await self._dump_debug_info("artifact_input_upload_success_after_click")
                    return True

            # Поиск в меню
            menu_upload_selector = probe_after_click.get("menu_upload_selector")
            if menu_upload_selector:
                logger.info(f"Clicking menu upload item: {menu_upload_selector}")
                await self._click_selector(menu_upload_selector)
                await asyncio.sleep(1.0)

                probe_after_menu = await self._probe_upload_elements()
                self.debug_data["probe_after_menu_click"] = probe_after_menu
                file_input_selector = probe_after_menu.get("file_input_selector")
                if file_input_selector:
                    success = await self._handle_file_input(file_input_selector, file_paths)
                    if success:
                        await self._dump_debug_info("artifact_input_upload_success_after_menu")
                        return True

        await self._dump_debug_info("artifact_input_upload_failed")
        return False

    async def _handle_file_input(self, selector: str, file_paths: list[Path]) -> bool:
        try:
            element = await self.tab.query_selector(selector)
            if element:
                # В nodriver метод для загрузки файлов может отличаться в зависимости от версии
                # Обычно это set_input_files или аналогичный через CDP
                await element.send_file(*[str(p) for p in file_paths])
                self.debug_data["upload_result"] = {"status": "success", "selector": selector}
                return True
        except Exception as e:
            logger.error(f"Failed to upload via {selector}: {e}")
            self.debug_data["upload_result"] = {
                "status": "error",
                "error": str(e),
                "selector": selector,
            }
        return False

    async def _click_selector(self, selector: str):
        try:
            element = await self.tab.query_selector(selector)
            if element:
                await element.click()
        except Exception as e:
            logger.warning(f"Failed to click {selector}: {e}")

    async def _probe_upload_elements(self) -> dict[str, Any]:
        script = self._build_probe_script()
        return await evaluate_script(self.tab, script)

    def _build_probe_script(self) -> str:
        prompt_selectors_json = json.dumps(PROMPT_INPUT_SELECTORS)
        return f"""
        (() => {{
            const promptSelectors = {prompt_selectors_json};

            function isVisible(el) {{
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none';
            }}

            let composer = null;
            for (const sel of promptSelectors) {{
                const el = document.querySelector(sel);
                if (el && isVisible(el)) {{
                    composer = el;
                    break;
                }}
            }}

            if (!composer) return {{ composer_found: false }};

            // Ищем ближайший контейнер (composer root), чтобы не считать document.body корнем
            let composerRoot = composer.closest('[data-testid="composer-root"], .composer-parent, form, div[role="presentation"]');
            if (!composerRoot || composerRoot === document.body) {{
                composerRoot = composer.parentElement;
            }}

            const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
            const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));

            // Ищем кнопку "+" или "Attach"
            const attachButton = buttons.find(b => {{
                const text = (b.innerText || b.ariaLabel || "").toLowerCase();
                return isVisible(b) && (text.includes("attach") || text.includes("upload") || b.querySelector('svg'));
            }});

            const menuUploadItem = buttons.find(b => {{
                const text = (b.innerText || b.ariaLabel || "").toLowerCase();
                return isVisible(b) && (text.includes("file") || text.includes("computer"));
            }});

            return {{
                composer_found: true,
                composer_tag: composer.tagName,
                composer_root_tag: composerRoot ? composerRoot.tagName : null,
                composer_root_html: composerRoot ? composerRoot.outerHTML : null,
                file_input_selector: fileInputs.length > 0 ? "input[type='file']" : null,
                attach_button_selector: attachButton ? (attachButton.getAttribute('data-testid') ? 'button[data-testid="' + attachButton.getAttribute('data-testid') + '"]' : (attachButton.ariaLabel ? "button[aria-label='" + attachButton.ariaLabel + "']" : null)) : null,
                menu_upload_selector: menuUploadItem ? (menuUploadItem.getAttribute('data-testid') ? 'button[data-testid="' + menuUploadItem.getAttribute('data-testid') + '"]' : null) : null,
                all_file_inputs: fileInputs.map(i => ({{ id: i.id, className: i.className }})),
                all_buttons: buttons.filter(isVisible).map(b => ({{ text: b.innerText, aria: b.ariaLabel, testid: b.getAttribute('data-testid') }}))
            }};
        }})()
        """

    async def _dump_debug_info(self, suffix: str):
        if not self.workspace_path:
            return

        debug_dir = self.workspace_path / "debug" / "artifact_upload"
        debug_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Page info
            (debug_dir / "page_url.txt").write_text(await self.tab.url, encoding="utf-8")
            (debug_dir / "page_title.txt").write_text(await self.tab.title, encoding="utf-8")
            (debug_dir / "document_ready_state.txt").write_text(
                str(await self.tab.evaluate("document.readyState")), encoding="utf-8"
            )

            # HTML
            html = await self.tab.get_content()
            (debug_dir / "full_page_before_upload.html").write_text(html, encoding="utf-8")

            probe = self.debug_data.get("initial_probe", {})
            if probe.get("composer_root_html"):
                (debug_dir / "composer_root_before_upload.html").write_text(
                    probe["composer_root_html"], encoding="utf-8"
                )

            # JSONs
            probe = self.debug_data.get("initial_probe", {})
            (debug_dir / "all_buttons_before_upload.json").write_text(
                json.dumps(probe.get("all_buttons", []), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (debug_dir / "all_file_inputs_before_upload.json").write_text(
                json.dumps(probe.get("all_file_inputs", []), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Near composer buttons (can be same as all buttons in our simple probe for now)
            (debug_dir / "near_composer_buttons.json").write_text(
                json.dumps(probe.get("all_buttons", []), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            upload_candidates = {
                "file_input_selector": probe.get("file_input_selector"),
                "attach_button_selector": probe.get("attach_button_selector"),
                "menu_upload_selector": probe.get("menu_upload_selector"),
            }
            (debug_dir / "upload_candidates.json").write_text(
                json.dumps(upload_candidates, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            (debug_dir / "artifact_input_uploader_debug.json").write_text(
                json.dumps(self.debug_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            (debug_dir / "artifact_input_upload_result.json").write_text(
                json.dumps(self.debug_data.get("upload_result", {}), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        except Exception as e:
            logger.warning(f"Failed to dump debug info: {e}")
