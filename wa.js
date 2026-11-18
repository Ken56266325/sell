#!/usr/bin/env node
/**
 * wa.js — Serveur WhatsApp OTP Manager
 * Expose une API HTTP locale pour gérer des comptes WhatsApp et récupérer les OTP.
 * Aucun menu interactif : démarrage direct du serveur.
 */

import { createRequire } from 'node:module';
import path from 'node:path';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const {
  WhalibmobClient,
  createNewStore,
  loadStore,
  saveStore,
  requestSmsCode,
  verifyCode,
  storeFileFor
} = require('whalibmob');
const qrcode = require('qrcode-terminal');
const Database = require('better-sqlite3');
const fs = require('fs');

// ---------- Configuration ----------
const BASE_DIR = __dirname;
const SESSION_DIR = path.join(BASE_DIR, 'accounts');
const DB_FILE = path.join(BASE_DIR, 'messages.db');
const OTP_REGEX = /\b\d{3}[-\s]?\d{3}\b|\b\d{4,8}\b/g;
const PORT = process.env.WA_PORT || 3000;

if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });

// ---------- Base de données ----------
const db = new Database(DB_FILE);
db.exec(`
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT,
    msg_id TEXT,
    timestamp INTEGER,
    chat_jid TEXT,
    sender_jid TEXT,
    push_name TEXT,
    from_me INTEGER,
    text TEXT,
    otp TEXT,
    UNIQUE(account, msg_id)
  );
  CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    jid TEXT,
    created_at INTEGER,
    mode TEXT NOT NULL DEFAULT 'web',
    phone TEXT
  );
`);
try { db.exec("ALTER TABLE accounts ADD COLUMN mode TEXT NOT NULL DEFAULT 'web'"); } catch {}
try { db.exec('ALTER TABLE accounts ADD COLUMN phone TEXT'); } catch {}

// ---------- Gestion multi-comptes ----------
const activeClients = new Map(); // name -> WhalibmobClient
const pendingPrimaryRegistrations = new Map(); // name -> { store, file, phone, method, displayName }
const seenIncomingIds = new Set();
const MAX_SEEN_INCOMING_IDS = 5000;

// Buffer d'OTP par compte
const otpBuffer = new Map(); // accountName -> string[]

function pushOtp(accountName, otp) {
  if (!otpBuffer.has(accountName)) otpBuffer.set(accountName, []);
  const list = otpBuffer.get(accountName);
  list.push(otp);
  if (list.length > 50) list.shift(); // garder les 50 derniers
}

function drainOtps(accountName) {
  const list = otpBuffer.get(accountName) || [];
  otpBuffer.set(accountName, []);
  return list;
}

function loadAccounts() {
  return db.prepare('SELECT * FROM accounts').all();
}

function saveAccount(name, jid, mode = 'web', phone = '') {
  db.prepare('INSERT OR REPLACE INTO accounts (name, jid, created_at, mode, phone) VALUES (?, ?, ?, ?, ?)')
    .run(name, jid, Date.now(), mode, phone || null);
}

function phoneForAccount(account) {
  return String(account.phone || account.jid || '').split('@')[0].split(':')[0].replace(/\D/g, '');
}

// ---------- Récupération des messages protobuf inconnus ----------
function readProtoVarint(buffer, start) {
  let value = 0;
  let shift = 0;
  for (let index = start; index < buffer.length && index < start + 10; index++) {
    const byte = buffer[index];
    value += (byte & 0x7f) * (2 ** shift);
    if ((byte & 0x80) === 0) return { value, next: index + 1 };
    shift += 7;
  }
  return null;
}

function printableUtf8(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 3 || buffer.length > 8192) return '';
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(buffer).trim(); } catch { return ''; }
  if (text.length < 3 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) return '';
  const printable = [...text].filter(char => char >= ' ' || char === '\n' || char === '\t').length;
  return printable / [...text].length >= 0.95 ? text : '';
}

