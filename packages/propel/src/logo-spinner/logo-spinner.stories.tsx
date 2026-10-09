/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Meta, StoryObj } from "@storybook/react-vite";
import { LogoSpinner } from "./logo-spinner";

const meta = {
  title: "Components/LogoSpinner",
  component: LogoSpinner,
  parameters: {
    layout: "centered",
  },
  tags: ["autodocs"],
} satisfies Meta<typeof LogoSpinner>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** The three sizes the app actually uses: 24px (small loaders), 44px (default), larger. */
export const Sizes: Story = {
  render() {
    return (
      <div className="flex items-end gap-6">
        <LogoSpinner className="h-6" />
        <LogoSpinner />
        <LogoSpinner className="h-16" />
      </div>
    );
  },
};

export const BothThemes: Story = {
  render() {
    return (
      <div className="flex flex-col gap-6">
        <div className="flex h-28 items-center justify-center rounded-md bg-canvas">
          <LogoSpinner />
        </div>
        <div className="dark flex h-28 items-center justify-center rounded-md bg-canvas">
          <LogoSpinner />
        </div>
      </div>
    );
  },
};
