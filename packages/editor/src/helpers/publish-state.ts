/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Which pages have a publish on the wire right now.
 *
 * A publish is written by the API, which then replaces the collaborative
 * document — the client that published included. That client is force-closed
 * with `content_replaced` while its own request is still in flight, and the
 * reload that follows tears the page down before the response is handled: the
 * success path never runs, so the local draft the author was editing is never
 * discarded and the page keeps offering work the server already holds.
 *
 * The flag lets the force-close handler tell "the server is dropping this
 * document because of my own save" from "someone else replaced it" (where the
 * reload is the correct answer). It is per browser tab: another tab is a
 * different writer and keeps the normal behaviour.
 */

const publishingDocuments = new Set<string>();

/** Mark the page as having a publish request in flight. */
export const markPublishInFlight = (documentId: string): void => {
  if (documentId) publishingDocuments.add(documentId);
};

/** The publish request settled (either way); the page is no longer publishing. */
export const clearPublishInFlight = (documentId: string): void => {
  if (documentId) publishingDocuments.delete(documentId);
};

/** True while this tab's publish request for the page has not settled yet. */
export const isPublishInFlight = (documentId: string): boolean => publishingDocuments.has(documentId);