function collectProtoStrings(buffer, depth = 0, results = []) {
  if (!Buffer.isBuffer(buffer) || depth > 6) return results;
  let offset = 0;
  while (offset < buffer.length) {
    const key = readProtoVarint(buffer, offset);
    if (!key || key.value === 0) break;
    offset = key.next;
    const wireType = key.value % 8;
    if (wireType === 0) {
      const value = readProtoVarint(buffer, offset);
      if (!value) break;
      offset = value.next;
      continue;
    }
    if (wireType === 1) { offset += 8; continue; }
    if (wireType === 5) { offset += 4; continue; }
    if (wireType !== 2) break;
    const size = readProtoVarint(buffer, offset);
    if (!size || !Number.isSafeInteger(size.value) || size.value < 0 || size.value > buffer.length - size.next) break;
    const value = buffer.subarray(size.next, size.next + size.value);
    offset = size.next + size.value;
    const text = printableUtf8(value);
    if (text) results.push(text);
    if (value.length) collectProtoStrings(value, depth + 1, results);
  }
  return results;
}

function recoverUnknownMessageText(payload) {
  const fallbackRuns = Buffer.isBuffer(payload)
    ? (payload.toString('utf8').match(/[\p{L}\p{N}][\p{L}\p{N}\p{P}\p{Z}\p{S}]{2,}/gu) || [])
    : [];
  const strings = [...new Set([...collectProtoStrings(payload), ...fallbackRuns])]
    .filter(text => !/^https?:\/\//i.test(text));
  if (!strings.length) return '';
  return strings.find(text => extractOtp(text)) || strings.sort((a, b) => b.length - a.length)[0];
}

function installUnknownMessageRecovery(client) {
  const originalDecode = client._decodeMsg;
  if (typeof originalDecode !== 'function' || client.__otpUnknownRecoveryInstalled) return;
  client.__otpUnknownRecoveryInstalled = true;
  client._decodeMsg = function patchedDecodeMessage(plaintext) {
    const decoded = originalDecode.call(this, plaintext);
    if (decoded?.type === 'unknown' && Buffer.isBuffer(plaintext)) {
      const recoveredText = recoverUnknownMessageText(plaintext);
      if (recoveredText) decoded.recoveredText = recoveredText;
      Object.defineProperty(decoded, '_unknownPayload', { value: Buffer.from(plaintext), enumerable: false });
    }
    return decoded;
  };
}

function clientOptions(sessionDir) {
  const options = { sessionDir };
  if (process.env.WA_OTP_DEBUG === '1') options.pino = true;
  return options;
}

// ---------- Extraction du texte ----------
function extractMessageText(msg) {
  const decoded = msg.decoded;
  if (typeof decoded?.recoveredText === 'string' && decoded.recoveredText.trim()) return decoded.recoveredText;
  if (decoded?.type === 'text' && typeof decoded.text === 'string' && decoded.text.trim()) return decoded.text;
  for (const field of ['caption', 'body', 'description', 'title', 'question', 'displayName']) {
    if (typeof decoded?.[field] === 'string' && decoded[field].trim()) return decoded[field];
  }
  if (msg.text && typeof msg.text === 'string' && msg.text.trim()) return msg.text;
  if (msg.body && typeof msg.body === 'string' && msg.body.trim()) return msg.body;
  const nodeContent = msg.node?.content;
  if (typeof nodeContent === 'string' && nodeContent.trim()) return nodeContent;
  if (Buffer.isBuffer(nodeContent)) {
    const text = nodeContent.toString('utf8').trim();
    if (text) return text;
  }
  const candidates = [msg.message, msg.content, msg.caption];
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) return c;
  }
  return '';
}

function extractOtp(text) {
  const matches = text.match(OTP_REGEX);
  return matches ? [...new Set(matches)].join(',') : '';
}

