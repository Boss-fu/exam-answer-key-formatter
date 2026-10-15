'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const normalizeCode = code => typeof code === 'string' ? code.toUpperCase().replace(/[\s-]/g, '') : '';
const fail = (message, statusCode = 400) => { throw Object.assign(new Error(message), { statusCode }); };
function passwordHash(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  return { salt, hash: crypto.scryptSync(password, salt, 64).toString('hex') };
}
function verifyPassword(password, stored) {
  if (typeof password !== 'string' || !stored) return false;
  const actual = crypto.scryptSync(password, stored.salt, 64);
  const expected = Buffer.from(stored.hash, 'hex');
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}
function createAuth({ dir = path.join(__dirname, 'output', 'auth'), now = Date.now } = {}) {
  const file = path.join(dir, 'auth.json'), attempts = new Map();
  function read() {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
    catch (e) { if (e.code === 'ENOENT') return { admin: null, invites: [], sessions: [] }; throw e; }
  }
  function save(data) {
    fs.mkdirSync(dir, { recursive: true });
    const temp = file + '.' + crypto.randomBytes(8).toString('hex') + '.tmp';
    try { fs.writeFileSync(temp, JSON.stringify(data), { encoding: 'utf8', mode: 0o600 }); fs.renameSync(temp, file); }
    finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  }
  function limit(ip) {
    const time = now();
    for (const [key, values] of attempts) if (!values.some(t => time - t < 60000)) attempts.delete(key);
    const values = (attempts.get(ip) || []).filter(t => time - t < 60000);
    if (values.length >= 10) fail('登入嘗試太頻繁，請一分鐘後再試。', 429);
    values.push(time); attempts.set(ip, values);
  }
  function session(data, role, invite) {
    const token = crypto.randomBytes(32).toString('hex');
    data.sessions = data.sessions.filter(s => s.expiresAt > now());
    data.sessions.push({ hash: hash(token), role, inviteId: invite?.id, expiresAt: now() + 30 * 86400000 });
    if (invite) invite.lastUsedAt = new Date(now()).toISOString();
    save(data); return { token, role, ...(invite ? { label: invite.label } : {}) };
  }
  const publicInvite = ({ codeHash, ...invite }) => invite;
  return {
    setPassword(password) {
      if (typeof password !== 'string' || Array.from(password).length < 8) fail('密碼至少需要 8 個字元。');
      const data = read(); data.admin = passwordHash(password); data.sessions = data.sessions.filter(s => s.role !== 'admin'); save(data);
    },
    adminLogin(password, ip) {
      limit(ip); const data = read();
      if (!data.admin) fail('尚未設定管理員密碼，請在公司電腦執行 node local-app/set-admin-password.js', 401);
      if (!verifyPassword(password, data.admin)) fail('管理員密碼不正確。', 401);
      return session(data, 'admin');
    },
    inviteLogin(code, ip) {
      limit(ip); const data = read(), normalized = normalizeCode(code);
      const invite = data.invites.find(i => i.codeHash === hash(normalized));
      if (!invite) fail('邀請碼不正確，請確認後再試。', 401);
      if (invite.disabled) fail('此邀請碼已被管理員停用', 401);
      return session(data, 'teacher', invite);
    },
    authenticate(token) {
      if (typeof token !== 'string' || !/^[a-f0-9]{64}$/.test(token)) fail('請先登入詳解系統。', 401);
      const data = read(), s = data.sessions.find(s => s.hash === hash(token));
      if (!s || s.expiresAt <= now()) fail('登入已過期，請重新登入。', 401);
      if (s.role === 'admin') return { role: 'admin', label: '管理員' };
      const invite = data.invites.find(i => i.id === s.inviteId);
      if (!invite || invite.disabled || s.revoked) fail('此邀請碼已被管理員停用', 401);
      return { role: 'teacher', label: invite.label, inviteId: invite.id };
    },
    logout(token) { const data = read(); data.sessions = data.sessions.filter(s => s.hash !== hash(token || '')); save(data); },
    listInvites() { return read().invites.map(publicInvite); },
    createInvite(label) {
      if (typeof label !== 'string' || !label.trim() || Array.from(label.trim()).length > 30) fail('請填寫老師名稱，最多 30 個字。');
      const data = read(), alphabet = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'; let raw;
      do { raw = Array.from({ length: 8 }, () => alphabet[crypto.randomInt(alphabet.length)]).join(''); } while (data.invites.some(i => i.codeHash === hash(raw)));
      const invite = { id: crypto.randomUUID(), label: label.trim(), codeHash: hash(raw), last4: raw.slice(-4), createdAt: new Date(now()).toISOString(), disabled: false, lastUsedAt: null, jobs: 0, expiresAt: null, jobLimit: null };
      data.invites.push(invite); save(data); return { ...publicInvite(invite), code: raw.slice(0, 4) + '-' + raw.slice(4) };
    },
    deleteInvite(id) {
      const data = read();
      if (!data.invites.some(i => i.id === id)) fail('找不到這組邀請碼。', 404);
      data.invites = data.invites.filter(i => i.id !== id);
      data.sessions = data.sessions.filter(s => s.inviteId !== id);
      save(data); return { ok: true };
    },
    setDisabled(id, disabled) {
      const data = read(), invite = data.invites.find(i => i.id === id);
      if (!invite) fail('找不到這組邀請碼。', 404);
      invite.disabled = disabled;
      if (disabled) data.sessions.filter(s => s.inviteId === id).forEach(s => { s.revoked = true; });
      save(data); return publicInvite(invite);
    },
    recordJob(identity) {
      if (identity.role !== 'teacher') return;
      const data = read(), invite = data.invites.find(i => i.id === identity.inviteId);
      if (invite) { invite.jobs++; invite.lastUsedAt = new Date(now()).toISOString(); save(data); }
    }
  };
}
module.exports = { createAuth, normalizeCode, passwordHash, verifyPassword };
