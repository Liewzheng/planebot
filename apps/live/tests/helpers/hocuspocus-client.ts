/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { WebSocket } from "ws";
import * as Y from "yjs";

/** Message types of the hocuspocus wire protocol. */
const MESSAGE_TYPE = {
  Sync: 0,
  Awareness: 1,
  Auth: 2,
  Stateless: 5,
} as const;

/** Sub-message types of the hocuspocus auth message. */
const AUTH_MESSAGE_TYPE = {
  Token: 0,
  PermissionDenied: 1,
  Authenticated: 2,
} as const;

/** yjs sync message types. */
const YJS_SYNC_TYPE = {
  SyncStep1: 0,
  SyncStep2: 1,
  Update: 2,
} as const;

const encodeVarUint = (value: number): Buffer => {
  const bytes: number[] = [];
  let num = value;
  while (num >= 0x80) {
    bytes.push((num & 0x7f) | 0x80);
    num = Math.floor(num / 128);
  }
  bytes.push(num);
  return Buffer.from(bytes);
};

const encodeVarString = (value: string): Buffer => {
  const payload = Buffer.from(value, "utf8");
  return Buffer.concat([encodeVarUint(payload.length), payload]);
};

const encodeVarUint8Array = (payload: Uint8Array): Buffer =>
  Buffer.concat([encodeVarUint(payload.length), Buffer.from(payload)]);

const readVarUint = (buffer: Buffer, offset: number): { value: number; offset: number } => {
  let num = 0;
  let shift = 1;
  let cursor = offset;
  let byte = buffer[cursor++];
  while (byte !== undefined && (byte & 0x80) !== 0) {
    num += (byte & 0x7f) * shift;
    shift *= 128;
    byte = buffer[cursor++];
  }
  if (byte !== undefined) num += byte * shift;
  return { value: num, offset: cursor };
};

const readVarString = (buffer: Buffer, offset: number): { value: string; offset: number } => {
  const length = readVarUint(buffer, offset);
  const end = length.offset + length.value;
  return { value: buffer.subarray(length.offset, end).toString("utf8"), offset: end };
};

export type TRawClientOptions = {
  /** websocket url including the query string the web client sends */
  url: string;
  documentName: string;
  /** the token handed to the server (JSON blob for humans, raw token for SPs) */
  token: string;
  /** how long to wait for the auth answer */
  timeoutMs?: number;
};

/**
 * A hand-rolled hocuspocus protocol client.
 *
 * The live app does not depend on `@hocuspocus/provider` (only the web
 * client does), so the wire protocol is spoken directly here: the client
 * sends the auth message, answers the sync handshake and records the
 * stateless payloads the server broadcasts on the document.
 */
export class RawHocuspocusClient {
  private socket: WebSocket;
  private readonly documentName: string;
  private readonly timeoutMs: number;
  private timer: NodeJS.Timeout | null = null;
  /** resolves the connect promise once the server answered the auth message */
  private onAuthAnswer: (() => void) | null = null;
  /** waiters for a stateless payload */
  private statelessWaiters: ((payload: string) => void)[] = [];
  /** waiters for the completed sync handshake */
  private syncWaiters: (() => void)[] = [];

  /** the server accepted the connection */
  public authenticated = false;
  /** the server refused the connection */
  public permissionDenied = false;
  /** the reason the server gave for refusing */
  public permissionDeniedReason: string | null = null;
  /** the server answered the sync handshake */
  public synced = false;
  /** every stateless payload the server broadcast on the document */
  public statelessPayloads: string[] = [];
  /** the socket closed */
  public closed = false;
  public closeReason: string | null = null;

  private constructor(socket: WebSocket, documentName: string, timeoutMs: number) {
    this.socket = socket;
    this.documentName = documentName;
    this.timeoutMs = timeoutMs;
  }

