/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import type { TServicePrincipal } from "@plane/types";
// local imports
import { ServicePrincipalsListItem } from "./service-principals-list-item";

type Props = {
  principals: TServicePrincipal[];
  workspaceSlug: string;
};

export function ServicePrincipalsList(props: Props) {
  const { principals, workspaceSlug } = props;

  return (
    <div className="flex flex-col gap-2">
      {principals.map((principal) => (
        <ServicePrincipalsListItem key={principal.id} principal={principal} workspaceSlug={workspaceSlug} />
      ))}
    </div>
  );
}