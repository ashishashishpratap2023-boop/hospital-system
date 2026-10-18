// WhatsApp session credentials are encrypted at rest (AES-256-GCM). Never stored in plaintext.
import { promises as fs } from 'fs';
import path from 'path';
import crypto from 'crypto';
import { initAuthCreds, BufferJSON, proto } from '@whiskeysockets/baileys';

const keyOf = (secret) => crypto.createHash('sha256').update(secret).digest();

export function seal(obj, secret) {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', keyOf(secret), iv);
  const enc = Buffer.concat([c.update(JSON.stringify(obj, BufferJSON.replacer), 'utf8'), c.final()]);
  return Buffer.concat([iv, c.getAuthTag(), enc]);
}

export function unseal(buf, secret) {
  const d = crypto.createDecipheriv('aes-256-gcm', keyOf(secret), buf.subarray(0, 12));
  d.setAuthTag(buf.subarray(12, 28));
  const plain = Buffer.concat([d.update(buf.subarray(28)), d.final()]).toString('utf8');
  return JSON.parse(plain, BufferJSON.reviver);
}

const fname = (type, id) => `${type}-${String(id)}`.replace(/[^\w.-]/g, '_') + '.enc';

export async function useEncryptedAuthState(dir, secret) {
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  const read = async (f) => { try { return unseal(await fs.readFile(path.join(dir, f)), secret); } catch { return null; } };
  const write = (f, o) => fs.writeFile(path.join(dir, f), seal(o, secret), { mode: 0o600 });
  const remove = (f) => fs.unlink(path.join(dir, f)).catch(() => {});
  const creds = (await read('creds.enc')) || initAuthCreds();
  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const out = {};
          await Promise.all(ids.map(async (id) => {
            let v = await read(fname(type, id));
            if (type === 'app-state-sync-key' && v) v = proto.Message.AppStateSyncKeyData.fromObject(v);
            out[id] = v;
          }));
          return out;
        },
        set: async (data) => {
          await Promise.all(Object.entries(data).flatMap(([type, m]) =>
            Object.entries(m).map(([id, v]) => (v ? write(fname(type, id), v) : remove(fname(type, id))))));
        },
      },
    },
    saveCreds: () => write('creds.enc', creds),
  };
}

export const wipe = (dir) => fs.rm(dir, { recursive: true, force: true });
