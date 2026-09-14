/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { CopyOutline, DownloadOutline, TickOutline } from "@makeplane/propel/icons";
// plane imports
import { useTranslation } from "@plane/i18n";

// propel-style terminal icon (16x16, fill="currentColor") — lucide's
// stroke-based SquareTerminal does not match the neighboring propel icons
function PbotCliIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 16 16"
      width="1em"
      height="1em"
      aria-hidden="true"
      className={className}
    >
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M3.5 2.5h9c1.104 0 2 .896 2 2v7c0 1.104-.896 2-2 2h-9c-1.104 0-2-.896-2-2v-7c0-1.104.896-2 2-2zm0 1.25c-.414 0-.75.336-.75.75v7c0 .414.336.75.75.75h9c.414 0 .75-.336.75-.75v-7c0-.414-.336-.75-.75-.75h-9z"
      />
      <path
        fill="currentColor"
        d="M4.32 5.87a.55.55 0 0 1 .78-.03l2.5 2.3a.55.55 0 0 1 0 .8l-2.5 2.3a.55.55 0 1 1-.75-.83l2.1-1.885-2.1-1.885a.55.55 0 0 1-.03-.78z"
      />
      <path fill="currentColor" d="M8.4 10.4h3a.55.55 0 1 1 0 1.1h-3a.55.55 0 1 1 0-1.1z" />
    </svg>
  );
}

// Promo/download page for the pbot CLI (placeholder until the dedicated site is ready)
const PBOT_CLI_URL = "https://github.com/Liewzheng/planebotcli";
const PBOT_CLI_RELEASES_URL = `${PBOT_CLI_URL}/releases`;
const GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/Liewzheng/planebotcli/releases/latest";

// cargo-dist installers from the latest release; the scripts auto-detect the
// platform AND architecture, so they are the fallback when no binary matches
const PBOT_CLI_INSTALLER = {
  shell: `curl --proto '=https' --tlsv1.2 -LsSf ${PBOT_CLI_RELEASES_URL}/latest/download/planebotcli-cli-installer.sh | sh`,
  powershell: `irm ${PBOT_CLI_RELEASES_URL}/latest/download/planebotcli-cli-installer.ps1 | iex`,
} as const;

type TDetectedOS = "macos" | "windows" | "linux";
type TDetectedArch = "x86_64" | "aarch64";

const OS_TARGET_TRIPLE: Record<TDetectedOS, string> = {
  macos: "apple-darwin",
  windows: "pc-windows-msvc",
  linux: "unknown-linux-gnu",
};

const detectOS = (): TDetectedOS => {
  const ua = navigator.userAgent;
  if (/mac os x/i.test(ua)) return "macos";
  if (/windows/i.test(ua)) return "windows";
  return "linux";
};

// Chromium exposes the real CPU architecture through high-entropy hints;
// elsewhere fall back to UA substrings, then x86_64 (an x86_64 binary still
// runs under Rosetta on Apple Silicon, the reverse is not true)
const detectArch = async (): Promise<TDetectedArch> => {
  const uaData = (
    navigator as Navigator & {
      userAgentData?: {
        getHighEntropyValues?: (hints: string[]) => Promise<{ architecture?: string; bitness?: string }>;
      };
    }
  ).userAgentData;
  if (uaData?.getHighEntropyValues) {
    try {
      const { architecture, bitness } = await uaData.getHighEntropyValues(["architecture", "bitness"]);
      if (architecture === "arm" && bitness === "64") return "aarch64";
      if (architecture === "x86") return "x86_64";
    } catch {
      // fall through to UA sniffing
    }
  }
  if (/arm64|aarch64/i.test(navigator.userAgent)) return "aarch64";
  return "x86_64";
};

// Find the direct-download URL of the binary archive matching the visitor's
// platform; null when the API or the asset is unavailable (caller then keeps
// the installer one-liner as the fallback)
const fetchBinaryDownloadUrl = async (os: TDetectedOS, arch: TDetectedArch): Promise<string | null> => {
  const triple = `${arch}-${OS_TARGET_TRIPLE[os]}`;
  const response = await fetch(GITHUB_LATEST_RELEASE_API);
  if (!response.ok) return null;
  const data: { assets?: { name?: string; browser_download_url?: string }[] } = await response.json();
  const asset = data.assets?.find(
    ({ name }) => name === `planebotcli-cli-${triple}.tar.xz` || name === `planebotcli-cli-${triple}.zip`
  );
  return asset?.browser_download_url ?? null;
};