// ---------- Sauvegarde d'un message ----------
function saveMessage(account, msg) {
  try {
    const text = extractMessageText(msg);
    if (!text) return null;
    const otp = extractOtp(text);
    const rawTimestamp = msg.ts ?? msg.timestamp;
    const ts = rawTimestamp
      ? (rawTimestamp > 1e11 ? Math.floor(rawTimestamp / 1000) : rawTimestamp)
      : Math.floor(Date.now() / 1000);
    db.prepare(`
      INSERT OR REPLACE INTO messages
        (account, msg_id, timestamp, chat_jid, sender_jid, push_name, from_me, text, otp)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      account, msg.id || '', ts,
      msg.chat || msg.from || '',
      msg.participant || msg.sender || msg.author || msg.from || '',
      msg.pushName || '',
      msg.fromMe ? 1 : 0,
      text, otp
    );
    return { text, otp };
  } catch (err) {
    console.error('Erreur sauvegarde message:', err.message);
    return null;
  }
}

function handleIncomingMessage(account, msg) {
  const eventKey = msg.id ? `${account}:${msg.id}` : '';
  if (eventKey && seenIncomingIds.has(eventKey)) return;
  if (eventKey) {
    seenIncomingIds.add(eventKey);
    if (seenIncomingIds.size > MAX_SEEN_INCOMING_IDS) seenIncomingIds.delete(seenIncomingIds.values().next().value);
  }
  const saved = saveMessage(account, msg);
  if (!saved) {
    console.log(`[${account}] message non déchiffré (${msg.decoded?.type || 'unknown'})`);
    return;
  }
  console.log(`[${account}] ${msg.pushName || msg.participant || 'contact'} : ${saved.text}`);
  if (saved.otp) {
    const codes = saved.otp.split(',');
    codes.forEach(code => pushOtp(account, code));
    console.log(`   🔑 OTP(s) détecté(s) : ${saved.otp}`);
  }
}

// ---------- Connexion d'un compte (compagnon) ----------
function connectAccount(name, phoneNumber, linkMode = 'pairing') {
  if (activeClients.has(name)) {
    console.log(`⚠️  Le compte "${name}" est déjà connecté.`);
    return activeClients.get(name);
  }

  const savedAccount = db.prepare('SELECT phone, jid FROM accounts WHERE name = ?').get(name);
  const needsPairingCode = linkMode === 'pairing' && !savedAccount;
  const resolvedPhone = String(
    phoneNumber || savedAccount?.phone || String(savedAccount?.jid || '').split('@')[0].split(':')[0]
  ).replace(/\D/g, '');
  if (!resolvedPhone) {
    console.error(`Le numéro du compte "${name}" est requis pour la connexion Web.`);
    return null;
  }

  const sessionDir = path.join(SESSION_DIR, name);
  if (!fs.existsSync(sessionDir)) fs.mkdirSync(sessionDir, { recursive: true });

  console.log(`\n🔌 Connexion du compte "${name}"...`);

  const client = new WhalibmobClient(clientOptions(sessionDir));
  installUnknownMessageRecovery(client);

  client.on('qr', ({ qr, remaining }) => {
    console.log(`\n📱 Scannez ce QR code avec WhatsApp (${remaining}s restantes) :`);
    qrcode.generate(qr, { small: true });
    console.log('   WhatsApp → Appareils connectés → Lier un appareil\n');
  });

  client.on('qr_timeout', () => {
    console.log('⏰ Le QR code a expiré. Reconnexion pour en obtenir un nouveau...');
  });

  client.on('paired', (payload) => {
    const jid = payload.jid || payload.user || '';
    console.log(`🔗 Appareil appairé : ${jid}`);
    saveAccount(name, jid, 'web', resolvedPhone);
  });

  client.on('connected', () => {
    console.log(`✅ Compte "${name}" connecté et prêt à recevoir des messages.`);
  });

  const isLoggedOut = (err) => Boolean(
    err?.loggedOut ||
    String(err?.code || '') === '401' ||
    Number(err?.output?.statusCode) === 401
  );

  client.on('auth_failure', (info) => {
    if (info?.loggedOut || String(info?.reason || '') === '401') {
      console.error(`Session de "${name}" invalidée par WhatsApp (401).`);
      activeClients.delete(name);
      try { client.disconnect(); } catch {}
    }
  });

  client.on('error', (err) => {
    if (isLoggedOut(err)) {
      console.error(`WhatsApp a désappairé "${name}" (401) : ${err?.message || err}`);
      activeClients.delete(name);
      try { client.disconnect(); } catch {}
      return;
    }
    console.error(`Erreur du compte "${name}":`, err?.message || err);
  });

  client.on('disconnected', () => {
    console.log(`🔌 Compte "${name}" déconnecté.`);
  });

  client.on('reconnecting', ({ attempt, delay }) => {
    console.log(`🔄 Reconnexion de "${name}" (tentative ${attempt}, dans ${delay}ms)...`);
  });

  client.on('message', (msg) => handleIncomingMessage(name, msg));

  (async () => {
    try {
      if (needsPairingCode) {
        await client.connectWeb(resolvedPhone, { syncFullHistory: true });
        const code = await client.requestPairingCode(resolvedPhone);
        console.log(`\n🔑 Code d'appairage pour "${name}" : ${code}`);
        console.log('   WhatsApp → Appareils connectés → Lier avec un numéro de téléphone\n');
      } else {
        await client.connectWeb(resolvedPhone, { syncFullHistory: true });
      }
    } catch (err) {
      console.error(`❌ Erreur de connexion pour "${name}":`, err.message);
    }
  })();

  activeClients.set(name, client);
  return client;
}

