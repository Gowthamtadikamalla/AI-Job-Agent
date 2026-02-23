/**
 * Jobnova Auto-Apply — Background Service Worker (MV3)
 *
 * Required by Manifest V3 for the extension to be valid.
 *
 * The primary data flow uses localStorage (shared between the page's main
 * world and the content script on the same origin):
 *
 *   Playwright (page.evaluate) ──writes──► localStorage[jobnovaData]
 *   content.js                 ──reads──► localStorage[jobnovaData]
 *   content.js                 ──writes──► localStorage[jobnovaStatus]
 *   Playwright (page.evaluate) ──polls──► localStorage[jobnovaStatus]
 *
 * This service worker handles no critical data relay but keeps the extension
 * alive and logs activity for debugging.
 */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("[Jobnova BG] Message from content script:", message);
  sendResponse({ ok: true });
  return false;
});

console.log("[Jobnova BG] Service worker started.");
