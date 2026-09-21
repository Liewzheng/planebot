/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Minimal ZIP writer, for exports that ship more than one file (a page's
 * markdown next to the images it references).
 *
 * Entries are stored, not deflated: the payloads are already-compressed images
 * and a markdown file of a few hundred kilobytes, so a compression pass would
 * cost seconds to save nothing. Sizes are known before writing, so no data
 * descriptors are needed and the header layout stays the simple one every
 * archive tool reads.
 */

/** General purpose bit 11: the entry name is UTF-8, not CP437. */
const NAME_ENCODING_FLAG = 0x0800;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index++) {
    let value = index;
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[index] = value >>> 0;
  }
  return table;
})();

/** CRC-32 of a byte array, as every ZIP entry header carries it. */
export const crc32 = (data: Uint8Array): number => {
  let crc = 0xffffffff;
  for (let index = 0; index < data.length; index++) {
    crc = CRC_TABLE[(crc ^ data[index]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
};

export type TZipEntry = {
  /** Path inside the archive, forward slashes, e.g. "assets/image-1.png". */
  path: string;
  data: Uint8Array;
};

/** MS-DOS packed date and time, the stamp a ZIP entry carries. */
const dosStamp = (date: Date): { time: number; date: number } => ({
  time: (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1),
  date: ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
});

/**
 * Build a ZIP archive out of the given entries.
 *
 * @param entries files to archive, in the order they should appear
 * @param modifiedAt stamp written into every entry (the caller's clock; tests pass a fixed date)
 */
export const createZip = (entries: TZipEntry[], modifiedAt: Date = new Date()): Blob => {
  const encoder = new TextEncoder();
  const { time, date } = dosStamp(modifiedAt);
  const parts: BlobPart[] = [];
  const centralDirectory: Uint8Array[] = [];
  let offset = 0;

  entries.forEach((entry) => {
    const name = encoder.encode(entry.path);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    // local file header: the entry's name, then its bytes
    const header = new Uint8Array(30 + name.length);
    const headerView = new DataView(header.buffer);
    headerView.setUint32(0, 0x04034b50, true); // signature
    headerView.setUint16(4, 20, true); // version needed to extract (2.0)
    // 0x0800: names are UTF-8. Exports are named after the page, which is not
    // ASCII for most of this instance's content.
    headerView.setUint16(6, NAME_ENCODING_FLAG, true);
    headerView.setUint16(8, 0, true); // method: store
    headerView.setUint16(10, time, true);
    headerView.setUint16(12, date, true);
    headerView.setUint32(14, crc, true);
    headerView.setUint32(18, size, true); // compressed size
    headerView.setUint32(22, size, true); // uncompressed size
    headerView.setUint16(26, name.length, true);
    headerView.setUint16(28, 0, true); // extra field length
    header.set(name, 30);

    parts.push(header, entry.data);

    // central directory entry, written after all the file data
    const central = new Uint8Array(46 + name.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true); // signature
    centralView.setUint16(4, 20, true); // version made by
    centralView.setUint16(6, 20, true); // version needed
    centralView.setUint16(8, NAME_ENCODING_FLAG, true); // flags
    centralView.setUint16(10, 0, true); // method: store
    centralView.setUint16(12, time, true);
    centralView.setUint16(14, date, true);
    centralView.setUint32(16, crc, true);
    centralView.setUint32(20, size, true);
    centralView.setUint32(24, size, true);
    centralView.setUint16(28, name.length, true);
    centralView.setUint16(30, 0, true); // extra field length
    centralView.setUint16(32, 0, true); // comment length
    centralView.setUint16(34, 0, true); // disk number
    centralView.setUint16(36, 0, true); // internal attributes
    centralView.setUint32(38, 0, true); // external attributes
    centralView.setUint32(42, offset, true); // offset of the local header
    central.set(name, 46);
    centralDirectory.push(central);

    offset += header.length + size;
  });

  const centralSize = centralDirectory.reduce((total, entry) => total + entry.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true); // end of central directory signature
  endView.setUint16(4, 0, true); // disk number
  endView.setUint16(6, 0, true); // disk with the central directory
  endView.setUint16(8, entries.length, true); // entries on this disk
  endView.setUint16(10, entries.length, true); // entries in total
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, offset, true); // where the central directory starts
  endView.setUint16(20, 0, true); // comment length

  return new Blob([...parts, ...centralDirectory, end], { type: "application/zip" });
};
