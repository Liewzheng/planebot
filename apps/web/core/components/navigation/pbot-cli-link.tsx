/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { CopyOutline, DownloadOutline } from "@makeplane/propel/icons";
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

// cargo-dist installers from the latest release; the scripts auto-detect the
// platform AND architecture, so the card only needs the visitor's OS
const PBOT_CLI_INSTALLER = {
  shell: `curl --proto '=https' --tlsv1.2 -LsSf ${PBOT_CLI_RELEASES_URL}/latest/download/planebotcli-cli-installer.sh | sh`,
  powershell: `irm ${PBOT_CLI_RELEASES_URL}/latest/download/planebotcli-cli-installer.ps1 | iex`,
} as const;

type TDetectedOS = "macos" | "windows" | "linux";

const detectOS = (): TDetectedOS => {
  const ua = navigator.userAgent;
  if (/mac os x/i.test(ua)) return "macos";
  if (/windows/i.test(ua)) return "windows";
  return "linux";
};

/**
 * Top-navigation entry for the pbot CLI: clicking opens the promo page,
 * hovering shows the OS-matched binary installer one-liner plus
 * verify/configure hints. Browsers cannot probe the local PATH, so
 * "is it installed" is answered with `pbot whoami` instead of detection.
 */
export function PbotCliLink() {
  // plane hooks
  const { t } = useTranslation();
  // states — resolved post-hydration to keep SSR and first client render equal
  const [os, setOs] = useState<TDetectedOS>("linux");
  const [origin, setOrigin] = useState("");
  const [isCopied, setIsCopied] = useState(false);

  useEffect(() => {
    setOs(detectOS());
    setOrigin(window.location.origin);
  }, []);

  const installCommand = os === "windows" ? PBOT_CLI_INSTALLER.powershell : PBOT_CLI_INSTALLER.shell;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(installCommand);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {
      // clipboard unavailable (non-secure context) — the command stays visible for manual copy
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
          <div className="relative">
            <pre className="font-mono overflow-x-auto rounded-md border border-subtle bg-surface-2 px-3 pt-2 pb-8 text-11 break-all whitespace-pre-wrap">
              {installCommand}
            </pre>
            <button
              type="button"
              onClick={handleCopy}
              className="absolute right-1.5 bottom-1.5 flex items-center gap-1 rounded-xs border border-subtle bg-surface-1 px-1.5 py-0.5 text-11 text-tertiary hover:bg-layer-1-hover"
            >
              {isCopied ? <DownloadOutline className="size-3" /> : <CopyOutline className="size-3" />}
              {isCopied ? t("home.pbot_cli.copied") : t("home.pbot_cli.copy_install")}
            </button>
          </div>
          <div className="flex flex-col gap-y-0.5 border-t border-subtle pt-2">
            <p className="text-11 text-tertiary">{t("home.pbot_cli.verify_hint")}</p>
            <p className="text-11 text-tertiary">
              {t("home.pbot_cli.configure_hint", { url: origin || t("home.pbot_cli.this_site") })}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
