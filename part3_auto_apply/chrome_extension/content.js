/**
 * Jobnova Auto-Apply — Content Script (Lever)
 *
 * Injected into https://jobs.lever.co/* pages.
 *
 * Communication: window.localStorage (shared between page main world and
 * content-script isolated world on the same origin).
 *
 *   Playwright (page.evaluate) ──writes──► localStorage[jobnovaData]
 *   Content script              ──reads──► localStorage[jobnovaData]
 *   Content script              ──writes──► localStorage[jobnovaStatus]
 *   Playwright (page.evaluate)  ──polls──► localStorage[jobnovaStatus]
 *
 * Lever form structure (discovered from live page):
 *   - Standard fields: input[name="name"], email, phone, location, org
 *   - Custom question cards: .application-question.custom-question
 *     with inputs named cards[uuid][fieldN]
 *   - Each card can contain: <select>, <input type=radio>, <textarea>
 *   - Question text is in the card's title div, NOT in <label> elements
 *   - Diversity selects: Gender, Ethnicity, Age, Referral source
 *   - CAPTCHA: hCaptcha (.h-captcha[data-sitekey])
 *   - Submit: button.postings-btn.template-btn-submit (NOT type=submit)
 *   - Consent: checkbox input[name="consent[marketing]"]
 */

