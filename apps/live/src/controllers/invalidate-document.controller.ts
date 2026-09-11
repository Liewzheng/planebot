/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Hocuspocus } from "@hocuspocus/server";
import type { Request, Response } from "express";
import { z } from "zod";
// helpers
import { Controller, Post } from "@plane/decorators";
// logger
import { logger } from "@plane/logger";
// env
import { env } from "@/env";
// extensions
import { forceCloseDocumentAcrossServers } from "@/extensions/force-close-handler";
import { ForceCloseReason } from "@/types/admin-commands";

const invalidateDocumentSchema = z.object({
  docId: z.string().min(1, "docId is required"),
});

@Controller("/invalidate-document")
export class InvalidateDocumentController {
  [key: string]: unknown;
  private readonly hocuspocusServer: Hocuspocus;

  constructor(hocuspocusServer: Hocuspocus) {
    this.hocuspocusServer = hocuspocusServer;
  }

  /**
   * Invalidate an in-memory collaborative document after its content was
   * replaced directly in the database (e.g. by the public API / CLI re-upload).
   *
   * Force-closes every connected client with reason `content_replaced` (so
   * clients discard their stale local copy and IndexedDB cache, then reload
   * the fresh document from the database) and unloads the document from
   * memory so the next sync reads the new binary.
   */
  @Post("/")
  async invalidateDocument(req: Request, res: Response) {
    // verify the internal api key for server-to-server calls
    const internalApiKey = req.headers["x-internal-api-key"];
    if (!env.LIVE_INTERNAL_API_KEY || internalApiKey !== env.LIVE_INTERNAL_API_KEY) {
      return res.status(401).json({
        message: "Unauthorized",
      });
    }

    const parsedBody = invalidateDocumentSchema.safeParse(req.body);
    if (!parsedBody.success) {
      return res.status(400).json({
        message: "docId is required",
      });
    }

    const { docId } = parsedBody.data;
    logger.info(`[INVALIDATE_DOCUMENT] Invalidating document ${docId} after external content replacement`);

    try {
      await forceCloseDocumentAcrossServers(this.hocuspocusServer, docId, ForceCloseReason.CONTENT_REPLACED);
      return res.status(200).json({
        message: "Document invalidated",
      });
    } catch (error) {
      logger.error("[INVALIDATE_DOCUMENT] Failed to invalidate document:", error);
      return res.status(500).json({
        message: "Failed to invalidate document",
      });
    }
  }
}
