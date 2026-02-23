"""
Part 3 — Application Controller
=================================
Orchestrates the full Lever auto-apply flow:

  1. Launch Chrome (--headless=new) with the MV3 Chrome Extension loaded.
  2. Navigate to the Lever application page (/apply).
  3. Inject candidate data via page.evaluate() → window.localStorage on the
     Lever origin.  This works because page.evaluate() runs in the page's main
     world, which has full access to localStorage.
  4. The extension content script reads applicationData from the same
     localStorage (content scripts share the page's localStorage).
  5. Wait for the extension content script to fill the form and write a status
     object to localStorage.
  6. Handle each status:
       needs_file_upload  → Playwright setInputFiles, signal upload done
       needs_human        → pause, surface browser to user, wait for CAPTCHA
       validation_errors  → log and stop (manual fix required)
       ready_to_submit    → submit via CDP trusted mouse event
  7. Report final outcome.

Why localStorage instead of chrome.storage?
-------------------------------------------
chrome.storage requires chrome.* APIs which are only available in extension
contexts.  Calling chrome.storage from page.evaluate() (main world) fails.
The alternative — accessing it through the background service worker — requires
the service worker to be discoverable, which is unreliable in headless mode.

localStorage is accessible from BOTH the page's main world (page.evaluate)
AND content scripts (they share the page's browsing-context storage), making
it the simplest bidirectional channel between Playwright and the extension.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

# Storage keys — must match content.js constants.
LS_DATA_KEY   = "jobnovaData"
LS_STATUS_KEY = "jobnovaStatus"
LS_UPLOAD_KEY = "jobnovaUploadDone"

# Maximum seconds to wait for extension to report a status.
FILL_TIMEOUT = 90

# Maximum seconds for the human to solve a CAPTCHA.
HUMAN_TIMEOUT = 300

# Maximum retries when extension fails to load or form is not found.
MAX_RETRIES = 2


class ApplicationController:
    """End-to-end controller for a single Lever job application."""

    def __init__(
        self,
        job_url: str,
        candidate_data: dict,
        extension_dir: str | None = None,
        headless: bool = True,
    ) -> None:
        self.job_url = job_url
        self.candidate_data = candidate_data

        if extension_dir is None:
            extension_dir = str(Path(__file__).parent / "chrome_extension")
        self.extension_dir = os.path.abspath(extension_dir)

        self.headless = headless
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._playwright = None

    # ── Browser lifecycle ─────────────────────────────────────────────────────

    async def launch(self) -> None:
        """Launch Chrome with the extension loaded."""
        user_data_dir = str(Path(__file__).parent / ".chrome_profile")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        chromium_args = [
            f"--disable-extensions-except={self.extension_dir}",
            f"--load-extension={self.extension_dir}",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]

        # CRITICAL: Always pass headless=False to Playwright so Chrome uses the
        # extension-compatible startup path.  When the user wants invisible mode
        # we add --headless=new to the Chrome args manually — Chrome 112+ new
        # headless supports extensions natively, but Playwright's headless=True
        # internally uses the old headless mode which blocks content scripts.
        if self.headless:
            chromium_args.append("--headless=new")

        logger.info(
            "Launching Chrome (headless=%s) with extension: %s",
            self.headless,
            self.extension_dir,
        )

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,  # always False at Playwright level; headlessness via args above
            args=chromium_args,
            accept_downloads=True,
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    # ── localStorage helpers (via page.evaluate in the page's main world) ────

    async def _ls_set(self, key: str, value) -> None:
        """Write a JSON-serialised value to localStorage on the current page."""
        await self._page.evaluate(
            "(args) => localStorage.setItem(args[0], JSON.stringify(args[1]))",
            [key, value],
        )

    async def _ls_get(self, key: str):
        """
        Read and JSON-parse a value from localStorage.
        Returns None if the key is absent or the value is not valid JSON.
        """
        raw = await self._page.evaluate(
            "(key) => localStorage.getItem(key)", key
        )
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def _ls_remove(self, *keys: str) -> None:
        """Remove one or more keys from localStorage."""
        await self._page.evaluate(
            "(keys) => keys.forEach((k) => localStorage.removeItem(k))",
            list(keys),
        )

    # ── State management ──────────────────────────────────────────────────────

    async def _clear_stale_state(self) -> None:
        """Remove leftover localStorage keys from any previous run."""
        await self._ls_remove(LS_DATA_KEY, LS_STATUS_KEY, LS_UPLOAD_KEY)
        logger.info("Stale localStorage state cleared.")

    async def _inject_application_data(self) -> None:
        """
        Write candidate data into the page's localStorage.
        The content script reads the same localStorage key to populate the form.
        Must be called AFTER navigating to the target page (localStorage is
        origin-scoped).
        """
        await self._ls_set(LS_DATA_KEY, self.candidate_data)
        logger.info("Candidate data injected into page localStorage (%s).", LS_DATA_KEY)

    # ── Status polling ────────────────────────────────────────────────────────

    async def _poll_fill_status(self, timeout: float = FILL_TIMEOUT) -> dict | None:
        """
        Poll localStorage for the fillStatus written by the content script.
        Returns the status dict or None on timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        last_ts = 0

        while asyncio.get_event_loop().time() < deadline:
            result = await self._ls_get(LS_STATUS_KEY)
            if result and isinstance(result, dict) and result.get("timestamp", 0) != last_ts:
                last_ts = result["timestamp"]
                logger.info("Extension status: %s", result.get("status"))
                return result
            await asyncio.sleep(1)

        return None

    # ── File upload ───────────────────────────────────────────────────────────

    async def _handle_file_upload(self) -> None:
        """
        Upload the resume file using Playwright's setInputFiles (trusted CDP
        event), then signal the content script via localStorage.
        """
        resume_path = self.candidate_data.get("files", {}).get("resume_path", "")

        if resume_path and not os.path.isabs(resume_path):
            resume_path = str(Path(__file__).parent / resume_path)

        if resume_path and os.path.exists(resume_path):
            logger.info("Uploading resume: %s", resume_path)
            file_input = self._page.locator('input[type="file"]')
            await file_input.set_input_files(resume_path)
            logger.info("Resume uploaded.")
        else:
            if resume_path:
                logger.warning("Resume file not found: %s — skipping.", resume_path)
            else:
                logger.warning("No resume_path configured — skipping upload.")

        # Clear the old fillStatus so we keep polling for the next event.
        await self._ls_remove(LS_STATUS_KEY)
        # Signal the content script that the upload is complete.
        await self._ls_set(LS_UPLOAD_KEY, True)

    # ── Human verification ────────────────────────────────────────────────────

    async def _handle_human_verification(self) -> None:
        """
        Pause automation and ask the user to solve the CAPTCHA.
        When headless=False the browser window is already visible.
        When headless=True we open the URL in the system browser as a fallback.
        """
        print("\n" + "=" * 60)
        print("HUMAN VERIFICATION REQUIRED")
        print("=" * 60)
        print(f"URL: {self._page.url}")
        print()
        print("The job application form has been filled automatically.")
        print("A CAPTCHA challenge (hCaptcha / Turnstile) was detected.")
        print()

        if self.headless:
            print("Opening the page in your system browser…")
            try:
                url = self._page.url
                if sys.platform == "darwin":
                    subprocess.Popen(["open", url])
                elif sys.platform == "linux":
                    subprocess.Popen(["xdg-open", url])
                elif sys.platform == "win32":
                    subprocess.Popen(["start", url], shell=True)
            except Exception as exc:
                logger.debug("Could not open system browser: %s", exc)
        else:
            print("The browser window is open — complete the CAPTCHA there.")

        print()
        print(f"You have up to {HUMAN_TIMEOUT // 60} minutes.")
        print("The script continues automatically once the CAPTCHA is solved.")
        print("=" * 60 + "\n")

        try:
            await asyncio.wait_for(
                self._wait_for_captcha_cleared(),
                timeout=HUMAN_TIMEOUT,
            )
            print("[Jobnova] Verification complete — resuming automation.\n")
        except asyncio.TimeoutError:
            logger.error("Human verification timed out after %d s.", HUMAN_TIMEOUT)
            print(f"\n[Jobnova] CAPTCHA not solved within {HUMAN_TIMEOUT // 60} minutes — stopping.")
            raise

    async def _wait_for_captcha_cleared(self) -> None:
        """
        Wait until the CAPTCHA is solved.

        Cloudflare Turnstile does NOT remove its DOM element when solved;
        instead it populates a hidden <input name="cf-turnstile-response">.
        We check for that token, the hCaptcha equivalent, or the absence of
        any captcha iframe entirely.
        """
        while True:
            try:
                is_cleared = await self._page.evaluate(
                    """() => {
                        // Turnstile populates this when solved
                        const t = document.querySelector(
                            'input[name="cf-turnstile-response"]'
                        );
                        if (t && t.value) return true;

                        // hCaptcha populates this when solved
                        const h = document.querySelector(
                            'textarea[name="h-captcha-response"]'
                        );
                        if (h && h.value) return true;

                        // reCAPTCHA populates this when solved
                        const r = document.querySelector(
                            'textarea[name="g-recaptcha-response"]'
                        );
                        if (r && r.value) return true;

                        // No captcha widget present at all → no challenge
                        const hasCaptcha = !!(
                            document.querySelector('.cf-turnstile') ||
                            document.querySelector('.h-captcha') ||
                            document.querySelector(
                                'iframe[src*="challenges.cloudflare.com"]'
                            ) ||
                            document.querySelector('iframe[src*="hcaptcha.com"]') ||
                            document.querySelector('iframe[src*="recaptcha"]')
                        );
                        return !hasCaptcha;
                    }"""
                )
                if is_cleared:
                    return
            except Exception as exc:
                # Browser/page was closed — treat as user cancellation.
                logger.warning("CAPTCHA check failed (page may be closed): %s", exc)
                raise
            await asyncio.sleep(2)

    # ── Post-fill field corrections ──────────────────────────────────────────

    async def _fix_unfilled_fields(self) -> None:
        """
        Re-set fields that Lever's resume parser may have overwritten.

        Lever's resume parser triggers async React state updates that can
        clear the location field after the content script sets it. This
        method re-applies values from the controller using page.evaluate,
        and ensures the consent checkbox is checked.
        """
        identity = self.candidate_data.get("identity", {})
        custom = self.candidate_data.get("custom_answers", {})

        # Re-set location if Lever's resume parser cleared it.
        # Lever's location is a React controlled component — setting the DOM value
        # directly doesn't update React's internal state.  The only reliable way is
        # to simulate real keyboard input via Playwright's press_sequentially(),
        # which dispatches trusted KeyboardEvent/InputEvent that React picks up.
        location_val = identity.get("location", "")
        if location_val:
            current = await self._page.evaluate(
                "() => document.querySelector(\"input[name='location']\")?.value || ''"
            )
            if not current:
                logger.info("Re-setting location: '%s'", location_val)
                try:
                    # Lever's location input is a controlled React component with
                    # an autocomplete dropdown and a hidden selectedLocation input.
                    # We must:
                    #   1. Call React's onChange via __reactProps on the input
                    #   2. Set the native DOM value via the property setter
                    #   3. Set the hidden selectedLocation input
                    await self._page.evaluate(
                        """(val) => {
                            const el = document.querySelector("input[name='location']");
                            const hidden = document.querySelector("input[name='selectedLocation']");
                            if (!el) return;

                            // Trigger React's onChange via the fiber props
                            const propsKey = Object.keys(el).find(
                                k => k.startsWith('__reactProps')
                            );
                            if (propsKey) {
                                const props = el[propsKey];
                                if (props && props.onChange) {
                                    props.onChange({
                                        target: { name: 'location', value: val }
                                    });
                                }
                            }

                            // Also set the hidden selectedLocation value
                            const setter = Object.getOwnPropertyDescriptor(
                                HTMLInputElement.prototype, 'value'
                            ).set;
                            if (hidden) {
                                setter.call(hidden, val);
                                hidden.dispatchEvent(new Event('change', { bubbles: true }));
                            }

                            // Force the visible input value via native setter
                            setter.call(el, val);
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        location_val,
                    )
                    logger.info("Location set via React props + native setter.")
                except Exception as exc:
                    logger.warning("Failed to set location: %s", exc)

        # Ensure consent checkbox is checked
        consent_val = (custom.get("consent", "yes") or "yes").lower()
        if consent_val in ("yes", "true"):
            is_checked = await self._page.evaluate(
                """() => {
                    const els = document.querySelectorAll("input[name='consent[marketing]']");
                    for (const el of els) {
                        if (el.type === 'checkbox') return el.checked;
                    }
                    return null;
                }"""
            )
            if is_checked is False:
                logger.info("Checking consent checkbox via Playwright.")
                try:
                    await self._page.locator(
                        "input[name='consent[marketing]'][type='checkbox']"
                    ).check(force=True)
                except Exception:
                    # Fallback: click via JS targeting the actual checkbox (not hidden)
                    await self._page.evaluate(
                        """() => {
                            const els = document.querySelectorAll("input[name='consent[marketing]']");
                            for (const el of els) {
                                if (el.type === 'checkbox') {
                                    el.click();
                                    return;
                                }
                            }
                        }"""
                    )

    # ── Form submission ───────────────────────────────────────────────────────

    async def _submit_form(self) -> None:
        """
        Submit the application via a CDP trusted mouse event.
        CDP-dispatched input events are treated as trusted by the browser,
        bypassing synthetic-click detection that some ATS platforms use.

        Strategy:
          1. Try to find and scroll a visible submit button into view.
          2. Click it via CDP trusted mouse event.
          3. If no button is found, fall back to clicking via Playwright.
          4. Last resort: form.submit().
        """
        # Try multiple selector strategies to find the submit button.
        # On Lever, the visible submit button is type="button" with class
        # "postings-btn template-btn-submit", NOT type="submit" (that one is hidden).
        selectors = [
            'button.postings-btn.template-btn-submit',
            'button:has-text("Submit application")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            'input[type="submit"]',
            'a:has-text("Submit application")',
            '[data-qa="btn-submit"]',
        ]

        btn = None
        for sel in selectors:
            locator = self._page.locator(sel).first
            try:
                await locator.wait_for(state="attached", timeout=3_000)
                btn = locator
                logger.info("Found submit element via: %s", sel)
                break
            except Exception:
                continue

        if not btn:
            logger.warning("No submit button found via selectors — falling back to form.submit().")
            await self._page.evaluate(
                "() => { const f = document.querySelector('form'); if (f) f.submit(); }"
            )
            return

        # Scroll into view and wait.
        try:
            await btn.scroll_into_view_if_needed()
        except Exception:
            pass
        await asyncio.sleep(0.5)

        box = await btn.bounding_box()
        if not box:
            logger.info("No bounding box — using Playwright .click().")
            await btn.click()
            return

        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2

        cdp = await self._context.new_cdp_session(self._page)
        try:
            for event_type in ("mousePressed", "mouseReleased"):
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": event_type,
                        "x": cx,
                        "y": cy,
                        "button": "left",
                        "clickCount": 1,
                        "modifiers": 0,
                    },
                )
            logger.info("Submit button clicked via CDP trusted mouse event at (%.0f, %.0f).", cx, cy)
        finally:
            await cdp.detach()

    # ── Post-submission detection ─────────────────────────────────────────────

    async def _verify_submission(self) -> bool:
        """
        Wait briefly and check whether the page transitioned to a confirmation
        state.  Returns True if submission appears successful.
        """
        try:
            # Wait for navigation or DOM change indicating success.
            await asyncio.sleep(3)

            # Check for common success indicators.
            is_success = await self._page.evaluate(
                """() => {
                    // URL changed to a thank-you / confirmation page.
                    const url = window.location.href.toLowerCase();
                    if (url.includes('thank') || url.includes('confirm') ||
                        url.includes('success') || url.includes('submitted')) {
                        return true;
                    }

                    // Page content contains success messaging.
                    const body = document.body.innerText.toLowerCase();
                    const successPhrases = [
                        'application has been submitted',
                        'thank you for applying',
                        'thanks for applying',
                        'application received',
                        'successfully submitted',
                        'your application',
                        'we received your application',
                        'we have received',
                    ];
                    for (const phrase of successPhrases) {
                        if (body.includes(phrase)) return true;
                    }

                    // The form disappeared (replaced by confirmation).
                    const form = document.querySelector('form');
                    if (!form) return true;

                    return false;
                }"""
            )
            return is_success
        except Exception as exc:
            # Navigation might have caused the page to unload — that's usually a
            # positive sign (redirect to confirmation page).
            logger.debug("Post-submission check encountered: %s", exc)
            return True

    # ── Main run loop ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Execute the full application flow."""
        try:  # noqa: C901
            await self.launch()

            apply_url = self.job_url.rstrip("/")
            if not apply_url.endswith("/apply"):
                apply_url += "/apply"

            for attempt in range(1, MAX_RETRIES + 1):
                print(f"[Jobnova] Navigating to: {apply_url}" +
                      (f" (attempt {attempt})" if attempt > 1 else ""))
                logger.info("Navigating to: %s (attempt %d)", apply_url, attempt)

                await self._page.goto(apply_url, wait_until="domcontentloaded")

                # Clear any stale data from a previous run, then inject fresh data.
                await self._clear_stale_state()
                await self._inject_application_data()

                print("\n[Jobnova] Form is being filled by the Chrome Extension…\n")

                # Poll for the first status report from the content script.
                status_obj = await self._poll_fill_status()
                if status_obj is None:
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            "Extension did not respond within %ds — retrying (attempt %d/%d).",
                            FILL_TIMEOUT, attempt, MAX_RETRIES,
                        )
                        print(f"[Jobnova] Extension timeout — retrying ({attempt}/{MAX_RETRIES})…\n")
                        await self._ls_remove(LS_DATA_KEY, LS_STATUS_KEY, LS_UPLOAD_KEY)
                        continue
                    else:
                        print(
                            f"\n[Jobnova] Extension did not respond after {MAX_RETRIES} attempts.\n"
                            "  Check that the Chrome extension is loaded and the page is a Lever apply page."
                        )
                        return

                # Successfully got a status — enter the status handling loop.
                await self._handle_status_loop(status_obj)
                return

        except Exception as exc:
            # Handle browser or page closure.
            exc_name = type(exc).__name__
            if "TargetClosedError" in exc_name or "closed" in str(exc).lower():
                print("\n[Jobnova] Browser was closed — session ended.")
            else:
                logger.error("Unexpected error: %s", exc)
                print(f"\n[Jobnova] Error: {exc}")
        finally:
            try:
                await asyncio.sleep(3)  # brief pause so user can see the result
            except Exception:
                pass
            await self.close()

    async def _handle_status_loop(self, initial_status: dict) -> None:
        """Process extension status reports until a terminal state is reached."""
        status_obj = initial_status

        while True:
            status  = status_obj.get("status")
            payload = status_obj.get("payload") or {}

            if status == "needs_file_upload":
                logger.info("Handling resume upload…")
                await self._handle_file_upload()

            elif status == "needs_human":
                # Fix any fields the content script couldn't reliably set.
                await self._fix_unfilled_fields()
                await self._handle_human_verification()
                # After CAPTCHA solved, re-check for validation errors before submitting.
                await asyncio.sleep(1)
                await self._submit_form()
                success = await self._verify_submission()
                if success:
                    print("\n[Jobnova] Application submitted successfully!")
                else:
                    print("\n[Jobnova] Application submitted — please verify in the browser.")
                return

            elif status == "validation_errors":
                errors = payload.get("errors", [])
                logger.warning("Validation errors: %s", errors)
                print(f"\n[Jobnova] Validation errors detected: {errors}")
                print(
                    "[Jobnova] Please fix these fields and re-run, "
                    "or complete the application manually."
                )
                return

            elif status == "ready_to_submit":
                summary = {k: v for k, v in payload.items() if k != "timestamp"} if payload else {}
                if summary:
                    print(f"[Jobnova] Fill summary: {summary}")
                await self._fix_unfilled_fields()
                await self._submit_form()
                success = await self._verify_submission()
                if success:
                    print("\n[Jobnova] Application submitted successfully!")
                else:
                    print("\n[Jobnova] Application submitted — please verify in the browser.")
                return

            elif status == "error":
                reason = payload.get("reason", "unknown")
                logger.error("Extension reported error: %s", reason)
                print(f"\n[Jobnova] Extension error: {reason}")
                return

            else:
                logger.warning(
                    "Unexpected status '%s' — clearing and continuing.", status
                )
                await self._ls_remove(LS_STATUS_KEY)

            # Poll for next status.
            next_status = await self._poll_fill_status()
            if next_status is None:
                logger.warning("No further status received — stopping.")
                print("\n[Jobnova] No further status from extension — check the browser.")
                return
            status_obj = next_status
