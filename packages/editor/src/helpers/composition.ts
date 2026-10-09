/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Editor } from "@tiptap/core";

/**
 * Runs `callback` outside an IME composition, deferring it to the end of the
 * active one when there is one.
 *
 * While a composition is active the browser owns the editor's DOM and
 * ProseMirror deliberately ignores it. Applying an external document change
 * mid-composition (a value sync, a draft reset, a fallback seed) re-renders
 * that DOM out from under the IME, and the IME then commits both the raw
 * composition text (the pinyin) and the chosen characters into the document.
 * Every external write to the editor therefore goes through this helper and
 * waits for `compositionend` instead.
 *
 * Returns a cleanup that cancels the deferral (used when the caller unmounts
 * or the editor is destroyed before the composition ends).
 */
export const runOutsideComposition = (editor: Editor, callback: () => void): (() => void) => {
  if (!editor.view.composing) {
    callback();
    return () => {};
  }

  const { view } = editor;
  let frame = 0;
  let settled = false;
  const run = () => {
    // run on the next frame: ProseMirror processes the compositionend event
    // itself first, and only then is the post-composition state authoritative
    frame = requestAnimationFrame(() => {
      if (!editor.isDestroyed) callback();
    });
  };
  const handleCompositionEnd = () => {
    if (settled) return;
    settled = true;
    view.dom.removeEventListener("compositionend", handleCompositionEnd);
    run();
  };

  view.dom.addEventListener("compositionend", handleCompositionEnd);

  return () => {
    if (settled) return;
    settled = true;
    view.dom.removeEventListener("compositionend", handleCompositionEnd);
    if (frame) cancelAnimationFrame(frame);
  };
};

/**
 * Resolves once the editor is not composing (immediately when it already is
 * not), so callers that must not read or write the document mid-composition —
 * the save path — can await a stable state.
 */
export const waitUntilCompositionEnds = (editor: Editor): Promise<void> =>
  new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      editor.off("destroy", handleDestroy);
      resolve();
    };
    const handleDestroy = () => done();
    editor.once("destroy", handleDestroy);
    runOutsideComposition(editor, done);
  });