// navigator.clipboard requires a secure context; on plain-HTTP deployments
// fall back to the legacy textarea + execCommand path
const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const succeeded = document.execCommand("copy");
    document.body.removeChild(textarea);
    return succeeded;
  }
};

/**
 * Top-navigation entry for the pbot CLI: clicking opens the promo page,
 * hovering shows a platform-matched binary download (falling back to the
 * installer one-liner) plus verify/configure hints. Browsers cannot probe
 * the local PATH, so "is it installed" is answered with `pbot whoami`.
 */
export function PbotCliLink() {
  // plane hooks
  const { t } = useTranslation();
  // states — resolved post-hydration to keep SSR and first client render equal
  const [os, setOs] = useState<TDetectedOS>("linux");
  const [origin, setOrigin] = useState("");
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [isCopied, setIsCopied] = useState(false);

  useEffect(() => {
    const detectedOS = detectOS();
    setOs(detectedOS);
    setOrigin(window.location.origin);

    let isCancelled = false;
    const resolveDownloadUrl = async () => {
      try {
        const url = await fetchBinaryDownloadUrl(detectedOS, await detectArch());
        if (!isCancelled && url) setDownloadUrl(url);
      } catch {
        // network/API unavailable — keep the installer one-liner
      }
    };
    resolveDownloadUrl();
    return () => {
      isCancelled = true;
    };
  }, []);

  const installCommand = os === "windows" ? PBOT_CLI_INSTALLER.powershell : PBOT_CLI_INSTALLER.shell;

  const handleCopy = async () => {
    if (await copyToClipboard(installCommand)) {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };

  return (
    <div className="group relative flex-shrink-0">
      <a
        aria-label={t("home.pbot_cli.title")}
        className="flex size-8 items-center justify-center rounded-md text-icon-tertiary hover:bg-layer-transparent-hover hover:text-icon-secondary"
        href={PBOT_CLI_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        <PbotCliIcon className="size-5" />
      </a>
      <div className="invisible absolute top-full right-0 z-30 mt-1 w-84 rounded-md border border-subtle bg-surface-1 p-4 opacity-0 shadow-raised-200 transition-opacity group-hover:visible group-hover:opacity-100">
        <div className="flex flex-col gap-y-2">
          <div>
            <h5 className="text-13 font-medium">{t("home.pbot_cli.title")}</h5>
            <p className="text-11 text-tertiary">{t("home.pbot_cli.description")}</p>
          </div>
          {downloadUrl ? (
            <a
              href={downloadUrl}
              className="flex items-center justify-center gap-1.5 rounded-md bg-accent-primary px-3 py-1.5 text-11 font-medium text-on-color"
            >
              <DownloadOutline className="size-3.5" />
              {t("home.pbot_cli.download_now")}
            </a>
          ) : (
            <div className="flex items-stretch gap-1.5">
              <pre className="font-mono min-w-0 flex-1 overflow-x-auto rounded-md border border-subtle bg-surface-2 px-3 py-2 text-11 whitespace-nowrap">
                {installCommand}
              </pre>
              <button
                type="button"
                onClick={handleCopy}
                className="flex shrink-0 items-center gap-1 rounded-md border border-subtle bg-surface-2 px-2 text-11 text-tertiary hover:bg-layer-1-hover"
              >
                {isCopied ? <TickOutline className="size-3" /> : <CopyOutline className="size-3" />}
                {isCopied ? t("home.pbot_cli.copied") : t("home.pbot_cli.copy_install")}
              </button>
            </div>
          )}
          <div className="flex flex-col gap-y-0.5 border-t border-subtle pt-2">
            <p className="text-11 text-tertiary">
              {t("home.pbot_cli.verify_hint")}{" "}
              <code className="font-mono rounded-xs border border-subtle bg-surface-2 px-1 py-0.5">pbot whoami</code>
            </p>
            <p className="text-11 text-tertiary">
              {origin ? (
                <>
                  {t("home.pbot_cli.configure_hint")}{" "}
                  <code className="font-mono rounded-xs border border-subtle bg-surface-2 px-1 py-0.5">
                    pbot configure {origin}
                  </code>
                </>
              ) : (
                t("home.pbot_cli.configure_hint_fallback")
              )}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
