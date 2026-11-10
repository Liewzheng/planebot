/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Dialog, Transition } from "@headlessui/react";
import React, { Fragment, useEffect, useState } from "react";
// constants
import { cn } from "../utils";
import { EModalPosition, EModalWidth } from "./constants";
// helpers

/** The leave classes below keep the panel and backdrop on screen for this long. */
const LEAVE_ANIMATION_MS = 200;
/**
 * A leave that has not been torn down after its own animation is stuck, and a
 * stuck dialog is an invisible `fixed inset-0` layer that swallows clicks. So
 * the tree comes down on this deadline no matter what the transition reports.
 */
const TEARDOWN_AFTER_CLOSE_MS = LEAVE_ANIMATION_MS + 50;

type Props = {
  children: React.ReactNode;
  handleClose?: () => void;
  isOpen: boolean;
  position?: EModalPosition;
  width?: EModalWidth;
  className?: string;
};
export function ModalCore(props: Props) {
  const {
    children,
    handleClose,
    isOpen,
    position = EModalPosition.CENTER,
    width = EModalWidth.XXL,
    className = "",
  } = props;

  /**
   * The dialog owns its own mount window instead of leaving it to
   * `Transition.Root`. `Transition.Root` only unmounts itself once headlessui's
   * nesting bookkeeping reports every `Transition.Child` gone; when that report
   * never arrives the whole tree stays mounted — panel and backdrop faded to
   * nothing, but the `fixed inset-0 z-30 overflow-y-auto` scroll container still
   * there with `pointer-events: auto`, swallowing every click meant for the page
   * behind it.
   *
   * `isMounted` is what keeps the leave animation on screen: it is raised while
   * the dialog is open and lowered one teardown window after `isOpen` goes
   * false. Rendering is gated on `isOpen || isMounted`, so an opening dialog
   * paints in the same commit, while a closing one is unmounted by our timer
   * whatever the transition layer did or did not report.
   */
  const [isMounted, setIsMounted] = useState(isOpen);

  useEffect(() => {
    if (isOpen) {
      setIsMounted(true);
      return;
    }
    if (!isMounted) return;
    const teardown = setTimeout(() => setIsMounted(false), TEARDOWN_AFTER_CLOSE_MS);
    return () => clearTimeout(teardown);
  }, [isOpen, isMounted]);

  if (!isOpen && !isMounted) return null;

  return (
    // `appear` is what animates the panel in: this tree now mounts per opening
    // rather than being kept around, and without it every open after the first
    // would pop in at full opacity.
    <Transition.Root show={isOpen} appear as={Fragment}>
      <Dialog as="div" className="relative z-30" onClose={() => handleClose && handleClose()}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-backdrop transition-opacity" />
        </Transition.Child>

        <div className="fixed inset-0 z-30 overflow-y-auto">
          <div className={position}>
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
              enterTo="opacity-100 translate-y-0 sm:scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 translate-y-0 sm:scale-100"
              leaveTo="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            >
              <Dialog.Panel
                className={cn(
                  "relative w-full transform rounded-lg bg-surface-1 text-left shadow-raised-200 transition-all",
                  width,
                  className
                )}
              >
                {children}
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition.Root>
  );
}
