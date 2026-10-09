/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Applies `patches/y-prosemirror@1.3.7.patch` to the installed package.
 *
 * Why not pnpm's `patchedDependencies`: the lockfile records a hash for every
 * patch, so declaring one there needs a `pnpm install` to regenerate
 * `pnpm-lock.yaml` — and the images build with `--frozen-lockfile`. This script
 * is run from the root `postinstall` instead, so a plain install (local or in
 * the Dockerfiles) keeps the patch applied.
 *
 * What it changes: `updateYFragment` (the ProseMirror -> Yjs reconcile, run on
 * every editor transaction) used to delete every Yjs child the ProseMirror
 * document no longer had. That range also covers blocks a remote writer added
 * after this client's last render, so a stale client copy silently deleted
 * freshly written content and the deletion was broadcast to everyone (PLANE-76,
 * reproduced against the incident's own documents). The patch only deletes
 * children the client actually rendered; the rest stay and become deletable
 * once the observer has rendered them.
 *
 * Only the `src` build is patched: every consumer here (web bundle, live server,
 * vitest) resolves the package's `import` condition.
 */

import { readFileSync, writeFileSync, readdirSync, existsSync } from "fs";
import { join } from "path";

const PATCH_MARKER = "PLANE PATCH (PLANE-76)";
const RELATIVE_PATH = "src/plugins/sync-plugin.js";

const ORIGINAL = `    } else if (yDelLen > 0) {
      yDomFragment.slice(left, left + yDelLen).forEach(type => meta.mapping.delete(type))
      yDomFragment.delete(left, yDelLen)
    }`;

const PATCHED = `    } else if (yDelLen > 0) {
      // PLANE PATCH (PLANE-76): the range this PM document no longer holds also
      // covers content we never rendered - blocks a remote writer added after our
      // last render. Deleting those destroys another writer's content silently
      // (reproduced: a stale client copy wiped a freshly imported page). Only
      // delete children present in the mapping; unmapped ones stay and become
      // deletable once the observer has rendered them.
      const deletableRuns = []
      let runStart = -1
      for (let i = left; i < left + yDelLen; i += 1) {
        const child = yDomFragment.get(i)
        const rendered = child !== null && meta.mapping.get(child) !== undefined
        if (rendered && runStart === -1) {
          runStart = i
        } else if (!rendered && runStart !== -1) {
          deletableRuns.push([runStart, i - runStart])
          runStart = -1
        }
      }
      if (runStart !== -1) {
        deletableRuns.push([runStart, left + yDelLen - runStart])
      }
      for (let r = deletableRuns.length - 1; r >= 0; r -= 1) {
        const [start, len] = deletableRuns[r]
        yDomFragment.slice(start, start + len).forEach(type => meta.mapping.delete(type))
        yDomFragment.delete(start, len)
      }
    }`;

const RENDER_ORIGINAL = `    // an error occured while creating the node. This is probably a result of a concurrent action.
    /** @type {Y.Doc} */ (el.doc).transact((transaction) => {
      /** @type {Y.Item} */ (el._item).delete(transaction)
    }, ySyncPluginKey)
    meta.mapping.delete(el)
    return null`;

const RENDER_PATCHED = `    // PLANE PATCH (PLANE-76): a node this client cannot render - an editor
    // version skew, a disabled extension, content written by another schema -
    // used to be deleted from the shared document, destroying another writer's
    // content. Keep it in Yjs and skip rendering it instead; leaving it out of
    // the mapping is what the reconcile patch above needs to protect it.
    meta.mapping.delete(el)
    return null`;

const TEXT_ORIGINAL = `    // an error occured while creating the node. This is probably a result of a concurrent action.
    /** @type {Y.Doc} */ (text.doc).transact((transaction) => {
      /** @type {Y.Item} */ (text._item).delete(transaction)
    }, ySyncPluginKey)
    return null`;

const TEXT_PATCHED = `    // PLANE PATCH (PLANE-76): keep unrenderable text in the shared document too.
    return null`;

const pnpmStore = join(process.cwd(), "node_modules", ".pnpm");
if (!existsSync(pnpmStore)) {
  // dependencies are not installed here (e.g. the Python-only api image)
  process.exit(0);
}

const targets = readdirSync(pnpmStore)
  .filter((entry) => entry.startsWith("y-prosemirror@"))
  .map((entry) => join(pnpmStore, entry, "node_modules", "y-prosemirror", RELATIVE_PATH))
  .filter((file) => existsSync(file));

if (targets.length === 0) {
  process.exit(0);
}

for (const file of targets) {
  const source = readFileSync(file, "utf8");
  const replacements = [
    [ORIGINAL, PATCHED],
    [RENDER_ORIGINAL, RENDER_PATCHED],
    [TEXT_ORIGINAL, TEXT_PATCHED],
  ];
  let patched = source;
  for (const [from, to] of replacements) {
    if (patched.includes(from)) {
      patched = patched.replace(from, to);
      continue;
    }
    // already-applied forms are fine; anything else means upstream changed
    if (!patched.includes(to.split("\n")[0])) {
      throw new Error(
        `[patches] cannot apply a y-prosemirror patch to ${file}.\n` +
          `The upstream code changed: review patches/y-prosemirror@1.3.7.patch (PLANE-76) and update this script.`
      );
    }
  }
  if (patched !== source) writeFileSync(file, patched);
  console.log(`[patches] applied the y-prosemirror reconcile patch to ${file}`);
}