// ---------- Enregistrement primaire ----------
async function registerPrimaryAccount(name, phoneNumber, method = 'sms', displayName = name) {
  if (activeClients.has(name)) {
    console.log(`Compte "${name}" déjà connecté.`);
    return activeClients.get(name);
  }

  const sessionDir = path.join(SESSION_DIR, name);
  fs.mkdirSync(sessionDir, { recursive: true });
  const initialFile = storeFileFor(sessionDir, phoneNumber);
  let store = loadStore(initialFile);
  if (!store) {
    store = createNewStore(phoneNumber, { name: displayName });
    saveStore(store, initialFile);
  }

  if (store.registered) {
    const savedPhone = String(store.phoneNumber || phoneNumber).replace(/\D/g, '');
    saveAccount(name, `${savedPhone}@s.whatsapp.net`, 'primary', savedPhone);
    console.log(`Le compte principal "${name}" est déjà enregistré ; reconnexion de la session sauvegardée.`);
    return connectPrimaryAccount(name, savedPhone);
  }

  console.log(`\nEnregistrement principal du numéro +${phoneNumber}...`);
  console.log('Un code de vérification va être envoyé par WhatsApp.');
  await requestSmsCode(store, method, { name: displayName });
  saveStore(store, initialFile);

  const code = (await new Promise(resolve => {
    const rl = require('readline').createInterface({ input: process.stdin, output: process.stdout });
    rl.question('Code reçu (6 chiffres) : ', answer => { rl.close(); resolve(answer); });
  })).replace(/\D/g, '');
  if (!/^\d{6}$/.test(code)) throw new Error('Le code doit contenir 6 chiffres.');

  const result = await verifyCode(store, code, { name: displayName });
  if (!['ok', 'sent', 'verified'].includes(result.status)) {
    throw new Error(`Échec de l'enregistrement : ${JSON.stringify(result)}`);
  }

  store.registered = true;
  store.codePending = false;
  const canonicalPhone = String(store.phoneNumber || phoneNumber).replace(/\D/g, '');
  saveStore(store, storeFileFor(sessionDir, canonicalPhone));
  saveAccount(name, `${canonicalPhone}@s.whatsapp.net`, 'primary', canonicalPhone);
  console.log(`Numéro enregistré comme compte principal : +${canonicalPhone}`);

  return connectPrimaryAccount(name, canonicalPhone);
}

function cleanPhoneNumber(value) {
  return String(value || '').replace(/\D/g, '');
}

