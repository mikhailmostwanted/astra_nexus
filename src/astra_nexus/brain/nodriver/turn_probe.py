from __future__ import annotations

import json
from typing import Any


def build_turn_dump_probe_script(*, limit: int = 0, include_html: bool = False) -> str:
    """Build a ChatGPT DOM probe that extracts user/assistant turns and final candidates."""

    return (
        TURN_DUMP_PROBE_SCRIPT.replace("__LIMIT__", json.dumps(max(0, int(limit))))
        .replace("__INCLUDE_HTML__", json.dumps(bool(include_html)))
        .replace("__PREVIEW_LIMIT__", json.dumps(240))
    )


def normalize_turn_items(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"turns": [], "assistant_items": [], "user_items": []}
    turns = payload.get("turns")
    assistant_items = payload.get("assistantItems") or payload.get("assistant_items")
    user_items = payload.get("userItems") or payload.get("user_items")
    return {
        **payload,
        "turns": [item for item in turns if isinstance(item, dict)]
        if isinstance(turns, list)
        else [],
        "assistant_items": [item for item in assistant_items if isinstance(item, dict)]
        if isinstance(assistant_items, list)
        else [],
        "user_items": [item for item in user_items if isinstance(item, dict)]
        if isinstance(user_items, list)
        else [],
    }