(function () {
  "use strict";

  // ── Storage key constants (must match controller.py) ──────────────────────
  const LS_DATA_KEY   = "jobnovaData";
  const LS_STATUS_KEY = "jobnovaStatus";
  const LS_UPLOAD_KEY = "jobnovaUploadDone";

  // ── localStorage helpers ──────────────────────────────────────────────────
  function lsGet(key) {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    try { return JSON.parse(raw); } catch { return raw; }
  }

  function lsSet(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function lsRemove(...keys) {
    keys.forEach((k) => localStorage.removeItem(k));
  }

  // ── Application data loading ──────────────────────────────────────────────
  async function getApplicationData(maxRetries = 20, retryDelayMs = 1000) {
    for (let i = 0; i < maxRetries; i++) {
      const data = lsGet(LS_DATA_KEY);
      if (data) {
        console.log("[Jobnova] Application data loaded.");
        return data;
      }
      if (i < maxRetries - 1) {
        console.log(`[Jobnova] Waiting for data… (${i + 1}/${maxRetries})`);
        await sleep(retryDelayMs);
      }
    }
    return null;
  }

  // ── Status reporting ──────────────────────────────────────────────────────
  function reportStatus(status, payload = null) {
    console.log("[Jobnova] Status:", status, payload);
    lsSet(LS_STATUS_KEY, { status, payload: payload || null, timestamp: Date.now() });
  }

  // ── Upload confirmation polling ───────────────────────────────────────────
  async function waitForUploadDone(timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (lsGet(LS_UPLOAD_KEY)) return true;
      await sleep(500);
    }
    return false;
  }

  // ── DOM utilities ─────────────────────────────────────────────────────────
  function setReactValue(element, value) {
    if (!element) return false;
    const proto = element.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : element.tagName === "SELECT"
        ? window.HTMLSelectElement.prototype
        : window.HTMLInputElement.prototype;

    // Delete React's _valueTracker so React sees our change as "new"
    // and updates its internal state accordingly.
    const tracker = element._valueTracker;
    if (tracker) {
      tracker.setValue("");
    }

    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(element, value);
    } else {
      element.value = value;
    }

    // Focus the element first so React's onFocus is triggered
    element.focus();
    element.dispatchEvent(new Event("input",  { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new Event("blur",   { bubbles: true }));
    return true;
  }

  function clickRadio(radio) {
    if (!radio) return false;
    radio.scrollIntoView({ block: "center", behavior: "instant" });
    radio.checked = true;
    radio.dispatchEvent(new Event("input",  { bubbles: true }));
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    radio.dispatchEvent(new Event("click",  { bubbles: true }));
    // Also click the parent label if present
    const label = radio.closest("label");
    if (label) label.click();
    return true;
  }



  function waitForAnyElement(selectors, timeoutMs = 25_000) {
    return new Promise((resolve, reject) => {
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) return resolve(el);
      }
      const observer = new MutationObserver(() => {
        for (const sel of selectors) {
          const found = document.querySelector(sel);
          if (found) { observer.disconnect(); resolve(found); return; }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => {
        observer.disconnect();
        reject(new Error(`waitForAnyElement timed out: ${selectors.join(", ")}`));
      }, timeoutMs);
    });
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function normalize(text) {
    return (text || "").toLowerCase().replace(/[^a-z0-9]/g, " ").replace(/\s+/g, " ").trim();
  }

  // ── Verification challenge detection ──────────────────────────────────────
  function detectVerificationChallenge() {
    const selectors = [
      ".cf-turnstile",
      ".h-captcha",
      "[data-sitekey]",
      'iframe[src*="challenges.cloudflare.com"]',
      'iframe[src*="hcaptcha.com"]',
      'iframe[src*="recaptcha"]',
      "[data-captcha]",
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 || rect.height > 0) {
          console.log("[Jobnova] Verification challenge detected:", sel);
          return true;
        }
      }
    }
    return false;
  }

  // ── Validation error detection ────────────────────────────────────────────
  function detectValidationErrors() {
    const errors = [];
    document.querySelectorAll(
      ".error-message, .field-error, [aria-invalid='true'], " +
      ".lever-error, .application-error, [data-error], " +
      ".application-form-error, .postings-form-error"
    ).forEach((el) => {
      const text = el.textContent.trim();
      if (text) errors.push(text);
    });
    return errors;
  }

  // ── Get the full question text from a card container ──────────────────────
  /**
   * Lever custom question cards have their question text in a title div.
   * We need to read the FULL text of the card (excluding option labels)
   * to understand what the question is asking.
   */
  function getCardQuestionText(card) {
    // Strategy 1: Look for a dedicated title element
    const titleEl = card.querySelector(
      ".custom-question-title, .application-question-title, " +
      ".card-title, h3, h4"
    );
    if (titleEl) return titleEl.textContent.trim();

    // Strategy 2: Get text from leaf divs (text-only divs with no children)
    // These often contain the question label (e.g. "Ethnicity", "Age Bracket")
    const leafTexts = [];
    card.querySelectorAll("div").forEach(d => {
      if (d.children.length === 0 && d.textContent.trim().length > 2) {
        leafTexts.push(d.textContent.trim());
      }
    });

    // Strategy 3: Clone and strip interactive elements to get question text
    const clone = card.cloneNode(true);
    clone.querySelectorAll("input, select, textarea, option, label").forEach(el => el.remove());
    const strippedText = clone.textContent.replace(/\s+/g, " ").trim();

    // Combine: use stripped text (which is the question sans options),
    // plus any leaf div texts for short-label cards (like "Ethnicity")
    const combined = [strippedText, ...leafTexts].join(" | ");
    return combined || card.textContent.trim().substring(0, 200);
  }

  // ── Find best matching answer for a question ─────────────────────────────
  function findAnswer(questionText, customAnswers) {
    const qNorm = normalize(questionText);
    if (!qNorm || !customAnswers) return null;

    // Direct key match — find the LONGEST matching key (prevents "visa" matching
    // before "visa details" when the question mentions both concepts)
    let bestMatch = null;
    let bestMatchLen = 0;
    for (const [key, val] of Object.entries(customAnswers)) {
      const keyNorm = normalize(key);
      if (!keyNorm) continue;
      if (qNorm.includes(keyNorm) || keyNorm.includes(qNorm)) {
        if (keyNorm.length > bestMatchLen) {
          bestMatchLen = keyNorm.length;
          bestMatch = String(val);
        }
      }
    }
    if (bestMatch) return bestMatch;

    // Word overlap scoring
    const qWords = new Set(qNorm.split(" ").filter(w => w.length > 2));
    let bestKey = null;
    let bestScore = 0;
    for (const key of Object.keys(customAnswers)) {
      const keyWords = normalize(key).split(" ").filter(w => w.length > 2);
      if (keyWords.length === 0) continue;
      let overlap = 0;
      for (const w of keyWords) {
        if (qWords.has(w)) overlap++;
      }
      const score = overlap / keyWords.length;
      if (score > bestScore && score >= 0.5) {
        bestScore = score;
        bestKey = key;
      }
    }
    return bestKey ? String(customAnswers[bestKey]) : null;
  }

  // ── Fill a custom question card ───────────────────────────────────────────
  function fillCard(card, answer) {
    if (!answer) return false;
    const answerNorm = normalize(answer);

    // a) Radio inputs — match by value or visible label text
    const radios = card.querySelectorAll("input[type='radio']");
    if (radios.length > 0) {
      // Helper: get the visible text for a radio (label text or value)
      function getRadioText(radio) {
        const label = radio.closest("label");
        if (label) return label.textContent.trim();
        const id = radio.id;
        if (id) {
          const linkedLabel = document.querySelector(`label[for="${id}"]`);
          if (linkedLabel) return linkedLabel.textContent.trim();
        }
        // Next sibling text node
        const next = radio.nextSibling;
        if (next && next.nodeType === Node.TEXT_NODE) return next.textContent.trim();
        return radio.value;
      }

      // Exact match on value or label text
      for (const radio of radios) {
        const valNorm = normalize(radio.value);
        const textNorm = normalize(getRadioText(radio));
        if (valNorm === answerNorm || textNorm === answerNorm) {
          return clickRadio(radio);
        }
      }
      // Partial match — find closest matching radio by substring
      for (const radio of radios) {
        const valNorm = normalize(radio.value);
        const textNorm = normalize(getRadioText(radio));
        if (valNorm.includes(answerNorm) || answerNorm.includes(valNorm) ||
            textNorm.includes(answerNorm) || answerNorm.includes(textNorm)) {
          return clickRadio(radio);
        }
      }
      // Word overlap match for longer radio values
      let bestRadio = null;
      let bestOverlap = 0;
      const answerWords = new Set(answerNorm.split(" ").filter(w => w.length > 2));
      for (const radio of radios) {
        const combined = normalize(radio.value) + " " + normalize(getRadioText(radio));
        const valWords = combined.split(" ").filter(w => w.length > 2);
        let overlap = 0;
        for (const w of valWords) {
          if (answerWords.has(w)) overlap++;
        }
        if (overlap > bestOverlap) {
          bestOverlap = overlap;
          bestRadio = radio;
        }
      }
      if (bestRadio && bestOverlap >= 1) {
        return clickRadio(bestRadio);
      }
      console.warn(`[Jobnova] No matching radio for answer "${answer}". Options: ${Array.from(radios).map(r => getRadioText(r)).join(", ")}`);
      return false;
    }

    // b) Select dropdown — match option text or value
    const select = card.querySelector("select");
    if (select) {
      for (const opt of select.options) {
        const optNorm = normalize(opt.text);
        const optValNorm = normalize(opt.value);
        if (optNorm === answerNorm || optValNorm === answerNorm) {
          setReactValue(select, opt.value);
          return true;
        }
      }
      // Partial match
      for (const opt of select.options) {
        const optNorm = normalize(opt.text);
        if (optNorm.includes(answerNorm) || answerNorm.includes(optNorm)) {
          if (opt.value) { // Skip placeholder "Select..."
            setReactValue(select, opt.value);
            return true;
          }
        }
      }
      console.warn(`[Jobnova] No matching option for answer "${answer}". Options: ${Array.from(select.options).map(o => o.text).join(", ")}`);
      return false;
    }

    // c) Textarea — fill with answer text
    const textarea = card.querySelector("textarea");
    if (textarea) {
      setReactValue(textarea, answer);
      return true;
    }

    // d) Text input
    const textInput = card.querySelector(
      "input:not([type='radio']):not([type='checkbox']):not([type='file']):not([type='hidden'])"
    );
    if (textInput) {
      setReactValue(textInput, answer);
      return true;
    }

    return false;
  }

  // ── Main form filler ──────────────────────────────────────────────────────
  class LeverFormFiller {
    constructor(data) {
      this.data = data;
    }

    async fill() {
      console.log("[Jobnova] Starting form fill…");

      // Wait for form to render
      try {
        await waitForAnyElement([
          'input[name="name"]',
          'form.application-form',
          '.postings-form',
          '.application-question',
        ], 25_000);
      } catch {
        reportStatus("error", { reason: "form_not_found" });
        return;
      }

      await sleep(2000); // React hydration settle

      const identity = this.data.identity || {};
      const customAnswers = this.data.custom_answers || {};
      let filledCount = 0;

      // ── Phase 1: Standard identity fields ──
      console.log("[Jobnova] Phase 1: Standard fields…");
      const standardFields = [
        { sel: 'input[name="name"]',     val: identity.name },
        { sel: 'input[name="email"]',    val: identity.email },
        { sel: 'input[name="phone"]',    val: identity.phone },
        { sel: 'input[name="location"]', val: identity.location },
        { sel: 'input[name="org"]',      val: identity.company },
      ];
      for (const { sel, val } of standardFields) {
        if (!val) continue;
        const el = document.querySelector(sel);
        if (el) {
          setReactValue(el, val);
          console.log(`[Jobnova] Filled: ${sel} → "${val}"`);
          filledCount++;
        } else {
          console.warn(`[Jobnova] Not found: ${sel}`);
        }
      }
      await sleep(500);

      // ── Phase 2: Cover letter (comments textarea) ──
      console.log("[Jobnova] Phase 2: Cover letter…");
      const coverLetter = customAnswers.cover_letter || "";
      if (coverLetter) {
        const commentsEl = document.querySelector(
          'textarea[name="comments"], textarea#additional-information'
        );
        if (commentsEl) {
          // Scroll to it first so it's in view
          commentsEl.scrollIntoView({ block: "center", behavior: "instant" });
          await sleep(200);
          setReactValue(commentsEl, coverLetter);
          console.log("[Jobnova] Filled: cover letter");
          filledCount++;
        }
      }
      await sleep(300);

      // ── Phase 3: Resume upload ──
      console.log("[Jobnova] Phase 3: Resume upload…");
      const resumeInput = document.querySelector('input[type="file"][name="resume"]');
      const resumePath = (this.data.files || {}).resume_path;
      if (resumeInput && resumePath) {
        reportStatus("needs_file_upload", { selector: 'input[type="file"]' });
        console.log("[Jobnova] Waiting for controller to upload resume…");
        const uploaded = await waitForUploadDone(30_000);
        lsRemove(LS_UPLOAD_KEY);
        console.log(uploaded ? "[Jobnova] Resume uploaded." : "[Jobnova] Resume upload timed out.");
        await sleep(3000); // Wait for Lever to parse the resume (needs time to auto-fill)
      }

      // ── Phase 3b: Re-fill standard fields after resume upload ──
      // Lever's resume parser triggers async React state updates that clear
      // fields (especially location). We retry multiple times over several
      // seconds to outlast the parser's re-renders.
      console.log("[Jobnova] Phase 3b: Re-filling standard fields after resume parse…");
      for (let attempt = 0; attempt < 5; attempt++) {
        await sleep(1000);
        let anyChanged = false;
        for (const { sel, val } of standardFields) {
          if (!val) continue;
          const el = document.querySelector(sel);
          if (el && el.value !== val) {
            console.log(`[Jobnova] Re-filling ${sel} (attempt ${attempt + 1}): "${el.value}" → "${val}"`);
            setReactValue(el, val);
            anyChanged = true;
          }
        }
        // Also re-fill cover letter if it was cleared
        if (coverLetter) {
          const commentsEl = document.querySelector(
            'textarea[name="comments"], textarea#additional-information'
          );
          if (commentsEl && !commentsEl.value) {
            setReactValue(commentsEl, coverLetter);
            console.log("[Jobnova] Re-filled: cover letter");
            anyChanged = true;
          }
        }
        if (!anyChanged && attempt >= 1) {
          console.log("[Jobnova] All standard fields stable.");
          break;
        }
      }
      await sleep(500);

      // ── Phase 4: Custom question cards ──
      console.log("[Jobnova] Phase 4: Custom question cards…");
      const customCards = document.querySelectorAll(".application-question.custom-question");
      let customFilled = 0;

      for (const card of customCards) {
        // Scroll the card into view before filling
        card.scrollIntoView({ block: "center", behavior: "instant" });
        await sleep(200);

        const questionText = getCardQuestionText(card);
        console.log(`[Jobnova] Question: "${questionText.substring(0, 100)}"`);

        const answer = findAnswer(questionText, customAnswers);
        if (answer) {
          const filled = fillCard(card, answer);
          if (filled) {
            console.log(`[Jobnova] ✓ Answered: "${answer}"`);
            customFilled++;
            continue;
          } else {
            console.warn(`[Jobnova] ✗ Could not fill answer "${answer}" for question.`);
          }
        }

        // Fallback 1: try matching against the select's first option text
        // (e.g. Gender dropdown has "Gender" as the first/placeholder option)
        const sel = card.querySelector("select");
        if (sel && sel.options.length > 0) {
          const firstOpt = sel.options[0].text;
          const answer2 = findAnswer(firstOpt, customAnswers);
          if (answer2) {
            const filled = fillCard(card, answer2);
            if (filled) {
              console.log(`[Jobnova] ✓ Answered (by placeholder "${firstOpt}"): "${answer2}"`);
              customFilled++;
              continue;
            }
          }
        }

        // Fallback 2: try matching against each leaf div text individually
        // (e.g. "Ethnicity", "Age Bracket" are short labels in leaf divs)
        let leafMatched = false;
        card.querySelectorAll("div").forEach(d => {
          if (leafMatched) return;
          if (d.children.length === 0 && d.textContent.trim().length > 2) {
            const leafText = d.textContent.trim();
            const leafAnswer = findAnswer(leafText, customAnswers);
            if (leafAnswer) {
              const filled = fillCard(card, leafAnswer);
              if (filled) {
                console.log(`[Jobnova] ✓ Answered (by leaf "${leafText}"): "${leafAnswer}"`);
                customFilled++;
                leafMatched = true;
              }
            }
          }
        });
        if (leafMatched) continue;

        if (!answer) {
          console.log(`[Jobnova] — No answer for: "${questionText.substring(0, 80)}"`);
        }
      }
      console.log(`[Jobnova] Custom questions filled: ${customFilled}/${customCards.length}`);
      await sleep(300);

      // ── Phase 5: Consent checkbox ──
      console.log("[Jobnova] Phase 5: Consent checkbox…");
      const consentAnswer = normalize(customAnswers.consent || "yes");
      if (consentAnswer === "yes" || consentAnswer === "true") {
        // Lever has TWO elements named consent[marketing]: a hidden input (value=0)
        // and the actual checkbox (value=1). We must find the CHECKBOX specifically.
        const allConsent = document.querySelectorAll('input[name="consent[marketing]"]');
        let consentCheckbox = null;
        for (const el of allConsent) {
          if (el.type === "checkbox") {
            consentCheckbox = el;
            break;
          }
        }
        if (consentCheckbox) {
          consentCheckbox.scrollIntoView({ block: "center", behavior: "instant" });
          await sleep(200);
          if (!consentCheckbox.checked) {
            // Use direct click on the checkbox element
            consentCheckbox.click();
            await sleep(100);
            // Verify and retry with label if needed
            if (!consentCheckbox.checked) {
              const label = consentCheckbox.closest("label") ||
                            consentCheckbox.parentElement?.querySelector("label") ||
                            consentCheckbox.parentElement?.closest("label");
              if (label) label.click();
            }
            // Last resort: set checked state directly via property
            if (!consentCheckbox.checked) {
              consentCheckbox.checked = true;
              consentCheckbox.dispatchEvent(new Event("change", { bubbles: true }));
            }
          }
          console.log("[Jobnova] Consent checkbox checked:", consentCheckbox.checked);
          filledCount++;
        } else {
          console.warn("[Jobnova] Consent checkbox not found among", allConsent.length, "elements");
        }
      }
      await sleep(300);

      // ── Phase 6: Scroll to bottom and check for verification ──
      console.log("[Jobnova] Phase 6: Verification check…");
      // Scroll down to make sure CAPTCHA is visible
      window.scrollTo(0, document.body.scrollHeight);
      await sleep(1000);

      if (detectVerificationChallenge()) {
        reportStatus("needs_human", { reason: "verification_challenge" });
        return;
      }

      // ── Phase 7: Validation errors ──
      const errors = detectValidationErrors();
      if (errors.length > 0) {
        reportStatus("validation_errors", { errors });
        return;
      }

      // ── Phase 8: Report ready ──
      const summary = {
        standard_fields: filledCount,
        custom_questions: customFilled,
        total_cards: customCards.length,
      };
      console.log("[Jobnova] Form filled — ready to submit.", summary);
      reportStatus("ready_to_submit", summary);
    }
  }

  // ── Entry point ───────────────────────────────────────────────────────────
  const isApplyPage =
    window.location.pathname.includes("/apply") ||
    !!document.querySelector('form.application-form, .postings-form');

  if (!isApplyPage) {
    console.log("[Jobnova] Not an apply page — idle.");
    return;
  }

  console.log("[Jobnova] Apply page detected. Waiting for application data…");

  (async () => {
    const data = await getApplicationData(20, 1000);
    if (!data) {
      console.error("[Jobnova] No application data after retries — aborting.");
      reportStatus("error", { reason: "no_application_data" });
      return;
    }
    await new LeverFormFiller(data).fill();
  })();
})();