async function startPrimaryRegistration(name, phoneNumber, method = 'wa_old', displayName = name) {
  const accountName = String(name || '').trim();
  const phone = cleanPhoneNumber(phoneNumber);
  if (!accountName) throw new Error('Nom de compte requis.');
  if (!/^\d{7,15}$/.test(phone)) throw new Error('Numéro international invalide.');
  const sessionDir = path.join(SESSION_DIR, accountName);
  fs.mkdirSync(sessionDir, { recursive: true });
  const initialFile = storeFileFor(sessionDir, phone);
  let store = loadStore(initialFile);
  if (!store) {
    store = createNewStore(phone, { name: displayName || accountName });
    saveStore(store, initialFile);
  }
  if (store.registered) {
    const canonicalPhone = cleanPhoneNumber(store.phoneNumber || phone);
    saveAccount(accountName, `${canonicalPhone}@s.whatsapp.net`, 'primary', canonicalPhone);
    connectPrimaryAccount(accountName, canonicalPhone);
    return { success: true, registered: true, account: accountName, phone: canonicalPhone };
  }
  // Ce pont Python est volontairement dédié au flux historique wa_old.
  // Aucun choix SMS/voice n'est exposé ni accepté par cette route.
  const selectedMethod = 'wa_old';
  await requestSmsCode(store, selectedMethod, { name: displayName || accountName });
  saveStore(store, initialFile);
  pendingPrimaryRegistrations.set(accountName, {
    store, initialFile, phone, method: selectedMethod, displayName: displayName || accountName
  });
  return { success: true, pending: true, account: accountName, phone, method: selectedMethod };
}

async function verifyPrimaryRegistration(name, code) {
  const accountName = String(name || '').trim();
  const pending = pendingPrimaryRegistrations.get(accountName);
  if (!pending) throw new Error('Aucune inscription WhatsApp en attente pour ce compte.');
  const normalizedCode = String(code || '').replace(/\D/g, '');
  if (!/^\d{6}$/.test(normalizedCode)) {
    return { success: false, retryable: true, error: 'Le code doit contenir 6 chiffres.' };
  }
  const result = await verifyCode(pending.store, normalizedCode, { name: pending.displayName });
  if (!['ok', 'sent', 'verified'].includes(result?.status)) {
    return {
      success: false, retryable: true,
      error: `Code WhatsApp refusé : ${JSON.stringify(result)}`,
      result
    };
  }
  pending.store.registered = true;
  pending.store.codePending = false;
  const canonicalPhone = cleanPhoneNumber(pending.store.phoneNumber || pending.phone);
  saveStore(pending.store, storeFileFor(path.dirname(pending.initialFile), canonicalPhone));
  saveAccount(accountName, `${canonicalPhone}@s.whatsapp.net`, 'primary', canonicalPhone);
  pendingPrimaryRegistrations.delete(accountName);
  connectPrimaryAccount(accountName, canonicalPhone);
  return { success: true, registered: true, account: accountName, phone: canonicalPhone, result };
}

