/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// @vitest-environment jsdom

// React 19's `act()` needs to know it is running under one; without the
// flag it warns and silently skips some flushes.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Regression coverage for the PLANE-76 sync-stamp fix.
 *
 * The hook (`packages/editor/src/hooks/use-yjs-setup.ts`) must stamp the
 * cache when the session reaches the server-synced state, so a read-only
 * visit leaves `stored === contentStamp` and the next open does not wipe
 * the cache and rebuild from scratch (the multi-second "正在同步..." the
 * human reported on long pages). The fix must also:
 *
 *   - not wipe the cache when `contentStamp` is undefined at the reset
 *     decision (the pages-list mount path) and decide again when the
 *     served value arrives;
 *   - not overwrite a stored mismatching stamp on the first sync of a
 *     session (the deferred PLANE-76 reset must still fire on the next
 *     open).
 *
 * The helper-only suite (`document-stamp.test.ts`) cannot pin this: it
 * exercises primitives that are also exposed unchanged. This file mocks
 * `@hocuspocus/provider` and `y-indexeddb`, renders `useYjsSetup`, and
 * fires the captured `onSynced` callback so the test fails when the hook
 * does not write (or wrongly clears) the cache and passes once it does.
 */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import * as Y from "yjs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MockProviderInstance = {
  fireSynced: () => void;
};

const mockProviderInstances: MockProviderInstance[] = [];

/** Each entry is "clearData" (the cleaner that the persistence effect
 *  spawns when the PLANE-76 reset flag is set). Empty = no reset. */
const mockClearDataCalls: string[] = [];

vi.mock("@hocuspocus/provider", () => ({
  HocuspocusProvider: class {
    configuration: {
      websocketProvider?: {
        shouldConnect: boolean;
        disconnect: () => void;
        destroy: () => void;
      };
    };
    document: Y.Doc;

    constructor(config: { onSynced?: () => void }) {
      this.configuration = {
        websocketProvider: {
          shouldConnect: true,
          disconnect: () => {},
          destroy: () => {},
        },
      };
      this.document = new Y.Doc();

      const onSynced = config?.onSynced;
      mockProviderInstances.push({
        fireSynced: () => onSynced?.(),
      });
    }

    on() {}
    off() {}
    disconnect() {}
    destroy() {}
  },
}));

vi.mock("y-indexeddb", () => ({
  IndexeddbPersistence: class {
    doc: Y.Doc;
    constructor(_docId: string, doc: Y.Doc) {
      this.doc = doc;
      const original = doc.getXmlFragment.bind(doc);
      doc.getXmlFragment = ((name: string) => original(name) ?? { length: 0 }) as typeof doc.getXmlFragment;
    }
    on() {}
    destroy() {}
    async clearData() {
      mockClearDataCalls.push("clearData");
    }
  },
}));

const STAMP_KEY_PREFIX = "plane:doc-stamp:";
const stampKey = (docId: string) => `${STAMP_KEY_PREFIX}${docId}`;

// Mutable props the test re-renders with; the component closure reads from
// this on every render so re-rendering with `createElement(Probe)` against
// the same root updates the props without unmounting the component (and
// without re-running the provider effect).
type ProbeProps = { docId: string; contentStamp: string | undefined };
let currentProps: ProbeProps = { docId: "", contentStamp: undefined };

function Probe() {
  useYjsSetup({
    docId: currentProps.docId,
    serverUrl: "ws://test",
    authToken: "token",
    contentStamp: currentProps.contentStamp,
  });
  return null;
}

// Imported after `vi.mock` so the hook picks up the mocked modules.
import { useYjsSetup } from "@/hooks/use-yjs-setup";

