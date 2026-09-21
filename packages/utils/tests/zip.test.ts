/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
// utils
import { createZip, crc32, type TZipEntry } from "../src/zip";

const bytesOf = (text: string) => new TextEncoder().encode(text);

/** Read back the entries of an archive the way an unzip tool walks it. */
const readZip = async (blob: Blob) => {
  const buffer = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(buffer.buffer);
  const entries: { path: string; data: Uint8Array; crc: number; offset: number }[] = [];

  const endOffset = buffer.length - 22;
  expect(view.getUint32(endOffset, true)).toBe(0x06054b50);
  const count = view.getUint16(endOffset + 10, true);
  let cursor = view.getUint32(endOffset + 16, true);

  for (let index = 0; index < count; index++) {
    expect(view.getUint32(cursor, true)).toBe(0x02014b50);
    const crc = view.getUint32(cursor + 16, true);
    const size = view.getUint32(cursor + 20, true);
    const nameLength = view.getUint16(cursor + 28, true);
    const localOffset = view.getUint32(cursor + 42, true);
    const path = new TextDecoder().decode(buffer.slice(cursor + 46, cursor + 46 + nameLength));

    expect(view.getUint32(localOffset, true)).toBe(0x04034b50);
    const dataStart = localOffset + 30 + view.getUint16(localOffset + 26, true);
    entries.push({ path, data: buffer.slice(dataStart, dataStart + size), crc, offset: localOffset });
    cursor += 46 + nameLength + view.getUint16(cursor + 30, true) + view.getUint16(cursor + 32, true);
  }

  return entries;
};

describe("createZip", () => {
  const entries: TZipEntry[] = [
    { path: "page.md", data: bytesOf("# 标题\n\n正文\n") },
    { path: "assets/image-1.png", data: new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) },
  ];

  it("archives every entry with its bytes, name and checksum", async () => {
    const zip = createZip(entries, new Date("2026-09-18T10:00:00Z"));
    const read = await readZip(zip);

    expect(read.map((entry) => entry.path)).toEqual(["page.md", "assets/image-1.png"]);
    expect(new TextDecoder().decode(read[0].data)).toBe("# 标题\n\n正文\n");
    expect(Array.from(read[1].data)).toEqual(Array.from(entries[1].data));
    read.forEach((entry, index) => expect(entry.crc).toBe(crc32(entries[index].data)));
  });

  it("marks entry names as UTF-8, so a Chinese page title survives", async () => {
    const buffer = new Uint8Array(
      await createZip([{ path: "论文.md", data: bytesOf("x") }], new Date("2026-09-18T10:00:00Z")).arrayBuffer()
    );
    const view = new DataView(buffer.buffer);
    const centralOffset = view.getUint32(buffer.length - 22 + 16, true);
    expect(view.getUint16(6, true) & 0x0800).toBe(0x0800); // local header flags
    expect(view.getUint16(centralOffset + 8, true) & 0x0800).toBe(0x0800); // central directory flags
  });

  it("is a zip the browser names as such", () => {
    expect(createZip(entries).type).toBe("application/zip");
  });

  it("writes the offset of every local header", async () => {
    const read = await readZip(createZip(entries));
    expect(read[0].offset).toBe(0);
    expect(read[1].offset).toBe(30 + "page.md".length + entries[0].data.length);
  });
});

describe("crc32", () => {
  it("matches the known checksum of a known string", () => {
    // the canonical CRC-32 of "123456789"
    expect(crc32(bytesOf("123456789"))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
  });
});
