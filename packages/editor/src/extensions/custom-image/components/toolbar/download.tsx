/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { DownloadOutline } from "@makeplane/propel/icons";
// plane imports
import { Tooltip } from "@plane/propel/tooltip";
import { useTranslation } from "react-i18next";

type Props = {
  src: string;
};

export function ImageDownloadAction(props: Props) {
  const { src } = props;
  const { t } = useTranslation("editor");

  return (
    <Tooltip tooltipContent={t("download")}>
      <button
        type="button"
        onClick={() => window.open(src, "_blank")}
        className="grid h-full flex-shrink-0 place-items-center text-white/60 transition-colors hover:text-white"
        aria-label={t("download_image")}
      >
        <DownloadOutline className="size-3" />
      </button>
    </Tooltip>
  );
}
