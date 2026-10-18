// Internal bridge: one WhatsApp (QR-linked) session per hospital. Only the backend should reach this service.
import express from 'express';
import QRCode from 'qrcode';
import pino from 'pino';
import { promises as fs } from 'fs';
import path from 'path';
import makeWASocket, { DisconnectReason, fetchLatestBaileysVersion } from '@whiskeysockets/baileys';
import { useEncryptedAuthState, wipe } from './authstore.js';
import { Pacer } from './pacer.js';

const SECRET = process.env.BRIDGE_SECRET || '';
const ENC = process.env.SESSION_ENC_KEY || '';
if (SECRET.length < 24 || ENC.length < 32) {
  console.error('Set BRIDGE_SECRET (>=24 chars) and SESSION_ENC_KEY (>=32 chars).');
  process.exit(1);
}
const DATA = process.env.SESSIONS_DIR || './sessions';
const sessions = new Map();   // tenantId -> { sock, status, qr, phone, pacer, sent: Map(ref -> messageId) }
const dirOf = (id) => path.join(DATA, `tenant-${id}`);

async function start(id) {
  let s = sessions.get(id);
  if (s && ['connecting', 'qr', 'connected'].includes(s.status)) return s;
  s = s || { pacer: new Pacer({ maxPerDay: Number(process.env.MAX_PER_DAY || 250) }), sent: new Map() };
  Object.assign(s, { status: 'connecting', qr: null });
  sessions.set(id, s);
  const { state, saveCreds } = await useEncryptedAuthState(dirOf(id), ENC);
  const { version } = await fetchLatestBaileysVersion();
  const sock = makeWASocket({ version, auth: state, logger: pino({ level: 'silent' }), browser: ['Hospital Appointments', 'Chrome', '1.0'] });
  s.sock = sock;
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', async (u) => {
    if (u.qr) { s.qr = await QRCode.toDataURL(u.qr); s.status = 'qr'; }
    if (u.connection === 'open') { s.status = 'connected'; s.qr = null; s.phone = (sock.user?.id || '').split(':')[0].split('@')[0]; }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode;
      s.status = 'disconnected'; s.qr = null;
      if (code === DisconnectReason.loggedOut) await wipe(dirOf(id));   // phone unlinked the device: forget the session
      else setTimeout(() => start(id).catch(() => {}), 5000);           // temporary drop: reconnect
    }
  });
  return s;
}

const view = (s) => ({ status: s?.status || 'disconnected', qr: s?.qr || null, phone: s?.phone || null });
const app = express();
app.use(express.json({ limit: '20kb' }));
app.use((req, res, next) => (req.get('x-bridge-secret') === SECRET ? next() : res.status(401).json({ error: 'unauthorized' })));
app.param('id', (req, res, next, id) => (/^\d{1,10}$/.test(id) ? next() : res.status(400).json({ error: 'bad_tenant' })));

app.post('/sessions/:id/start', async (req, res) => res.json(view(await start(req.params.id))));
app.get('/sessions/:id', (req, res) => res.json(view(sessions.get(req.params.id))));
app.delete('/sessions/:id', async (req, res) => {
  const s = sessions.get(req.params.id);
  try { await s?.sock?.logout(); } catch { /* already closed */ }
  sessions.delete(req.params.id);
  await wipe(dirOf(req.params.id));
  res.json({ status: 'disconnected' });
});

app.post('/sessions/:id/send', async (req, res) => {
  const s = sessions.get(req.params.id);
  if (!s || s.status !== 'connected') return res.status(409).json({ error: 'not_connected' });
  const { to, text, ref } = req.body || {};
  const digits = String(to || '').replace(/\D/g, '');
  if (digits.length < 8 || digits.length > 15 || typeof text !== 'string' || !text || text.length > 4000) return res.status(400).json({ error: 'bad_request' });
  if (ref && s.sent.has(ref)) return res.json({ message_id: s.sent.get(ref) });   // retry of an already-sent message: do not send twice
  try {
    const messageId = await s.pacer.run(async () => {
      const [r] = await s.sock.onWhatsApp(digits);
      if (!r?.exists) throw Object.assign(new Error('not_on_whatsapp'), { code: 404 });
      return (await s.sock.sendMessage(r.jid, { text })).key.id;
    });
    if (ref) s.sent.set(ref, messageId);
    res.json({ message_id: messageId });
  } catch (e) {
    res.status(e.code === 404 ? 404 : e.code === 429 ? 429 : 502).json({ error: e.message });
  }
});

// Restore sessions that were linked before a restart.
for (const d of await fs.readdir(DATA).catch(() => [])) {
  const m = /^tenant-(\d+)$/.exec(d);
  if (m) start(m[1]).catch(() => {});
}
const port = Number(process.env.PORT || 3100);
app.listen(port, process.env.HOST || '127.0.0.1', () => console.log(`WhatsApp bridge on ${port}`));
