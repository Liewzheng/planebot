/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
// plane utils
import { cn } from "@plane/utils";
// local imports
import { formatMermaidError, renderMermaidToSVG } from "./mermaid-render";

type Props = {
  source: string;
};

// debounce delay (ms) before re-rendering the diagram while the source is being edited
const RENDER_DEBOUNCE_MS = 400;

const getThemeAttribute = (element: HTMLElement | null): string | null => {
  const themedAncestor = element?.closest("[data-theme]");
  if (themedAncestor) return themedAncestor.getAttribute("data-theme");
  return document.documentElement.getAttribute("data-theme");
};

export function MermaidDiagram({ source }: Props) {
  const { t } = useTranslation("editor");
  const containerRef = useRef<HTMLDivElement>(null);
  // states
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [themeAttribute, setThemeAttribute] = useState<string | null>(null);

  // track the active `data-theme` (set by next-themes) so the diagram re-renders on theme changes
  useEffect(() => {
    const resolveTheme = () => setThemeAttribute(getThemeAttribute(containerRef.current));
    resolveTheme();
    const observer = new MutationObserver(resolveTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  // render the diagram, debounced, whenever the source or theme changes
  useEffect(() => {
    if (!source.trim()) {
      setError(null);
      setIsLoading(false);
      if (containerRef.current) containerRef.current.innerHTML = "";
      return;
    }

    let cancelled = false;
    setIsLoading(true);

    const renderTimer = setTimeout(() => {
      void (async () => {
        try {
          const svg = await renderMermaidToSVG(source, themeAttribute ?? undefined);
          if (cancelled) return;

          const container = containerRef.current;
          if (!container) {
            setIsLoading(false);
            return;
          }

          container.innerHTML = svg;
          setError(null);
          setIsLoading(false);
        } catch (err) {
          if (cancelled) return;
          setError(formatMermaidError(err));
          setIsLoading(false);
        }
      })();
    }, RENDER_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(renderTimer);
    };
  }, [source, themeAttribute]);

  // keep the canvas mounted in every state — unmounting it on error would leave
  // containerRef null on the next render pass, so the diagram could never recover
  const state = error ? "error" : isLoading ? "loading" : "rendered";
  return (
    <div className={cn("mermaid-diagram", { "mermaid-diagram-error": error })} data-mermaid-state={state}>
      {error && (
        <>
          <p className="mermaid-diagram-error-message">{error}</p>
          <pre className="mermaid-diagram-source">
            <code>{source}</code>
          </pre>
        </>
      )}
      {!error && isLoading && <div className="mermaid-diagram-loading">{t("mermaidDiagram.rendering")}</div>}
      <div ref={containerRef} className="mermaid-diagram-canvas" hidden={!!error} />
    </div>
  );
}