describe("useYjsSetup stamps the cache on sync (PLANE-76 regression)", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  beforeEach(() => {
    window.localStorage.clear();
    mockProviderInstances.length = 0;
    mockClearDataCalls.length = 0;
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root!.unmount();
      });
      root = null;
    }
    if (container?.parentNode) {
      container.parentNode.removeChild(container);
    }
    container = null;
    mockProviderInstances.length = 0;
    mockClearDataCalls.length = 0;
  });

  async function render(props: ProbeProps) {
    currentProps = props;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root!.render(createElement(Probe));
    });
  }

  async function rerender(props: ProbeProps) {
    currentProps = props;
    await act(async () => {
      root!.render(createElement(Probe));
    });
  }

  it("writes the served contentStamp when the session first syncs", async () => {
    await render({ docId: "page-1", contentStamp: "stamp-abc" });
    expect(mockProviderInstances.length).toBe(1);

    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });

    expect(window.localStorage.getItem(stampKey("page-1"))).toBe("stamp-abc");
  });

  it("a read-only open leaves stored === contentStamp so shouldResetLocalDocument is false", async () => {
    await render({ docId: "page-2", contentStamp: "stamp-abc" });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });

    // The first open stamped the cache; the helper must accept it.
    const stored = window.localStorage.getItem(stampKey("page-2"));
    expect(stored).toBe("stamp-abc");

    const { shouldResetLocalDocument, readDocumentStamp } = await import("@/helpers/document-stamp");
    expect(shouldResetLocalDocument(readDocumentStamp("page-2"), "stamp-abc")).toBe(false);
  });

  it("does not re-stamp on a mid-session contentStamp change (PLANE-76 defense)", async () => {
    // First open: stamp the cache with the served revision.
    await render({ docId: "page-3", contentStamp: "stamp-abc" });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });
    expect(window.localStorage.getItem(stampKey("page-3"))).toBe("stamp-abc");

    // Mid-session: another writer saves; the parent re-renders with the new
    // served contentStamp. The stamp effect sets the PLANE-76 reset flag, but
    // the cache is only cleared on the next persistence bind. If a re-sync
    // here overwrote the stamp, the next open would skip the reset.
    await rerender({ docId: "page-3", contentStamp: "stamp-def" });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });

    expect(window.localStorage.getItem(stampKey("page-3"))).toBe("stamp-abc");
  });

  it("stamps when the served contentStamp arrives after sync and does not wipe the cache (PLANE-76 pages-list path)", async () => {
    // Pages-list navigation mounts the editor before `description_html` arrives
    // (the list serializer omits it; only the detail serializer adds it), so
    // `contentStamp` starts undefined. The websocket can sync before the
    // detail request resolves; the first `onSynced` finds nothing to write.
    await render({ docId: "page-4", contentStamp: undefined });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });
    expect(window.localStorage.getItem(stampKey("page-4"))).toBeNull();

    // The detail request resolves; the prop arrives via re-render. The hook
    // must catch up and stamp so the read-only open still leaves a usable
    // stamp behind.
    await rerender({ docId: "page-4", contentStamp: "stamp-abc" });

    expect(window.localStorage.getItem(stampKey("page-4"))).toBe("stamp-abc");
    // The reset flag must NOT have fired while the served value was unknown;
    // otherwise the cache would be wiped before the stamp arrives and the
    // multi-second "正在同步..." returns on the list flow.
    expect(mockClearDataCalls).toEqual([]);
  });

  it("does not clear the cache when contentStamp is undefined at the reset decision (PLANE-76 pages-list mount)", async () => {
    // The pages-list layout renders the editor while SWR fetches the list,
    // and the list serializer omits description_html, so `contentStamp`
    // is undefined at the first persistence bind. Pre-fix the stamp
    // effect set the reset flag on the unknown served stamp. The flag
    // survived the initial bind (which ran before the stamp effect in
    // the old declaration order) and any subsequent provider rebuild
    // — e.g. the docId bump below — would consume it and call clearData.
    // Post-fix the helper returns false for an unknown served stamp so
    // the flag is never set; the cache survives the mount and the
    // rebuild.
    await render({ docId: "page-5", contentStamp: undefined });
    expect(mockClearDataCalls).toEqual([]);

    // Trigger a provider rebuild by switching docIds (both the stamp
    // effect and the persistence effect have docId in their deps, so
    // both re-run on the new docId). Pre-fix the stamp effect set the
    // flag during the first mount and the rebuild consumes it; post-fix
    // the helper never sets the flag.
    await rerender({ docId: "page-5-other", contentStamp: undefined });
    expect(mockClearDataCalls).toEqual([]);
  });

  it("does not overwrite a stored mismatching stamp on first sync (PLANE-76 reset still fires on next open)", async () => {
    // Pre-populate localStorage with a stale stamp (the page was updated
    // elsewhere between sessions).
    window.localStorage.setItem(stampKey("page-6"), "stamp-old");

    // First open with the new served revision. The PLANE-76 reset flag is
    // set by the stamp effect, but the cache is only cleared on the next
    // persistence bind. If the first sync overwrote the stored stamp, the
    // next open would see stored === served and skip the reset.
    await render({ docId: "page-6", contentStamp: "stamp-new" });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });
    expect(window.localStorage.getItem(stampKey("page-6"))).toBe("stamp-old");

    // Simulate the next open: unmount and remount with the same contentStamp.
    await act(async () => {
      root!.unmount();
    });
    root = null;
    mockProviderInstances.length = 0;
    mockClearDataCalls.length = 0;

    await render({ docId: "page-6", contentStamp: "stamp-new" });

    // The deferred reset must still fire: stored ("stamp-old") !== served
    // ("stamp-new"), so the persistence bind spawns the cleaner.
    expect(mockClearDataCalls.length).toBeGreaterThan(0);
  });

  it("does not overwrite a stored mismatching stamp when the served value arrives after sync", async () => {
    // Pre-populate localStorage with a stale stamp (the page was updated
    // elsewhere before this session even opened).
    window.localStorage.setItem(stampKey("page-8"), "stamp-old");

    // Mount with contentStamp undefined (pages-list path). Sync fires
    // with no stamp to write.
    await render({ docId: "page-8", contentStamp: undefined });
    await act(async () => {
      mockProviderInstances[0].fireSynced();
    });
    expect(window.localStorage.getItem(stampKey("page-8"))).toBe("stamp-old");

    // The served value arrives post-sync, but it differs from the stored
    // stamp. The catch-up stamp write must not overwrite the stored
    // evidence — the PLANE-76 reset still has to fire on the next open.
    await rerender({ docId: "page-8", contentStamp: "stamp-new" });
    expect(window.localStorage.getItem(stampKey("page-8"))).toBe("stamp-old");

    // A provider rebuild (sessionEpoch bump) must now consume the reset
    // flag that the stamp effect set when the served value became known.
    // Pre-fix the P2a effect doesn't exist, so the flag is still set
    // but no write happened either — same outcome here. To pin the P2a
    // gate specifically we rebuild with a fresh flag and assert the
    // reset fires (stored "stamp-old" !== served "stamp-new"). On a
    // post-fix mount the stamp effect runs first and the persistence
    // bind (after my reorder) consumes the flag.
    await act(async () => {
      root!.unmount();
    });
    root = null;
    mockProviderInstances.length = 0;
    mockClearDataCalls.length = 0;

    await render({ docId: "page-8", contentStamp: "stamp-new" });
    expect(mockClearDataCalls.length).toBeGreaterThan(0);
  });
});
