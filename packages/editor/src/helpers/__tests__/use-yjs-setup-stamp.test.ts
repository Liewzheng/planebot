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
 * human reported on long pages).
 *
 * The helper-only suite (`document-stamp.test.ts`) cannot pin this: it
 * exercises unchanged primitives and passes 13/13 on both the pre-fix and
 * post-fix revisions. This file mocks `@hocuspocus/provider` and
 * `y-indexeddb`, renders `useYjsSetup`, and fires the captured `onSynced`
 * callback so the test fails when the hook does not write the stamp and
 * passes once it does.
 */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import * as Y from "yjs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MockProviderInstance = {
  fireSynced: () => void;
};

const mockProviderInstances: MockProviderInstance[] = [];

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
    async clearData() {}
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

  it("stamps when the served contentStamp arrives after sync (PLANE-76 pages-list path)", async () => {
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
  });
});