function connectPrimaryAccount(name, phoneNumber) {
  if (activeClients.has(name)) return activeClients.get(name);
  const sessionDir = path.join(SESSION_DIR, name);
  const client = new WhalibmobClient(clientOptions(sessionDir));
  installUnknownMessageRecovery(client);

  client.on('connected', () => {
    console.log(`Compte principal "${name}" connecté.`);
    console.log(`Écoute temps réel active pour "${name}" (événements message).`);
  });
  client.on('message', (msg) => handleIncomingMessage(name, msg));
  client.on('decrypt_error', ({ id, from, err }) => {
    console.error(`Message non déchiffré pour "${name}" (id ${id}, ${from || 'inconnu'}) : ${err?.message || err}`);
  });
  client.on('stream_error', ({ reason }) => {
    console.error(`Flux WhatsApp en erreur pour "${name}" : ${reason}`);
  });
  client.on('auth_failure', ({ reason }) => {
    console.error(`Session principale "${name}" invalide : ${reason}`);
    activeClients.delete(name);
  });
  client.on('error', (err) => console.error(`Erreur du compte "${name}" :`, err.message));
  client.on('primary_message_store_error', ({ code, err }) =>
    console.error(`Stockage local des messages de "${name}" indisponible (${code}) : ${err?.message || ''}`));
  client.on('keepalive_timeout', ({ errorCount }) => {
    if (errorCount >= 3) client.disconnect({ reconnect: true });
  });
  client.on('disconnected', () => console.log(`Compte "${name}" déconnecté.`));
  client.on('reconnecting', ({ attempt, delay }) =>
    console.log(`Reconnexion de "${name}" (tentative ${attempt}, dans ${delay}ms)...`));

  activeClients.set(name, client);
  client.init(phoneNumber).catch((err) => {
    activeClients.delete(name);
    console.error(`Impossible de connecter "${name}" :`, err.message);
  });
  return client;
}

function connectSavedAccount(account) {
  const phone = phoneForAccount(account);
  if (!phone) throw new Error(`Le numéro du compte "${account.name}" est introuvable.`);
  if (account.mode === 'primary') return connectPrimaryAccount(account.name, phone);
  return connectAccount(account.name, phone);
}

// ===========================================================================
// SERVEUR HTTP NATIF (aucune dépendance Express)
// ===========================================================================
function sendJson(res, statusCode, value) {
  const body = JSON.stringify(value);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let length = 0;
    req.on('data', chunk => {
      length += chunk.length;
      if (length > 1024 * 1024) {
        reject(new Error('Corps JSON trop volumineux.'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(new Error('JSON invalide.')); }
    });
    req.on('error', reject);
  });
}

function routeAccountName(pathname, prefix) {
  if (!pathname.startsWith(`${prefix}/`)) return null;
  const value = pathname.slice(prefix.length + 1);
  return value ? decodeURIComponent(value) : null;
}