TURN_DUMP_PROBE_SCRIPT = r"""
/* ASTRA_NEXUS_TURN_DUMP_PROBE */
(() => {
  const dumpLimit = __LIMIT__;
  const includeHtml = __INCLUDE_HTML__;
  const previewLimit = __PREVIEW_LIMIT__;
  const messageRootSelector = [
    '[data-turn="user"]',
    '[data-turn="assistant"]',
    '[data-message-author-role="user"]',
    '[data-message-author-role="assistant"]',
    'article',
  ].join(', ');
  const markdownSelector = [
    '.markdown',
    '.prose',
    '[class*="markdown"]',
    '[class*="prose"]',
  ].join(', ');
  const answerContentSelector = [
    '[data-message-author-role="assistant"]',
    '[data-testid*="message"]',
    '[data-testid*="answer"]',
    '[data-testid*="response"]',
    '[data-testid*="conversation-turn-content"]',
    '[class*="message-content"]',
    '[class*="result-content"]',
    '[class*="text-message"]',
    '[class*="assistant-message"]',
  ].join(', ');
  const visibleTextSelector = [
    'p',
    'li',
    'pre',
    'code',
    'blockquote',
    'h1',
    'h2',
    'h3',
    'h4',
    'table',
    '[data-start]',
    '[class*="whitespace-pre-wrap"]',
  ].join(', ');
  const thoughtSelector = [
    '[class*="result-thinking"]',
    '[class*="thinking"]',
    '[class*="reasoning"]',
    '[class*="thought"]',
    '[data-testid*="thinking"]',
    '[data-testid*="reasoning"]',
    '[data-testid*="thought"]',
    '[aria-label*="thinking" i]',
    '[aria-label*="reasoning" i]',
    '[aria-label*="thought" i]',
  ].join(', ');

  function normalizeText(value) {
    return String(value || '')
      .replace(/\r\n/g, '\n')
      .replace(/\u00a0/g, ' ')
      .replace(/\u200b/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n[ \t]+/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function compactPreview(value, limit = previewLimit) {
    const text = normalizeText(value).replace(/\s+/g, ' ');
    if (text.length <= limit) return text;
    return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}...`;
  }

  function attr(node, name) {
    return node && node.getAttribute ? node.getAttribute(name) || '' : '';
  }

  function classNameOf(node) {
    if (!node) return '';
    return typeof node.className === 'string' ? node.className : '';
  }

  function describeNode(node) {
    if (!node) return '';
    const bits = [
      (node.tagName || '').toLowerCase(),
      attr(node, 'data-turn') ? `[data-turn="${attr(node, 'data-turn')}"]` : '',
      attr(node, 'data-message-author-role')
        ? `[data-message-author-role="${attr(node, 'data-message-author-role')}"]`
        : '',
      attr(node, 'data-testid') ? `[data-testid="${attr(node, 'data-testid')}"]` : '',
      attr(node, 'aria-label') ? `[aria-label="${attr(node, 'aria-label')}"]` : '',
      classNameOf(node)
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 4)
        .map((name) => `.${name}`)
        .join(''),
    ];
    return bits.filter(Boolean).join('');
  }

  function elementVisible(node) {
    if (!node || !node.getBoundingClientRect) return false;
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      style.opacity !== '0'
    );
  }

  function hiddenByAttributeOrStyle(node) {
    if (!node || !node.getAttribute) return false;
    const style = String(attr(node, 'style')).toLowerCase();
    return (
      node.hidden ||
      attr(node, 'aria-hidden') === 'true' ||
      style.includes('display: none') ||
      style.includes('visibility: hidden') ||
      style.includes('opacity: 0')
    );
  }

  function hasHiddenAncestor(node, boundary) {
    let current = node && (node.nodeType === Node.TEXT_NODE ? node.parentElement : node);
    while (current && current !== boundary.parentElement) {
      if (current.nodeType === Node.ELEMENT_NODE) {
        if (hiddenByAttributeOrStyle(current)) return true;
        const style = window.getComputedStyle(current);
        if (
          style.display === 'none' ||
          style.visibility === 'hidden' ||
          style.opacity === '0'
        ) {
          return true;
        }
      }
      if (current === boundary) break;
      current = current.parentElement;
    }
    return false;
  }

  function isThinkingLabelOnly(text) {
    const normalized = normalizeText(text).replace(/\s+/g, ' ').toLowerCase();
    if (!normalized) return true;
    if (['thinking', 'думаю', 'думает'].includes(normalized)) return true;
    return normalized.startsWith('thought for ') && normalized.length <= 80;
  }

  function isControlText(text) {
    const normalized = normalizeText(text).replace(/\s+/g, ' ').toLowerCase();
    return [
      'copy',
      'копировать',
      'share',
      'поделиться',
      'read aloud',
      'regenerate',
      'try again',
      'chatgpt said:',
      'chatgpt сказал:',
      'you said:',
      'вы сказали:',
    ].includes(normalized);
  }

  function isControlNode(node) {
    if (!node || !node.getAttribute) return false;
    const tag = (node.tagName || '').toLowerCase();
    const role = attr(node, 'role').toLowerCase();
    if (['button', 'svg', 'canvas', 'nav', 'menu'].includes(tag)) return true;
    return role === 'button' || role === 'menu' || role === 'menuitem';
  }

  function isThoughtNode(node) {
    if (!node || !node.getAttribute) return false;
    const haystack = [
      classNameOf(node),
      attr(node, 'data-testid'),
      attr(node, 'aria-label'),
      attr(node, 'aria-describedby'),
      attr(node, 'role'),
    ].join(' ').toLowerCase();
    const thoughtPattern =
      /(^|\s|[-_])(result-thinking|thinking|reasoning|thought|chain-of-thought|cot)(\s|[-_]|$)/;
    if (thoughtPattern.test(haystack)) {
      return true;
    }
    return isThinkingLabelOnly(node.innerText || node.textContent || '');
  }

  function textAncestorRejected(textNode, boundary, role) {
    let current = textNode.parentElement;
    while (current && current !== boundary.parentElement) {
      const tag = (current.tagName || '').toLowerCase();
      if (['script', 'style', 'noscript', 'svg', 'canvas'].includes(tag)) {
        return 'non_text_node';
      }
      if (hasHiddenAncestor(current, boundary)) {
        return 'hidden';
      }
      if (role === 'assistant' && current !== boundary) {
        const explicitRole =
          attr(current, 'data-turn') || attr(current, 'data-message-author-role');
        if (explicitRole === 'user') {
          return 'user_descendant';
        }
      }
      if (isThoughtNode(current)) {
        return 'thought_or_reasoning';
      }
      if (isControlNode(current)) {
        return 'control_node';
      }
      if (current === boundary) break;
      current = current.parentElement;
    }
    return '';
  }

  function visibleTextFromNode(node, role) {
    if (!node) return '';
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const parts = [];
    while (walker.nextNode()) {
      const textNode = walker.currentNode;
      const raw = String(textNode.nodeValue || '');
      if (!raw.trim()) continue;
      if (textAncestorRejected(textNode, node, role)) continue;
      parts.push(raw);
    }
    return normalizeText(parts.join(' '));
  }

  function rawTextFromNode(node) {
    return normalizeText((node && (node.innerText || node.textContent)) || '');
  }

  function queryWithinIncludingSelf(root, selector) {
    const nodes = [];
    if (!root) return nodes;
    try {
      if (root.matches && root.matches(selector)) nodes.push(root);
      for (const node of root.querySelectorAll(selector)) nodes.push(node);
    } catch (_error) {}
    return Array.from(new Set(nodes));
  }

  function collectThoughtCandidates(root) {
    const nodes = queryWithinIncludingSelf(root, thoughtSelector).filter((node) =>
      isThoughtNode(node)
    );
    const seen = new Set();
    return nodes
      .map((node) => {
        const text = rawTextFromNode(node);
        const key = `${describeNode(node)}:${text}`;
        if (seen.has(key)) return null;
        seen.add(key);
        return {
          selector: describeNode(node),
          textLength: text.length,
          textPreview: compactPreview(text),
        };
      })
      .filter(Boolean);
  }

  function addFinalCandidate(root, node, source, accepted, rejected, seenText) {
    const selector = describeNode(node);
    if (!node) {
      rejected.push({ source, selector, reason: 'missing_node' });
      return;
    }
    if (!elementVisible(node) && node !== root) {
      rejected.push({ source, selector, reason: 'hidden_candidate' });
      return;
    }
    if (isThoughtNode(node)) {
      rejected.push({ source, selector, reason: 'thought_or_reasoning_candidate' });
      return;
    }
    const text = visibleTextFromNode(node, 'assistant');
    if (!text) {
      rejected.push({ source, selector, reason: 'empty_after_filtering' });
      return;
    }
    if (isThinkingLabelOnly(text)) {
      rejected.push({ source, selector, reason: 'thinking_label_only' });
      return;
    }
    if (isControlText(text)) {
      rejected.push({ source, selector, reason: 'control_text_only' });
      return;
    }
    const dedupeKey = text.replace(/\s+/g, ' ');
    if (seenText.has(dedupeKey)) {
      rejected.push({
        source,
        selector,
        reason: 'duplicate_text',
        textPreview: compactPreview(text),
      });
      return;
    }
    seenText.add(dedupeKey);
    accepted.push({
      source,
      selector,
      text,
      textLength: text.length,
      textPreview: compactPreview(text),
    });
  }

  function chooseFinalCandidate(candidates) {
    const sourcePriority = {
      answer_content: 4,
      markdown_prose: 3,
      assistant_root_text: 2,
      visible_text: 1,
    };
    return candidates
      .slice()
      .sort((left, right) => {
        const priorityDelta =
          (sourcePriority[right.source] || 0) - (sourcePriority[left.source] || 0);
        if (priorityDelta) return priorityDelta;
        const lengthDelta = (right.textLength || 0) - (left.textLength || 0);
        if (lengthDelta) return lengthDelta;
        return candidates.indexOf(right) - candidates.indexOf(left);
      })[0] || null;
  }

  function inferRole(node) {
    const explicit = attr(node, 'data-turn') || attr(node, 'data-message-author-role');
    if (explicit === 'user' || explicit === 'assistant') return explicit;
    const descendant = node.querySelector(
      '[data-turn="user"], [data-turn="assistant"], ' +
        '[data-message-author-role="user"], [data-message-author-role="assistant"]'
    );
    const descendantRole =
      descendant &&
      (attr(descendant, 'data-turn') || attr(descendant, 'data-message-author-role'));
    return descendantRole === 'user' || descendantRole === 'assistant' ? descendantRole : '';
  }

  function stableId(node, index) {
    return (
      attr(node, 'data-turn-id') ||
      attr(node, 'data-turn-id-container') ||
      attr(node, 'data-message-id') ||
      attr(node, 'data-testid') ||
      node.id ||
      `${attr(node, 'data-message-author-role') || attr(node, 'data-turn') || 'message'}:${index}`
    );
  }

  function summarizeClasses(node) {
    const rootClasses = classNameOf(node).split(/\s+/).filter(Boolean).slice(0, 8);
    const descendantClasses = Array.from(node.querySelectorAll('[class]'))
      .flatMap((item) => classNameOf(item).split(/\s+/).filter(Boolean))
      .filter((name) =>
        /markdown|prose|message|assistant|thinking|reason|result|turn|whitespace/.test(
          name.toLowerCase()
        )
      )
      .slice(0, 16);
    return Array.from(new Set(rootClasses.concat(descendantClasses))).join(' ');
  }

  function hasHiddenElements(node) {
    return Boolean(
      node.querySelector(
        '[aria-hidden="true"], [hidden], [style*="display: none"], ' +
          '[style*="visibility: hidden"], [style*="opacity: 0"]'
      )
    );
  }

  function extractTurn(node, index) {
    const role = inferRole(node);
    const rawText = rawTextFromNode(node);
    const html = node.outerHTML || '';
    const finalCandidates = [];
    const rejectedCandidates = [];
    const seenText = new Set();
    const thoughtCandidates = role === 'assistant' ? collectThoughtCandidates(node) : [];

    if (role === 'assistant') {
      for (const candidate of queryWithinIncludingSelf(node, answerContentSelector)) {
        addFinalCandidate(
          node,
          candidate,
          'answer_content',
          finalCandidates,
          rejectedCandidates,
          seenText
        );
      }
      for (const candidate of queryWithinIncludingSelf(node, markdownSelector)) {
        addFinalCandidate(
          node,
          candidate,
          'markdown_prose',
          finalCandidates,
          rejectedCandidates,
          seenText
        );
      }
      addFinalCandidate(
        node,
        node,
        'assistant_root_text',
        finalCandidates,
        rejectedCandidates,
        seenText
      );
      for (const candidate of queryWithinIncludingSelf(node, visibleTextSelector)) {
        addFinalCandidate(
          node,
          candidate,
          'visible_text',
          finalCandidates,
          rejectedCandidates,
          seenText
        );
      }
    }

    const chosen = role === 'assistant' ? chooseFinalCandidate(finalCandidates) : null;
    const text = role === 'assistant' ? chosen?.text || '' : visibleTextFromNode(node, role);
    const item = {
      index,
      role,
      id: stableId(node, index),
      dataTurn: attr(node, 'data-turn'),
      dataTestid: attr(node, 'data-testid'),
      ariaLabel: attr(node, 'aria-label'),
      dataMessageAuthorRole: attr(node, 'data-message-author-role'),
      text,
      finalText: role === 'assistant' ? text : '',
      textLength: text.length,
      textPreview: compactPreview(text),
      rawTextLength: rawText.length,
      rawTextPreview: compactPreview(rawText),
      htmlLength: html.length,
      classNames: summarizeClasses(node),
      selectorSummary: describeNode(node),
      hasMarkdownProseBlocks: queryWithinIncludingSelf(node, markdownSelector).length > 0,
      hasThinkingReasoningBlocks: thoughtCandidates.length > 0,
      hasHiddenAriaHiddenElements: hasHiddenElements(node),
      finalCandidates,
      finalCandidatePreviews: finalCandidates.map((candidate) => ({
        source: candidate.source,
        selector: candidate.selector,
        textLength: candidate.textLength,
        textPreview: candidate.textPreview,
      })),
      thoughtCandidates,
      thoughtCandidatePreviews: thoughtCandidates.map((candidate) => ({
        selector: candidate.selector,
        textLength: candidate.textLength,
        textPreview: candidate.textPreview,
      })),
      rejectedCandidateReasons: rejectedCandidates.map((candidate) => ({
        source: candidate.source,
        selector: candidate.selector,
        reason: candidate.reason,
        textPreview: candidate.textPreview || '',
      })),
      selectedFinalCandidate: chosen
        ? {
            source: chosen.source,
            selector: chosen.selector,
            textLength: chosen.textLength,
            textPreview: chosen.textPreview,
          }
        : null,
    };
    if (includeHtml) {
      item.outerHTML = html;
    }
    return item;
  }

  const roots = Array.from(document.querySelectorAll(messageRootSelector))
    .filter((node) => {
      const role = inferRole(node);
      if (role !== 'user' && role !== 'assistant') return false;
      const explicit = attr(node, 'data-turn') || attr(node, 'data-message-author-role');
      if (!explicit && node.querySelector('[data-turn], [data-message-author-role]')) {
        return false;
      }
      return true;
    })
    .filter((node, index, array) => array.indexOf(node) === index);

  const allTurns = roots
    .map((node, index) => extractTurn(node, index))
    .filter((item) => item.role === 'user' || item.role === 'assistant');
  const userItems = allTurns.filter((item) => item.role === 'user');
  const assistantItems = allTurns.filter((item) => item.role === 'assistant');
  const turns = dumpLimit > 0 ? allTurns.slice(-dumpLimit) : allTurns;

  const payload = {
    url: window.location.href,
    title: document.title,
    turnCount: allTurns.length,
    assistantCount: assistantItems.length,
    userCount: userItems.length,
    turns,
    assistantItems,
    userItems,
  };
  return JSON.parse(JSON.stringify(payload));
})()
"""