  static connect({ url, documentName, token, timeoutMs = 5000 }: TRawClientOptions): Promise<RawHocuspocusClient> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url);
      const client = new RawHocuspocusClient(socket, documentName, timeoutMs);

      const settle = (error?: Error) => {
        if (error) reject(error);
        else resolve(client);
      };
      client.onAuthAnswer = () => settle();

      socket.on("open", () => {
        client.sendAuth(token);
      });

      socket.on("message", (data: Buffer | ArrayBuffer | Buffer[]) => {
        client.handleMessage(Buffer.isBuffer(data) ? data : Buffer.from(data as ArrayBuffer));
      });

      socket.on("close", (code: number, reason: Buffer) => {
        client.closed = true;
        client.closeReason = reason?.toString() || `code ${code}`;
        client.clearTimer();
        settle();
      });

      socket.on("error", (error: Error) => {
        client.clearTimer();
        settle(error);
      });

      client.armTimer(() => settle(new Error("Timed out waiting for the authentication answer")));
    });
  }

  private armTimer(onTimeout: () => void) {
    this.timer = setTimeout(onTimeout, this.timeoutMs);
  }

  private clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private sendAuth(token: string) {
    const message = Buffer.concat([
      encodeVarString(this.documentName),
      encodeVarUint(MESSAGE_TYPE.Auth),
      encodeVarUint(AUTH_MESSAGE_TYPE.Token),
      encodeVarString(token),
    ]);
    this.socket.send(message);
  }

  private sendSyncStep1() {
    const document = new Y.Doc();
    const message = Buffer.concat([
      encodeVarString(this.documentName),
      encodeVarUint(MESSAGE_TYPE.Sync),
      encodeVarUint(YJS_SYNC_TYPE.SyncStep1),
      encodeVarUint8Array(Y.encodeStateVector(document)),
    ]);
    this.socket.send(message);
  }

  private sendSyncStep2() {
    const document = new Y.Doc();
    const message = Buffer.concat([
      encodeVarString(this.documentName),
      encodeVarUint(MESSAGE_TYPE.Sync),
      encodeVarUint(YJS_SYNC_TYPE.SyncStep2),
      encodeVarUint8Array(Y.encodeStateAsUpdate(document)),
    ]);
    this.socket.send(message);
  }

  private handleMessage(data: Buffer) {
    // every server message starts with the document name
    const name = readVarString(data, 0);
    if (name.value !== this.documentName) return;
    const type = readVarUint(data, name.offset);
    switch (type.value) {
      case MESSAGE_TYPE.Auth: {
        const subType = readVarUint(data, type.offset);
        if (subType.value === AUTH_MESSAGE_TYPE.PermissionDenied) {
          this.permissionDenied = true;
          this.permissionDeniedReason = readVarString(data, subType.offset).value;
          this.clearTimer();
          this.onAuthAnswer?.();
          return;
        }
        if (subType.value === AUTH_MESSAGE_TYPE.Authenticated) {
          this.authenticated = true;
          this.clearTimer();
          this.onAuthAnswer?.();
          this.sendSyncStep1();
        }
        return;
      }
      case MESSAGE_TYPE.Stateless: {
        const payload = readVarString(data, type.offset).value;
        this.statelessPayloads.push(payload);
        this.statelessWaiters.splice(0).forEach((waiter) => waiter(payload));
        return;
      }
      case MESSAGE_TYPE.Sync: {
        const syncType = readVarUint(data, type.offset);
        if (syncType.value === YJS_SYNC_TYPE.SyncStep1) {
          this.sendSyncStep2();
          return;
        }
        if (syncType.value === YJS_SYNC_TYPE.SyncStep2) {
          this.synced = true;
          this.syncWaiters.splice(0).forEach((waiter) => waiter());
        }
        return;
      }
      default:
        return;
    }
  }

  /** Wait until the client is authenticated / refused / synced. */
  async waitForAuth(): Promise<void> {
    if (this.authenticated || this.permissionDenied || this.closed) return;
    await new Promise<void>((resolve) => {
      const check = () => {
        if (this.authenticated || this.permissionDenied || this.closed) {
          this.socket.off("message", check);
          this.socket.off("close", check);
          resolve();
          return;
        }
      };
      this.socket.on("message", check);
      this.socket.on("close", check);
    });
  }

  /** Wait for a stateless payload to arrive (or time out). */
  async waitForStateless(timeoutMs = 2000): Promise<string | null> {
    if (this.statelessPayloads.length > 0) return this.statelessPayloads[0];
    let timer: NodeJS.Timeout;
    const waiter = new Promise<string>((resolve) => {
      this.statelessWaiters.push((payload) => {
        clearTimeout(timer);
        resolve(payload);
      });
    });
    const timeout = new Promise<null>((resolve) => {
      timer = setTimeout(() => resolve(null), timeoutMs);
    });
    return Promise.race([waiter, timeout]);
  }

  /**
   * Wait until the sync handshake completed.
   *
   * The connection is attached to the document only after the
   * authentication answer was sent, so a broadcast issued before the
   * handshake finished would not reach this client.
   */
  async waitForSync(timeoutMs = 2000): Promise<void> {
    if (this.synced || this.permissionDenied || this.closed) return;
    let timer: NodeJS.Timeout;
    const waiter = new Promise<void>((resolve) => {
      this.syncWaiters.push(() => {
        clearTimeout(timer);
        resolve();
      });
    });
    const timeout = new Promise<void>((resolve) => {
      timer = setTimeout(resolve, timeoutMs);
    });
    return Promise.race([waiter, timeout]);
  }

  /** Give the server a moment to deliver anything that is in flight. */
  async settle(ms = 250): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, ms));
  }

  destroy() {
    this.clearTimer();
    try {
      this.socket.close();
    } catch {
      // the socket may already be closed
    }
  }
}
