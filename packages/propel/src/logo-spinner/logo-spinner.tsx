/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as React from "react";

/**
 * Global loading mark: the brand letter, then the robot it grows into.
 *
 * "planebot" is a pun on plantbot, so the sequence tells that story in beats:
 * the "p" is written on the brand square first (the only state a page that loads
 * in 300ms will show), then it is unwritten as the square stretches into the
 * robot's head — the letter becomes the mascot, the way the product is the
 * plant. The ears grow out of the head's sides, the face fades in and winks with
 * one eye, then everything folds back into the square and the loop restarts (3s).
 *
 * One blue throughout (the brand's own), nothing flashes, and the geometry
 * settles rather than jumps. `prefers-reduced-motion` keeps the finished robot:
 * the letter is dropped and every animation is off, which leaves the head at its
 * final size with the ears out and the face visible.
 */

const BRAND_BLUE = "#3f76ff";
const FACE_COLOR = "#ffffff";

/** Lowercase p as a single geometric stroke in the 64×64 viewBox. */
const LETTER_PATH = "M 20 49 L 20 27 A 12 12 0 0 1 44 27 A 12 12 0 0 1 20 27";

/** One global rule set; the `<style>` is repeated per instance, which is harmless. */
const SPINNER_STYLES = `
/* The loop OPENS on the finished letter: a page that loads in half a second only
   ever shows this frame, and it has to read as the logo, not as a dot. The letter
   is unwritten from there, and written back at the end of the cycle. */
@keyframes plane-logo-spinner-write {
  0%, 18%   { stroke-dashoffset: 0; animation-timing-function: cubic-bezier(.55,.05,.45,1); }
  27%       { stroke-dashoffset: 100; }
  86%       { stroke-dashoffset: 100; animation-timing-function: cubic-bezier(.65,.05,.35,1); }
  97%, 100% { stroke-dashoffset: 0; }
}
/* the square stretches into the head, and folds back at the end of the loop */
@keyframes plane-logo-spinner-shell {
  0%, 24%   { x: 0px; y: 0px; width: 64px; height: 64px; rx: 14px; animation-timing-function: cubic-bezier(.35,.9,.3,1); }
  40%, 72%  { x: 4px; y: 11px; width: 56px; height: 42px; rx: 13px; }
  86%, 100% { x: 0px; y: 0px; width: 64px; height: 64px; rx: 14px; }
}
/* the ears grow out of the head's sides, then retract toward its middle and fade */
@keyframes plane-logo-spinner-ear {
  0%, 30%   { transform: scale(0); opacity: 1; animation-timing-function: cubic-bezier(.2,.9,.3,1.4); }
  48%       { transform: scale(1); opacity: 1; }
  72%       { transform: scale(1); opacity: 1; animation-timing-function: cubic-bezier(.4,0,.7,.3); }
  86%, 100% { transform: scale(0); opacity: 0; }
}
/* the face arrives once the head has settled, and fades out as it folds back */
@keyframes plane-logo-spinner-face {
  0%, 38%   { opacity: 0; }
  50%, 76%  { opacity: 1; }
  86%, 100% { opacity: 0; }
}
@keyframes plane-logo-spinner-nod {
  0%, 50%   { transform: translateY(0); }
  55%       { transform: translateY(-1.6px); }
  61%, 100% { transform: translateY(0); }
}
/* one eye winks, the other stays open */
@keyframes plane-logo-spinner-wink {
  0%, 60%   { transform: scaleY(1); }
  64%       { transform: scaleY(.12); }
  68%, 100% { transform: scaleY(1); }
}
.plane-logo-spinner-letter { animation: plane-logo-spinner-write 3s infinite; }
.plane-logo-spinner-shell { animation: plane-logo-spinner-shell 3s infinite; }
.plane-logo-spinner-ear-left {
  transform-box: fill-box;
  transform-origin: 100% 50%;
  animation: plane-logo-spinner-ear 3s infinite;
}
.plane-logo-spinner-ear-right {
  transform-box: fill-box;
  transform-origin: 0% 50%;
  animation: plane-logo-spinner-ear 3s infinite;
}
.plane-logo-spinner-face { animation: plane-logo-spinner-face 3s infinite; }
.plane-logo-spinner-nod { animation: plane-logo-spinner-nod 3s ease-in-out infinite; }
.plane-logo-spinner-wink {
  transform-box: fill-box;
  transform-origin: center;
  animation: plane-logo-spinner-wink 3s ease-in-out infinite;
}
/* The finished robot is the state that has to look right on its own: the letter
   is not shown twice, nothing moves, the head keeps its final size. */
@media (prefers-reduced-motion: reduce) {
  .plane-logo-spinner-letter { display: none; }
  .plane-logo-spinner-shell,
  .plane-logo-spinner-ear-left,
  .plane-logo-spinner-ear-right,
  .plane-logo-spinner-face,
  .plane-logo-spinner-nod,
  .plane-logo-spinner-wink { animation: none; }
}
`;

export type TLogoSpinnerProps = {
  /** Extra classes for the mark; sizing stays with the component (h-6 → sm:h-11). */
  className?: string;
};

export function LogoSpinner({ className }: TLogoSpinnerProps = {}) {
  return (
    <div className="flex items-center justify-center">
      <style>{SPINNER_STYLES}</style>
      <svg
        className={`h-6 w-auto object-contain sm:h-11 ${className ?? ""}`}
        viewBox="0 0 64 64"
        xmlns="http://www.w3.org/2000/svg"
        role="img"
        aria-label="pbot logo"
      >
        <g className="plane-logo-spinner-nod">
          {/* ears sit under the head, so its edge hides where they meet */}
          <rect className="plane-logo-spinner-ear-left" x="0" y="26" width="7" height="6" rx="3" fill={BRAND_BLUE} />
          <rect className="plane-logo-spinner-ear-right" x="57" y="26" width="7" height="6" rx="3" fill={BRAND_BLUE} />

          {/* the head: a brand square that stretches into the robot's head.
              Attributes carry the final geometry so `prefers-reduced-motion` lands on the robot. */}
          <rect className="plane-logo-spinner-shell" x="4" y="11" width="56" height="42" rx="13" fill={BRAND_BLUE} />

          {/* beat 1 — the letter */}
          <path
            className="plane-logo-spinner-letter"
            d={LETTER_PATH}
            fill="none"
            stroke={FACE_COLOR}
            strokeWidth="6"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength="100"
            strokeDasharray="100"
          />

          {/* beat 3 — the face (symmetric about x = 32) */}
          <g className="plane-logo-spinner-face">
            <circle className="plane-logo-spinner-wink" cx="23" cy="29" r="3.4" fill={FACE_COLOR} />
            <circle cx="41" cy="29" r="3.4" fill={FACE_COLOR} />
            <path d="M27 39 Q32 43.4 37 39" stroke={FACE_COLOR} strokeWidth="2.4" fill="none" strokeLinecap="round" />
          </g>
        </g>
      </svg>
    </div>
  );
}