const server = createServer(async (req, res) => {
  const parsed = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  const pathname = parsed.pathname;
  // Les appels de l'API sont internes au flux automatique. Ils restent
  // activables pour le diagnostic avec WA_API_LOGS=1, mais sont silencieux
  // par défaut dans la console utilisateur.
  if (process.env.WA_API_LOGS === '1') {
    console.log(`[API] ${req.method} ${pathname}`);
  }
  try {
    if (req.method === 'GET' && pathname === '/health') {
      return sendJson(res, 200, {
        status: 'ok', uptime: process.uptime(), accounts: loadAccounts().length,
        activeConnections: activeClients.size
      });
    }
    if (req.method === 'GET' && pathname === '/accounts') {
      return sendJson(res, 200, loadAccounts().map(acc => ({
        name: acc.name, phone: phoneForAccount(acc), mode: acc.mode,
        connected: activeClients.has(acc.name), jid: acc.jid || ''
      })));
    }
    if (req.method === 'POST' && pathname === '/register-account/start') {
      const body = await readJsonBody(req);
      const result = await startPrimaryRegistration(
        body.name, body.phone, body.method || 'wa_old', body.displayName || body.name
      );
      return sendJson(res, 200, result);
    }
    if (req.method === 'POST' && pathname === '/register-account/verify') {
      const body = await readJsonBody(req);
      const result = await verifyPrimaryRegistration(body.name, body.code);
      return sendJson(res, result.success ? 200 : 422, result);
    }
    if (req.method === 'POST' && pathname === '/add-account') {
      const { phone } = await readJsonBody(req);
      if (!phone) return sendJson(res, 400, { error: 'Numéro requis (champ "phone").' });
      const cleanPhone = String(phone).replace(/\D/g, '');
      if (!cleanPhone) return sendJson(res, 400, { error: 'Numéro invalide.' });
      const existing = db.prepare(
        'SELECT * FROM accounts WHERE phone = ? OR jid LIKE ?'
      ).get(cleanPhone, `${cleanPhone}@%`);
      if (!existing) return sendJson(res, 404, {
        error: `Aucun compte WhatsApp enregistré avec le numéro +${cleanPhone}. Ajoutez-le d'abord via le CLI de whalibmob ou via wa.js.`
      });
      return sendJson(res, 200, {
        success: true, account: existing.name,
        phone: existing.phone || cleanPhone, mode: existing.mode
      });
    }
    const listenName = routeAccountName(pathname, '/listen');
    if (req.method === 'POST' && listenName !== null) {
      const account = db.prepare('SELECT * FROM accounts WHERE name = ?').get(listenName);
      if (!account) return sendJson(res, 404, { error: `Compte "${listenName}" inconnu.` });
      try { connectSavedAccount(account); return sendJson(res, 200, { success: true, account: listenName }); }
      catch (err) { return sendJson(res, 500, { error: err.message }); }
    }
    const otpName = routeAccountName(pathname, '/otp');
    if (req.method === 'GET' && otpName !== null) {
      const account = db.prepare('SELECT * FROM accounts WHERE name = ?').get(otpName);
      if (!account) return sendJson(res, 404, { error: `Compte "${otpName}" inconnu.` });
      return sendJson(res, 200, { otps: drainOtps(otpName), account: otpName });
    }
    const deleteName = routeAccountName(pathname, '/accounts');
    if (req.method === 'DELETE' && deleteName !== null) {
      const account = db.prepare('SELECT * FROM accounts WHERE name = ?').get(deleteName);
      if (!account) return sendJson(res, 404, { error: `Compte "${deleteName}" inconnu.` });
      if (activeClients.has(deleteName)) {
        try { activeClients.get(deleteName).disconnect?.(); } catch {}
        activeClients.delete(deleteName);
      }
      db.prepare('DELETE FROM accounts WHERE name = ?').run(deleteName);
      const dir = path.join(SESSION_DIR, deleteName);
      if (fs.existsSync(dir)) { try { fs.rmSync(dir, { recursive: true }); } catch {} }
      return sendJson(res, 200, { success: true, account: deleteName });
    }
    const messageName = routeAccountName(pathname, '/messages');
    if (req.method === 'GET' && messageName !== null) {
      const limit = Math.min(parseInt(parsed.searchParams.get('limit'), 10) || 20, 100);
      const rows = db.prepare(
        'SELECT * FROM messages WHERE account = ? ORDER BY timestamp DESC LIMIT ?'
      ).all(messageName, limit);
      return sendJson(res, 200, rows.map(row => ({
        timestamp: row.timestamp, sender: row.sender_jid, pushName: row.push_name,
        text: row.text, otp: row.otp, fromMe: Boolean(row.from_me)
      })));
    }
    return sendJson(res, 404, { error: 'Route introuvable.' });
  } catch (err) {
    return sendJson(res, err.message === 'JSON invalide.' ? 400 : 500, { error: err.message });
  }
});

// --- Démarrage ---
server.listen(PORT, () => {
  console.log(`✅ Serveur WhatsApp OTP démarré sur http://localhost:${PORT}`);
  console.log(`   Comptes enregistrés : ${loadAccounts().length}`);
  console.log(`   Sessions actives    : ${activeClients.size}`);
  console.log(`   En attente de requêtes...`);
});

// Gestion propre de l'arrêt
process.on('SIGINT', () => {
  console.log('\n🛑 Arrêt du serveur...');
  for (const [name, client] of activeClients) {
    try { client.disconnect?.(); } catch {}
    console.log(`   Déconnecté : ${name}`);
  }
  server.close(() => {
    db.close();
    process.exit(0);
  });
});

process.on('SIGTERM', () => {
  console.log('\n🛑 Arrêt du serveur (SIGTERM)...');
  for (const [name, client] of activeClients) {
    try { client.disconnect?.(); } catch {}
  }
  server.close(() => {
    db.close();
    process.exit(0);
  });
});
