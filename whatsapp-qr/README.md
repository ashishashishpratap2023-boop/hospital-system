# WhatsApp QR bridge (free, no Meta API)
Uses the open-source Baileys library (github.com/WhiskeySockets/Baileys). **Unofficial**: it is not endorsed by WhatsApp and
its use may violate WhatsApp's Terms; the linked number can be restricted or banned. Use a dedicated SIM/number, message only
patients who agreed, and keep volume low (this bridge sends one message at a time with delays and a daily cap).
```
npm install
export BRIDGE_SECRET=$(openssl rand -hex 24) SESSION_ENC_KEY=$(openssl rand -hex 32)
npm start            # listens on 127.0.0.1:3100 only
npm test
```
Backend env: WHATSAPP_BRIDGE_URL=http://127.0.0.1:3100  BRIDGE_SECRET=<same value>. Keep SESSION_ENC_KEY safe: without it saved sessions cannot be read.
