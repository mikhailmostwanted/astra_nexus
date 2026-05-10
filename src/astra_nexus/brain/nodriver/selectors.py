from __future__ import annotations

LOGIN_MARKERS = [
    'a[href*="/auth/login"]',
    'button[data-testid="login-button"]',
]

PROMPT_INPUT_SELECTORS = [
    "#prompt-textarea",
    "textarea#prompt-textarea",
    'textarea[data-testid="prompt-textarea"]',
    '[data-testid="composer-textarea"]',
    '[data-testid="composer-text-input"]',
    "div#prompt-textarea",
    'div[contenteditable="true"][data-lexical-editor="true"]',
    '[contenteditable="true"][role="textbox"]',
    '[role="textbox"]',
    'div[contenteditable="true"]',
    "textarea",
    "div.ProseMirror",
]

SEND_BUTTON_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[data-testid="composer-submit-button"]',
    'button[aria-label="Send prompt"]',
    'button[aria-label="Send message"]',
    'button[aria-label*="Send"]',
]

STOP_BUTTON_SELECTORS = [
    'button[data-testid="stop-button"]',
    'button[data-testid="composer-stop-button"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label*="Stop"]',
    'button[aria-label*="Cancel"]',
]

ASSISTANT_MESSAGE_QUERY = """
(() => {
  let nodes = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
  if (!nodes.length) {
    nodes = Array.from(document.querySelectorAll('article'));
  }
  return nodes
    .map((node) => (node.innerText || '').trim())
    .filter(Boolean);
})()
"""

LOGIN_REQUIRED_QUERY = """
(() => {
  const text = document.body ? document.body.innerText.toLowerCase() : '';
  const loginLink = document.querySelector('a[href*="/auth/login"]');
  const prompt = document.querySelector('#prompt-textarea,[contenteditable="true"]');
  return Boolean(loginLink || (!prompt && (text.includes('log in') || text.includes('sign up'))));
})()
"""
