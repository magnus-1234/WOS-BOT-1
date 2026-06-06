import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'crypto';
import path from 'path';
import express, { ErrorRequestHandler, Request, Response } from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import multer from 'multer';
import { MongoClient, ObjectId, type Db, type Sort } from 'mongodb';
import { DeleteObjectCommand, PutObjectCommand, S3Client } from '@aws-sdk/client-s3';

dotenv.config({ path: path.resolve(process.cwd(), '.env') });
dotenv.config({ path: path.resolve(process.cwd(), '..', '.env') });

type IslandDocument = {
  _id?: ObjectId;
  title: string;
  creatorName: string;
  creatorUserId?: ObjectId;
  description: string;
  playerId: string;
  coordinates: {
    x: number;
    y: number;
  };
  player: PlayerProfile;
  server?: string;
  alliance?: string;
  tags: string[];
  imageUrl: string;
  objectKey: string;
  likes: number;
  shares: number;
  commentsCount: number;
  createdAt: Date;
  updatedAt: Date;
};

type PlayerProfile = {
  playerId: string;
  nickname: string;
  stateId?: string;
  furnaceLevel?: number;
  furnaceLevelFormatted?: string;
  furnaceIcon?: string;
  avatarImage?: string;
};

type IslandLikeDocument = {
  islandId: ObjectId;
  viewerId: string;
  createdAt: Date;
};

type IslandCommentDocument = {
  _id?: ObjectId;
  islandId: ObjectId;
  authorName: string;
  message: string;
  createdAt: Date;
};

type MessageTemplateCategory = 'unicodes' | 'emojis' | 'funny' | 'alliance-recruit';

type MessageTemplateDocument = {
  _id?: ObjectId;
  title: string;
  description?: string;
  text: string;
  previewText?: string;
  imageUrl?: string;
  imageObjectKey?: string;
  category: MessageTemplateCategory;
  tags: string[];
  creatorName: string;
  creatorUserId: ObjectId;
  likes: number;
  shares: number;
  createdAt: Date;
  updatedAt: Date;
};

type TemplateLikeDocument = {
  templateId: ObjectId;
  viewerId: string;
  createdAt: Date;
};

type AuthProvider = 'google' | 'discord';

type LinkedPlayerAccount = PlayerProfile & {
  linkedAt: Date;
};

type UserDocument = {
  _id?: ObjectId;
  email?: string;
  displayName: string;
  avatarUrl?: string;
  providers: {
    provider: AuthProvider;
    providerUserId: string;
    email?: string;
    linkedAt: Date;
  }[];
  playerAccounts: LinkedPlayerAccount[];
  createdAt: Date;
  updatedAt: Date;
};

type SessionDocument = {
  _id?: ObjectId;
  sessionHash: string;
  userId: ObjectId;
  createdAt: Date;
  expiresAt: Date;
};

type OAuthStateDocument = {
  _id?: ObjectId;
  stateHash: string;
  provider: AuthProvider;
  returnTo: string;
  createdAt: Date;
  expiresAt: Date;
};

type SiteVisitDocument = {
  _id?: ObjectId;
  id: string;
  visitorId: string;
  ip: string;
  country: string;
  region: string;
  city: string;
  browser: string;
  os: string;
  device: string;
  page: string;
  referrer: string;
  userAgent: string;
  language: string;
  timezone: string;
  screen: string;
  viewport: string;
  timestamp: Date;
  createdAt: Date;
};

type AdminSessionDocument = {
  _id?: ObjectId;
  tokenHash: string;
  createdAt: Date;
  expiresAt: Date;
  ip: string;
  userAgent: string;
};

type OAuthProfile = {
  provider: AuthProvider;
  providerUserId: string;
  email?: string;
  displayName: string;
  avatarUrl?: string;
};

type GiftCodeSource = 'wostools' | 'wosgiftcodes' | 'bot_dashboard';

type GiftCodeItem = {
  code: string;
  rewards: string;
  expiry: string;
  description: string;
  dateAdded?: string;
  status: 'active';
  isActive: boolean;
};

type RawGiftCode = Record<string, unknown>;

type GiftCodeInput = {
  rewards?: unknown;
  expiry?: unknown;
  description?: unknown;
  dateAdded?: unknown;
};

type UploadedRequest = Request & {
  file?: Express.Multer.File;
};

const app = express();
const port = process.env.DAYBREAK_PORT || process.env.PORT || 3001;
const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: Number(process.env.DAYBREAK_MAX_UPLOAD_BYTES || 8 * 1024 * 1024),
  },
  fileFilter: (_req, file, callback) => {
    if (!file.mimetype.startsWith('image/')) {
      callback(new Error('Only image uploads are supported'));
      return;
    }

    callback(null, true);
  },
});

const maxUploadBytes = Number(process.env.DAYBREAK_MAX_UPLOAD_BYTES || 8 * 1024 * 1024);

let mongoClient: MongoClient | null = null;
let mongoDb: Db | null = null;
let indexesReady: Promise<void> | null = null;
const playerCache = new Map<string, { expiresAt: number; profile: PlayerProfile }>();
const playerCacheTtlMs = Number(process.env.DAYBREAK_PLAYER_CACHE_TTL_MS || 10 * 60 * 1000);

const required = (name: string) => {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }

  return value;
};

const normalizePublicUrl = (value: string) => value.replace(/\/+$/, '');

const slugify = (value: string) =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')
    .slice(0, 72) || 'island';

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const parseTags = (value: unknown) => {
  if (typeof value !== 'string') {
    return [];
  }

  const seen = new Set<string>();
  return (value.match(/#[\p{L}\p{N}_-]+|[\p{L}\p{N}][\p{L}\p{N}_-]*/gu) || [])
    .map((tag) => tag.replace(/^#/, '').trim())
    .filter((tag) => {
      const key = tag.toLowerCase();
      if (!key || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .slice(0, 8);
};

const cleanText = (value: unknown, maxLength: number) => {
  if (typeof value !== 'string') {
    return '';
  }

  return value.trim().replace(/\s+/g, ' ').slice(0, maxLength);
};

const cleanPlayerId = (value: unknown) => String(value || '').replace(/\D/g, '').slice(0, 16);

const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

const appUrl = normalizePublicUrl(process.env.PUBLIC_APP_URL || process.env.FRONTEND_URL || 'http://localhost:3000');
const apiUrl = normalizePublicUrl(process.env.PUBLIC_API_URL || process.env.BACKEND_PUBLIC_URL || `http://localhost:${port}`);
const authCookieName = process.env.AUTH_COOKIE_NAME || 'wos_session';
const authCookieSecure = process.env.AUTH_COOKIE_SECURE === 'true' || apiUrl.startsWith('https://');
const authCookieSameSite = (process.env.AUTH_COOKIE_SAMESITE || (authCookieSecure ? 'None' : 'Lax')) as 'Lax' | 'Strict' | 'None';
const authCookieMaxAgeMs = Number(process.env.AUTH_SESSION_TTL_MS || 30 * 24 * 60 * 60 * 1000);
const adminCookieName = process.env.ADMIN_COOKIE_NAME || 'wos_admin_session';
const adminCookieSecure = process.env.ADMIN_COOKIE_SECURE === 'true' || apiUrl.startsWith('https://');
const adminCookieSameSite = (process.env.ADMIN_COOKIE_SAMESITE || (adminCookieSecure ? 'None' : 'Lax')) as 'Lax' | 'Strict' | 'None';
const adminSessionTtlMs = Number(process.env.ADMIN_SESSION_TTL_MS || 12 * 60 * 60 * 1000);

const oauthConfig = {
  google: {
    clientId: process.env.GOOGLE_CLIENT_ID || '',
    clientSecret: process.env.GOOGLE_CLIENT_SECRET || '',
    authorizeUrl: 'https://accounts.google.com/o/oauth2/v2/auth',
    tokenUrl: 'https://oauth2.googleapis.com/token',
    userInfoUrl: 'https://www.googleapis.com/oauth2/v2/userinfo',
    scope: 'openid email profile',
  },
  discord: {
    clientId: process.env.DISCORD_CLIENT_ID || '',
    clientSecret: process.env.DISCORD_CLIENT_SECRET || '',
    authorizeUrl: 'https://discord.com/oauth2/authorize',
    tokenUrl: 'https://discord.com/api/oauth2/token',
    userInfoUrl: 'https://discord.com/api/users/@me',
    scope: 'identify email',
  },
} satisfies Record<AuthProvider, {
  clientId: string;
  clientSecret: string;
  authorizeUrl: string;
  tokenUrl: string;
  userInfoUrl: string;
  scope: string;
}>;

const isProviderConfigured = (provider: AuthProvider) =>
  provider === 'discord'
    ? Boolean(oauthConfig.discord.clientId && (oauthConfig.discord.clientSecret || process.env.DISCORD_TOKEN_EXCHANGE_URL))
    : Boolean(oauthConfig[provider].clientId && oauthConfig[provider].clientSecret);

const getRedirectUri = (provider: AuthProvider) => `${apiUrl}/api/auth/${provider}/callback`;

const parseCookies = (header: string | undefined) =>
  Object.fromEntries(
    (header || '')
      .split(';')
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const separator = part.indexOf('=');
        if (separator === -1) {
          return [part, ''];
        }

        return [part.slice(0, separator), decodeURIComponent(part.slice(separator + 1))];
      }),
  );

const setSessionCookie = (res: Response, sessionToken: string) => {
  const parts = [
    `${authCookieName}=${encodeURIComponent(sessionToken)}`,
    'Path=/',
    'HttpOnly',
    `Max-Age=${Math.floor(authCookieMaxAgeMs / 1000)}`,
    `SameSite=${authCookieSameSite}`,
  ];

  if (authCookieSecure) {
    parts.push('Secure');
  }
  if (process.env.AUTH_COOKIE_DOMAIN) {
    parts.push(`Domain=${process.env.AUTH_COOKIE_DOMAIN}`);
  }

  res.setHeader('Set-Cookie', parts.join('; '));
};

const clearSessionCookie = (res: Response) => {
  const parts = [
    `${authCookieName}=`,
    'Path=/',
    'HttpOnly',
    'Max-Age=0',
    `SameSite=${authCookieSameSite}`,
  ];

  if (authCookieSecure) {
    parts.push('Secure');
  }
  if (process.env.AUTH_COOKIE_DOMAIN) {
    parts.push(`Domain=${process.env.AUTH_COOKIE_DOMAIN}`);
  }

  res.setHeader('Set-Cookie', parts.join('; '));
};

const setAdminCookie = (res: Response, sessionToken: string, expiresAt: Date) => {
  const parts = [
    `${adminCookieName}=${encodeURIComponent(sessionToken)}`,
    'Path=/',
    'HttpOnly',
    `Max-Age=${Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000))}`,
    `SameSite=${adminCookieSameSite}`,
  ];

  if (adminCookieSecure) {
    parts.push('Secure');
  }
  if (process.env.ADMIN_COOKIE_DOMAIN) {
    parts.push(`Domain=${process.env.ADMIN_COOKIE_DOMAIN}`);
  }

  res.setHeader('Set-Cookie', parts.join('; '));
};

const clearAdminCookie = (res: Response) => {
  const parts = [
    `${adminCookieName}=`,
    'Path=/',
    'HttpOnly',
    'Max-Age=0',
    `SameSite=${adminCookieSameSite}`,
  ];

  if (adminCookieSecure) {
    parts.push('Secure');
  }
  if (process.env.ADMIN_COOKIE_DOMAIN) {
    parts.push(`Domain=${process.env.ADMIN_COOKIE_DOMAIN}`);
  }

  res.setHeader('Set-Cookie', parts.join('; '));
};

const adminSecret = () => process.env.ADMIN_PASSWORD || process.env.ADMIN_ACCESS_TOKEN || '';

const isAdminConfigured = () => Boolean(adminSecret());

const verifyAdminSecret = (value: unknown) => {
  const secret = adminSecret();
  const provided = typeof value === 'string' ? value : '';
  if (!secret || !provided) {
    return false;
  }

  const expected = Buffer.from(sha256(secret), 'hex');
  const actual = Buffer.from(sha256(provided), 'hex');
  return expected.length === actual.length && timingSafeEqual(expected, actual);
};

const getRequestIp = (req: Request) => {
  const forwarded = String(req.headers['x-forwarded-for'] || '').split(',')[0]?.trim();
  return (
    String(req.headers['cf-connecting-ip'] || '') ||
    String(req.headers['x-real-ip'] || '') ||
    forwarded ||
    req.ip ||
    'unknown'
  );
};

const getRequestGeo = (req: Request) => ({
  country:
    String(req.headers['x-vercel-ip-country'] || '') ||
    String(req.headers['cf-ipcountry'] || '') ||
    String(req.headers['x-country-code'] || '') ||
    'unknown',
  region: String(req.headers['x-vercel-ip-country-region'] || req.headers['x-region'] || ''),
  city: String(req.headers['x-vercel-ip-city'] || req.headers['x-city'] || ''),
});

const browserPatterns: [RegExp, string][] = [
  [/edg\/([\d.]+)/i, 'Edge'],
  [/opr\/([\d.]+)/i, 'Opera'],
  [/chrome\/([\d.]+)/i, 'Chrome'],
  [/firefox\/([\d.]+)/i, 'Firefox'],
  [/version\/([\d.]+).*safari/i, 'Safari'],
  [/safari\/([\d.]+)/i, 'Safari'],
];

const parseBrowser = (userAgent: string) => {
  const match = browserPatterns.find(([pattern]) => pattern.test(userAgent));
  if (!match) {
    return 'Unknown';
  }
  const version = userAgent.match(match[0])?.[1]?.split('.')[0];
  return version ? `${match[1]} ${version}` : match[1];
};

const parseOs = (userAgent: string) => {
  if (/windows nt/i.test(userAgent)) return 'Windows';
  if (/android/i.test(userAgent)) return 'Android';
  if (/(iphone|ipad|ipod)/i.test(userAgent)) return 'iOS';
  if (/mac os x/i.test(userAgent)) return 'macOS';
  if (/linux/i.test(userAgent)) return 'Linux';
  return 'Unknown';
};

const parseDevice = (userAgent: string) => {
  if (/ipad|tablet/i.test(userAgent)) return 'Tablet';
  if (/mobile|android|iphone|ipod/i.test(userAgent)) return 'Mobile';
  return 'Desktop';
};

const safeReturnTo = (value: unknown) => {
  const raw = cleanText(value, 300);
  if (!raw) {
    return appUrl;
  }

  try {
    const url = new URL(raw, appUrl);
    const appOrigin = new URL(appUrl).origin;
    return url.origin === appOrigin ? url.toString() : appUrl;
  } catch {
    return appUrl;
  }
};

const formatFurnaceLevel = (value: unknown) => {
  if (value === null || value === undefined || value === '') {
    return '0';
  }

  const level = Number(value);
  if (!Number.isFinite(level)) {
    return String(value);
  }

  const lv = Math.trunc(level);
  if (lv <= 30) return String(lv);
  if (lv === 31) return '30-1';
  if (lv === 32) return '30-2';
  if (lv === 33) return '30-3';
  if (lv === 34) return '30-4';
  if (lv === 35) return '1';
  if (lv === 36) return '1-1';
  if (lv === 37) return '1-2';
  if (lv === 38) return '1-3';
  if (lv === 39) return '1-4';
  if (lv === 40) return '2';
  if (lv === 41) return '2-1';
  if (lv === 42) return '2-1';
  if (lv === 43) return '2-1';
  if (lv === 44) return '2-2';
  if (lv === 45) return '3';

  const relative = lv - 45;
  const tier = Math.floor(relative / 5) + 3;
  const stage = relative % 5;
  return stage === 0 ? String(tier) : `${tier}-${stage}`;
};

const parsePositiveInt = (value: unknown, fallback: number, max: number) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }

  return Math.min(Math.trunc(parsed), max);
};

const parseCoordinate = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
};

const normalizeExternalImageUrl = (value: unknown) => {
  const raw = cleanText(value, 600);
  if (!raw) {
    return '';
  }

  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol)) {
      return '';
    }

    const driveMatch = url.hostname.includes('drive.google.com')
      ? raw.match(/\/file\/d\/([^/]+)/) || raw.match(/[?&]id=([^&]+)/)
      : null;
    if (driveMatch?.[1]) {
      return `https://drive.google.com/uc?export=download&id=${driveMatch[1]}`;
    }

    return url.toString();
  } catch {
    return '';
  }
};

const normalizeGiftCodeText = (value: unknown, fallback = '') => {
  const text = cleanText(value, 500).replace(/\s+/g, ' ').trim();
  return text || fallback;
};

const isLikelyGiftCode = (value: string) => /^[A-Za-z0-9]{4,30}$/.test(value.trim());
const ignoredGiftCodeLabels = new Set(['code', 'codes', 'giftcode', 'giftcodes', 'reward', 'rewards', 'expires', 'expiry', 'status']);
const fetchGiftCodeSource = (url: string, init: RequestInit = {}) =>
  fetch(url, {
    ...init,
    signal: AbortSignal.timeout(Number(process.env.GIFT_CODE_SOURCE_TIMEOUT_MS || 7000)),
  });

const toGiftCodeItem = (
  code: string,
  source: GiftCodeSource,
  values: GiftCodeInput = {},
): GiftCodeItem | null => {
  const cleanCode = cleanText(code, 40).trim();
  if (!isLikelyGiftCode(cleanCode) || ignoredGiftCodeLabels.has(cleanCode.toLowerCase())) {
    return null;
  }

  return {
    code: cleanCode,
    rewards: normalizeGiftCodeText(values.rewards, 'Rewards not specified'),
    expiry: normalizeGiftCodeText(values.expiry, 'Unknown'),
    description: normalizeGiftCodeText(values.description),
    dateAdded: normalizeGiftCodeText(values.dateAdded),
    status: 'active',
    isActive: true,
  };
};

const parseWosGiftCodesRows = (html: string) => {
  const activeSection = html.slice(0, Math.max(html.toLowerCase().indexOf('expired code'), 0) || html.length);
  const rowPattern = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
  const cellPattern = /<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi;
  const tagPattern = /<[^>]+>/g;
  const codes: GiftCodeItem[] = [];
  let rowMatch: RegExpExecArray | null;

  while ((rowMatch = rowPattern.exec(activeSection)) !== null) {
    const cells: string[] = [];
    let cellMatch: RegExpExecArray | null;
    while ((cellMatch = cellPattern.exec(rowMatch[1])) !== null) {
      cells.push(cellMatch[1].replace(tagPattern, '').replace(/&nbsp;/g, ' ').trim());
    }

    const code = cells[0] || '';
    if (!code || code.toLowerCase() === 'code') {
      continue;
    }

    const item = toGiftCodeItem(code, 'wosgiftcodes', {
      description: cells[1],
      rewards: cells[2] || cells[1],
      expiry: cells[3] || 'Unknown',
    });
    if (item) {
      codes.push(item);
    }
  }

  return codes;
};

const fetchWosToolsGiftCodes = async (): Promise<GiftCodeItem[]> => {
  const response = await fetchGiftCodeSource('https://wostools.net/api/gift-codes', {
    headers: {
      Accept: 'application/json',
      'User-Agent': 'Mozilla/5.0 WhiteoutSurvival.dev/1.0',
    },
  });
  if (!response.ok) {
    return [];
  }

  const payload = await response.json().catch(() => null);
  const rawCodes = Array.isArray(payload?.codes) ? payload.codes : [];
  return rawCodes
    .filter((item: RawGiftCode) => String(item.status || '').toLowerCase() === 'active')
    .map((item: RawGiftCode) =>
      toGiftCodeItem(String(item.code || ''), 'wostools', {
        rewards: item.rewards || item.reward || item.rewardText || item.description || item.label,
        expiry: item.expiry || item.expires || item.expiresAt || item.expiration || item.expirationDate,
        description: item.description || item.label,
        dateAdded: item.dateAdded || item.date_added || item.created_at || item.date,
      }),
    )
    .filter((item: GiftCodeItem | null): item is GiftCodeItem => Boolean(item));
};

const fetchWosGiftCodesHtml = async (): Promise<GiftCodeItem[]> => {
  const response = await fetchGiftCodeSource('https://wosgiftcodes.com/', {
    headers: {
      Accept: 'text/html,application/xhtml+xml',
      'User-Agent': 'Mozilla/5.0 WhiteoutSurvival.dev/1.0',
    },
  });
  if (!response.ok) {
    return [];
  }

  return parseWosGiftCodesRows(await response.text());
};

const fetchBotDashboardGiftCodes = async (): Promise<GiftCodeItem[]> => {
  const response = await fetchGiftCodeSource('https://bot.whiteoutsurvival.dev/api/giftcodes', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    return [];
  }

  const payload = await response.json().catch(() => null);
  const rawCodes = Array.isArray(payload?.codes) ? payload.codes : [];
  return rawCodes
    .filter((item: RawGiftCode) => item.is_active !== false && item.isActive !== false)
    .map((item: RawGiftCode) =>
      toGiftCodeItem(String(item.code || item.giftcode || ''), 'bot_dashboard', {
        rewards: item.rewards || item.reward || item.description,
        expiry: item.expiry || item.expires || item.expiration,
        description: item.description,
        dateAdded: item.date_added || item.dateAdded || item.created_at,
      }),
    )
    .filter((item: GiftCodeItem | null): item is GiftCodeItem => Boolean(item));
};

const mergeGiftCodes = (sourceLists: GiftCodeItem[][]) => {
  const merged = new Map<string, GiftCodeItem>();

  sourceLists.flat().forEach((item) => {
    const key = item.code.toUpperCase();
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, { ...item });
      return;
    }

    if (existing.rewards === 'Rewards not specified' && item.rewards !== 'Rewards not specified') {
      existing.rewards = item.rewards;
    }
    if (existing.expiry === 'Unknown' && item.expiry !== 'Unknown') {
      existing.expiry = item.expiry;
    }
    if (!existing.description && item.description) {
      existing.description = item.description;
    }
    if (!existing.dateAdded && item.dateAdded) {
      existing.dateAdded = item.dateAdded;
    }
  });

  return Array.from(merged.values()).sort((a, b) => {
    const aTime = Date.parse(a.dateAdded || a.expiry || '');
    const bTime = Date.parse(b.dateAdded || b.expiry || '');
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
  });
};

const wosApiHeaders = {
  Accept: 'application/json, text/plain, */*',
  'Accept-Language': 'en-US,en;q=0.9',
  'Content-Type': 'application/x-www-form-urlencoded',
  Origin: 'https://wos-giftcode.centurygame.com',
  Referer: 'https://wos-giftcode.centurygame.com/',
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
};

const encodeWosGiftPayload = (values: Record<string, string>) => {
  const form = Object.keys(values)
    .sort()
    .map((key) => `${key}=${values[key]}`)
    .join('&');
  const sign = createHash('md5').update(`${form}tB87#kPtkxqOS2`).digest('hex');
  return `sign=${sign}&${form}`;
};

const cleanGiftCode = (value: unknown) => cleanText(value, 40).replace(/[^A-Za-z0-9]/g, '').trim();

const normalizeRedeemStatus = (payload: any) => {
  const message = cleanText(payload?.msg || payload?.message || 'Unknown response', 120).replace(/\.$/, '');
  const normalized = message.toUpperCase();
  const errCode = payload?.err_code;

  if (normalized === 'SUCCESS') {
    return { state: 'success', message: 'Gift code redeemed successfully.' };
  }
  if (normalized === 'RECEIVED' && errCode === 40008) {
    return { state: 'already_redeemed', message: 'This player has already received this reward.' };
  }
  if (normalized === 'CDK NOT FOUND') {
    return { state: 'invalid', message: 'This gift code is no longer valid.' };
  }
  if (normalized === 'TIME ERROR' && errCode === 40007) {
    return { state: 'expired', message: 'This gift code has expired.' };
  }
  if (normalized.includes('CAPTCHA')) {
    return { state: 'captcha_error', message: 'Captcha was incorrect or expired. Refresh it and try again.' };
  }
  if (normalized === 'USAGE LIMIT' && errCode === 40009) {
    return { state: 'limit', message: 'This gift code has reached its redemption limit.' };
  }
  if (normalized === 'SAME TYPE EXCHANGE' && errCode === 40011) {
    return { state: 'already_redeemed', message: 'This reward type was already claimed.' };
  }

  return { state: 'unknown', message: message || 'Unable to redeem this code right now.' };
};

const redeemViaBotDashboard = async (playerId: string, code: string) => {
  const response = await fetch('https://bot.whiteoutsurvival.dev/api/giftcodes/redeem', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      id: playerId,
      fid: playerId,
      codes: [code],
      guild_id: 0,
    }),
    signal: AbortSignal.timeout(Number(process.env.GIFT_REDEEM_TIMEOUT_MS || 90000)),
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || payload?.error || 'Automatic redeem service is unavailable.');
  }

  const result = Array.isArray(payload?.results) ? payload.results[0] : payload;
  if (!result) {
    throw new Error('Automatic redeem service did not return a result.');
  }

  const status = cleanText(result.status || result.message || payload.message || 'Unknown response', 160);
  const normalized = status.toUpperCase();
  if (result.success || normalized === 'SUCCESS') {
    return { state: 'success', message: 'Gift code redeemed successfully.' };
  }
  if (result.already_redeemed || normalized.includes('ALREADY') || normalized.includes('RECEIVED')) {
    return { state: 'already_redeemed', message: 'This player has already received this reward.' };
  }
  if (result.failed || normalized.includes('CDK_NOT_FOUND') || normalized.includes('NOT FOUND')) {
    return { state: 'invalid', message: 'This gift code is no longer valid.' };
  }
  if (normalized.includes('TIME_ERROR') || normalized.includes('EXPIRED')) {
    return { state: 'expired', message: 'This gift code has expired.' };
  }
  if (normalized.includes('RATE')) {
    return { state: 'rate_limited', message: 'Redemption service is busy. Try again in a moment.' };
  }

  return { state: 'unknown', message: status || 'Unable to redeem this code right now.' };
};

const fetchPlayerProfile = async (playerId: string): Promise<PlayerProfile | null> => {
  const cached = playerCache.get(playerId);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.profile;
  }

  const apiUrls = [
    'https://wos-giftcode-api.centurygame.com/api/player',
    'https://gof-report-api-formal.centurygame.com/api/player',
  ];
  let payload: any = null;

  for (const apiUrl of apiUrls) {
    const currentTime = Date.now();
    const form = `fid=${playerId}&time=${currentTime}`;
    const sign = createHash('md5').update(`${form}tB87#kPtkxqOS2`).digest('hex');
    const body = `sign=${sign}&${form}`;

    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json, text/plain, */*',
        'Content-Type': 'application/x-www-form-urlencoded',
        Referer: 'https://wos-giftcode-api.centurygame.com',
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      },
      body,
    });

    if (!response.ok) {
      continue;
    }

    const candidate = await response.json().catch(() => null);
    if (candidate?.data) {
      payload = candidate;
      break;
    }
  }

  if (!payload?.data) {
    return null;
  }

  const data = payload.data;
  const furnaceLevel = Number(data.stove_lv);
  const profile = {
    playerId,
    nickname: cleanText(data.nickname, 80) || `Player ${playerId}`,
    stateId: data.kid ? String(data.kid) : undefined,
    furnaceLevel: Number.isFinite(furnaceLevel) ? furnaceLevel : undefined,
    furnaceLevelFormatted: Number.isFinite(furnaceLevel) ? formatFurnaceLevel(furnaceLevel) : undefined,
    furnaceIcon: cleanText(data.stove_lv_content, 240) || undefined,
    avatarImage: cleanText(data.avatar_image, 240) || undefined,
  };

  playerCache.set(playerId, { expiresAt: Date.now() + playerCacheTtlMs, profile });
  return profile;
};

const getDb = async () => {
  if (mongoDb) {
    return mongoDb;
  }

  const uri = process.env.MONGODB_URI || process.env.MONGO_URI;
  if (!uri) {
    throw new Error('MONGODB_URI or MONGO_URI is required');
  }

  mongoClient = new MongoClient(uri, {
    maxPoolSize: Number(process.env.MONGODB_MAX_POOL_SIZE || 30),
    serverSelectionTimeoutMS: Number(process.env.MONGODB_SERVER_SELECTION_TIMEOUT_MS || 5000),
  });
  await mongoClient.connect();
  mongoDb = mongoClient.db(process.env.MONGODB_DB || 'whiteoutsurvival_dev');
  return mongoDb;
};

const getCollections = async () => {
  const db = await getDb();
  const islands = db.collection<IslandDocument>('daybreak_islands');
  const likes = db.collection<IslandLikeDocument>('daybreak_island_likes');
  const comments = db.collection<IslandCommentDocument>('daybreak_island_comments');
  const templates = db.collection<MessageTemplateDocument>('message_templates');
  const templateLikes = db.collection<TemplateLikeDocument>('message_template_likes');
  const users = db.collection<UserDocument>('users');
  const sessions = db.collection<SessionDocument>('auth_sessions');
  const oauthStates = db.collection<OAuthStateDocument>('auth_oauth_states');
  const siteVisits = db.collection<SiteVisitDocument>('site_visits');
  const adminSessions = db.collection<AdminSessionDocument>('admin_sessions');

  indexesReady ??= Promise.all([
    islands.createIndex({ createdAt: -1 }),
    islands.createIndex({ likes: -1, createdAt: -1 }),
    islands.createIndex({ playerId: 1 }),
    islands.createIndex({ creatorUserId: 1, createdAt: -1 }),
    likes.createIndex({ islandId: 1, viewerId: 1 }, { unique: true }),
    likes.createIndex({ viewerId: 1, createdAt: -1 }),
    comments.createIndex({ islandId: 1, createdAt: -1 }),
    templates.createIndex({ createdAt: -1 }),
    templates.createIndex({ likes: -1, createdAt: -1 }),
    templates.createIndex({ creatorUserId: 1, createdAt: -1 }),
    templates.createIndex({ tags: 1 }),
    templates.createIndex({ category: 1, createdAt: -1 }),
    templateLikes.createIndex({ templateId: 1, viewerId: 1 }, { unique: true }),
    templateLikes.createIndex({ viewerId: 1, createdAt: -1 }),
    users.createIndex({ 'providers.provider': 1, 'providers.providerUserId': 1 }),
    users.createIndex({ email: 1 }, { sparse: true }),
    users.createIndex({ 'playerAccounts.playerId': 1 }),
    sessions.createIndex({ sessionHash: 1 }, { unique: true }),
    sessions.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 }),
    oauthStates.createIndex({ stateHash: 1 }, { unique: true }),
    oauthStates.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 }),
    siteVisits.createIndex({ timestamp: -1 }),
    siteVisits.createIndex({ visitorId: 1 }),
    siteVisits.createIndex({ page: 1, timestamp: -1 }),
    adminSessions.createIndex({ tokenHash: 1 }, { unique: true }),
    adminSessions.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 }),
  ]).then(() => undefined);

  await indexesReady;
  return { islands, likes, comments, templates, templateLikes, users, sessions, oauthStates, siteVisits, adminSessions };
};

const userCanManageIsland = (user: UserDocument | null | undefined, island: IslandDocument) => {
  if (!user?._id) {
    return false;
  }

  if (island.creatorUserId?.equals(user._id)) {
    return true;
  }

  if (island.creatorUserId) {
    return false;
  }

  return user.playerAccounts.some((player) => player.playerId === island.playerId || player.nickname === island.creatorName);
};

const validTemplateCategories = new Set<MessageTemplateCategory>(['unicodes', 'emojis', 'funny', 'alliance-recruit']);

const cleanTemplateCategory = (value: unknown): MessageTemplateCategory => {
  const category = cleanText(value, 40) as MessageTemplateCategory;
  return validTemplateCategories.has(category) ? category : 'unicodes';
};

const cleanTemplateBody = (value: unknown) => {
  if (typeof value !== 'string') {
    return '';
  }

  return value.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim().slice(0, 4000);
};

const userCanManageTemplate = (user: UserDocument | null | undefined, template: MessageTemplateDocument) =>
  Boolean(user?._id && template.creatorUserId.equals(user._id));

const toTemplateResponse = (template: MessageTemplateDocument, viewer?: UserDocument | null) => ({
  id: template._id?.toString(),
  title: template.title,
  description: template.description || '',
  text: template.text,
  previewText: template.previewText,
  imageUrl: template.imageUrl || '',
  category: template.category,
  tags: template.tags,
  creatorName: template.creatorName,
  creatorUserId: template.creatorUserId.toString(),
  canManage: userCanManageTemplate(viewer, template),
  likes: template.likes,
  shares: template.shares,
  createdAt: template.createdAt.toISOString(),
  updatedAt: template.updatedAt.toISOString(),
});

const toIslandResponse = (island: IslandDocument, viewer?: UserDocument | null) => ({
  id: island._id?.toString(),
  title: island.title,
  creatorName: island.creatorName,
  creatorUserId: island.creatorUserId?.toString(),
  canManage: userCanManageIsland(viewer, island),
  description: island.description,
  playerId: island.playerId,
  coordinates: island.coordinates,
  player: island.player,
  server: island.server,
  alliance: island.alliance,
  tags: island.tags,
  imageUrl: island.imageUrl,
  likes: island.likes,
  shares: island.shares,
  commentsCount: island.commentsCount || 0,
  createdAt: island.createdAt.toISOString(),
});

const toCommentResponse = (comment: IslandCommentDocument) => ({
  id: comment._id?.toString(),
  islandId: comment.islandId.toString(),
  authorName: comment.authorName,
  message: comment.message,
  createdAt: comment.createdAt.toISOString(),
});

const toUserResponse = (user: UserDocument) => ({
  id: user._id?.toString(),
  email: user.email,
  displayName: user.displayName,
  avatarUrl: user.avatarUrl,
  providers: user.providers.map((provider) => provider.provider),
  playerAccounts: user.playerAccounts.map((player) => ({
    playerId: player.playerId,
    nickname: player.nickname,
    stateId: player.stateId,
    furnaceLevel: player.furnaceLevel,
    furnaceLevelFormatted: player.furnaceLevelFormatted,
    furnaceIcon: player.furnaceIcon,
    avatarImage: player.avatarImage,
    linkedAt: player.linkedAt.toISOString(),
  })),
  createdAt: user.createdAt.toISOString(),
});

const getCurrentUser = async (req: Request) => {
  const sessionToken = parseCookies(req.get('cookie'))[authCookieName];
  if (!sessionToken) {
    return null;
  }

  const { users, sessions } = await getCollections();
  const session = await sessions.findOne({ sessionHash: sha256(sessionToken), expiresAt: { $gt: new Date() } });
  if (!session) {
    return null;
  }

  return users.findOne({ _id: session.userId });
};

const requireCurrentUser = async (req: Request, res: Response) => {
  const user = await getCurrentUser(req);
  if (!user) {
    res.status(401).json({ error: 'Sign in required' });
    return null;
  }

  return user;
};

const viewerIdForUser = (user: UserDocument) => (user._id ? `user:${user._id.toString()}` : '');

const createSession = async (res: Response, userId: ObjectId) => {
  const sessionToken = randomBytes(32).toString('base64url');
  const now = new Date();
  const expiresAt = new Date(now.getTime() + authCookieMaxAgeMs);
  const { sessions } = await getCollections();
  await sessions.insertOne({
    sessionHash: sha256(sessionToken),
    userId,
    createdAt: now,
    expiresAt,
  });
  setSessionCookie(res, sessionToken);
};

const upsertOAuthUser = async (profile: OAuthProfile) => {
  const now = new Date();
  const { users } = await getCollections();
  const providerMatch = {
    'providers.provider': profile.provider,
    'providers.providerUserId': profile.providerUserId,
  };
  let user = await users.findOne(providerMatch);

  if (!user && profile.email) {
    user = await users.findOne({ email: profile.email.toLowerCase() });
  }

  if (!user) {
    const document: UserDocument = {
      email: profile.email?.toLowerCase(),
      displayName: profile.displayName,
      avatarUrl: profile.avatarUrl,
      providers: [{
        provider: profile.provider,
        providerUserId: profile.providerUserId,
        email: profile.email?.toLowerCase(),
        linkedAt: now,
      }],
      playerAccounts: [],
      createdAt: now,
      updatedAt: now,
    };
    const result = await users.insertOne(document);
    return { ...document, _id: result.insertedId };
  }

  const hasProvider = user.providers.some(
    (provider) => provider.provider === profile.provider && provider.providerUserId === profile.providerUserId,
  );
  const update = hasProvider
    ? {
        $set: {
          email: user.email || profile.email?.toLowerCase(),
          displayName: profile.displayName || user.displayName,
          avatarUrl: profile.avatarUrl || user.avatarUrl,
          updatedAt: now,
        },
      }
    : {
        $set: {
          email: user.email || profile.email?.toLowerCase(),
          displayName: profile.displayName || user.displayName,
          avatarUrl: profile.avatarUrl || user.avatarUrl,
          updatedAt: now,
        },
        $push: {
          providers: {
            provider: profile.provider,
            providerUserId: profile.providerUserId,
            email: profile.email?.toLowerCase(),
            linkedAt: now,
          },
        },
      };

  await users.updateOne({ _id: user._id }, update);
  return (await users.findOne({ _id: user._id })) || user;
};

const exchangeOAuthCode = async (provider: AuthProvider, code: string) => {
  const config = oauthConfig[provider];

  if (provider === 'discord' && !config.clientSecret && process.env.DISCORD_TOKEN_EXCHANGE_URL) {
    const proxyResponse = await fetch(process.env.DISCORD_TOKEN_EXCHANGE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ code, redirect_uri: getRedirectUri(provider) }),
    });
    const proxyData = await proxyResponse.json().catch(() => null);
    const providerUser = proxyData?.user;
    if (!proxyResponse.ok || !providerUser?.id) {
      throw new Error('Discord OAuth exchange failed');
    }

    const avatarUrl = providerUser.avatar
      ? `https://cdn.discordapp.com/avatars/${providerUser.id}/${providerUser.avatar}.png?size=128`
      : undefined;
    return {
      provider,
      providerUserId: String(providerUser.id),
      email: cleanText(providerUser.email, 240).toLowerCase() || undefined,
      displayName: cleanText(providerUser.global_name || providerUser.username, 120) || 'Discord User',
      avatarUrl,
    } satisfies OAuthProfile;
  }

  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    grant_type: 'authorization_code',
    redirect_uri: getRedirectUri(provider),
  });

  const tokenResponse = await fetch(config.tokenUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
    body,
  });
  const tokenData = await tokenResponse.json().catch(() => null);
  if (!tokenResponse.ok || !tokenData?.access_token) {
    throw new Error('OAuth token exchange failed');
  }

  const profileResponse = await fetch(config.userInfoUrl, {
    headers: { Authorization: `Bearer ${tokenData.access_token}`, Accept: 'application/json' },
  });
  const providerUser = await profileResponse.json().catch(() => null);
  if (!profileResponse.ok || !providerUser?.id) {
    throw new Error('OAuth profile lookup failed');
  }

  if (provider === 'google') {
    return {
      provider,
      providerUserId: String(providerUser.id),
      email: cleanText(providerUser.email, 240).toLowerCase() || undefined,
      displayName: cleanText(providerUser.name, 120) || 'Google User',
      avatarUrl: cleanText(providerUser.picture, 600) || undefined,
    } satisfies OAuthProfile;
  }

  const avatarUrl = providerUser.avatar
    ? `https://cdn.discordapp.com/avatars/${providerUser.id}/${providerUser.avatar}.png?size=128`
    : undefined;
  return {
    provider,
    providerUserId: String(providerUser.id),
    email: cleanText(providerUser.email, 240).toLowerCase() || undefined,
    displayName: cleanText(providerUser.global_name || providerUser.username, 120) || 'Discord User',
    avatarUrl,
  } satisfies OAuthProfile;
};

const getR2Client = () => {
  const accountId = required('CLOUDFLARE_ACCOUNT_ID');
  return new S3Client({
    region: 'auto',
    endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: required('CLOUDFLARE_R2_ACCESS_KEY_ID'),
      secretAccessKey: required('CLOUDFLARE_R2_SECRET_ACCESS_KEY'),
    },
  });
};

const uploadToR2 = async (file: Express.Multer.File, title: string, folder = 'daybreak-islands') => {
  const bucket = required('CLOUDFLARE_R2_BUCKET');
  const publicUrl = normalizePublicUrl(required('CLOUDFLARE_R2_PUBLIC_URL'));
  const extension = file.originalname.includes('.')
    ? file.originalname.split('.').pop()?.toLowerCase()
    : file.mimetype.split('/').pop();
  const objectKey = `${folder}/${Date.now()}-${slugify(title)}-${randomUUID()}.${extension || 'webp'}`;

  await getR2Client().send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: objectKey,
      Body: file.buffer,
      ContentType: file.mimetype,
      CacheControl: 'public, max-age=31536000, immutable',
    }),
  );

  return {
    imageUrl: `${publicUrl}/${objectKey}`,
    objectKey,
  };
};

const uploadRemoteImageToR2 = async (remoteUrl: string, title: string, folder = 'daybreak-islands') => {
  const response = await fetch(remoteUrl, {
    headers: {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    },
  });

  if (!response.ok) {
    throw new Error('Image link could not be downloaded');
  }

  const contentType = response.headers.get('content-type') || 'image/jpeg';
  if (!contentType.startsWith('image/')) {
    throw new Error('Image link must point to an image file');
  }

  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > maxUploadBytes) {
    throw new Error('Image is larger than the upload limit');
  }

  const extension = contentType.split('/').pop()?.split(';')[0] || 'jpg';
  return uploadToR2(
    {
      buffer: bytes,
      mimetype: contentType,
      originalname: `remote.${extension}`,
    } as Express.Multer.File,
    title,
    folder,
  );
};

const deleteFromR2 = async (objectKey: string) => {
  if (!objectKey) {
    return;
  }

  await getR2Client().send(
    new DeleteObjectCommand({
      Bucket: required('CLOUDFLARE_R2_BUCKET'),
      Key: objectKey,
    }),
  );
};

const sendStorageError = (res: Response, error: unknown) => {
  const message = error instanceof Error ? error.message : 'Storage operation failed';
  const missingConfig = message.endsWith('is required');
  res.status(missingConfig ? 503 : 500).json({
    error: missingConfig ? 'Storage is not configured' : 'Storage operation failed',
    detail: message,
  });
};

const allowedOrigins = (process.env.CORS_ORIGINS || '')
  .split(',')
  .map((origin) => origin.trim())
  .filter(Boolean);

app.use(cors({
  origin: allowedOrigins.length
    ? (origin, callback) => {
        if (!origin || allowedOrigins.includes(origin)) {
          callback(null, true);
          return;
        }

        callback(new Error('Origin is not allowed by CORS'));
      }
    : true,
  credentials: true,
}));
app.use(express.json({ limit: process.env.JSON_BODY_LIMIT || '1mb' }));

app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', message: 'Whiteout Survival backend is running' });
});

const serializeVisit = (visit: SiteVisitDocument) => ({
  id: visit.id,
  visitorId: visit.visitorId,
  ip: visit.ip,
  country: visit.country,
  region: visit.region,
  city: visit.city,
  browser: visit.browser,
  os: visit.os,
  device: visit.device,
  page: visit.page,
  referrer: visit.referrer,
  userAgent: visit.userAgent,
  language: visit.language,
  timezone: visit.timezone,
  screen: visit.screen,
  viewport: visit.viewport,
  timestamp: visit.timestamp.toISOString(),
});

const getAdminSession = async (req: Request) => {
  const token = parseCookies(req.get('cookie'))[adminCookieName];
  if (!token || !isAdminConfigured()) {
    return null;
  }

  const { adminSessions } = await getCollections();
  return adminSessions.findOne({ tokenHash: sha256(token), expiresAt: { $gt: new Date() } });
};

const requireAdminSession = async (req: Request, res: Response) => {
  const session = await getAdminSession(req);
  if (!session) {
    res.status(401).json({ authenticated: false, error: 'Admin access required' });
    return null;
  }

  return session;
};

app.get('/api/admin/status', async (req, res) => {
  try {
    if (!isAdminConfigured()) {
      res.json({ configured: false, authenticated: false });
      return;
    }

    const session = await getAdminSession(req);
    res.json({
      configured: true,
      authenticated: Boolean(session),
      expiresAt: session?.expiresAt.toISOString(),
    });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Admin status failed' });
  }
});

app.post('/api/admin/login', async (req, res) => {
  try {
    if (!isAdminConfigured()) {
      res.status(503).json({ error: 'Admin secret is not configured' });
      return;
    }

    if (!verifyAdminSecret(req.body?.password || req.body?.token)) {
      res.status(401).json({ error: 'Invalid admin secret' });
      return;
    }

    const token = randomBytes(32).toString('base64url');
    const now = new Date();
    const expiresAt = new Date(now.getTime() + adminSessionTtlMs);
    const { adminSessions } = await getCollections();
    await adminSessions.insertOne({
      tokenHash: sha256(token),
      createdAt: now,
      expiresAt,
      ip: getRequestIp(req),
      userAgent: req.get('user-agent') || '',
    });

    setAdminCookie(res, token, expiresAt);
    res.json({ authenticated: true, expiresAt: expiresAt.toISOString() });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Admin login failed' });
  }
});

app.post('/api/admin/logout', async (req, res) => {
  try {
    const token = parseCookies(req.get('cookie'))[adminCookieName];
    if (token) {
      const { adminSessions } = await getCollections();
      await adminSessions.deleteOne({ tokenHash: sha256(token) });
    }
    clearAdminCookie(res);
    res.json({ authenticated: false });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Admin logout failed' });
  }
});

app.post('/api/admin/track', async (req, res) => {
  try {
    const userAgent = req.get('user-agent') || '';
    const existingVisitorId = parseCookies(req.get('cookie')).wos_visitor_id;
    const visitorId = existingVisitorId || randomUUID();
    const geo = getRequestGeo(req);
    const now = new Date();
    const { siteVisits } = await getCollections();

    await siteVisits.insertOne({
      id: randomUUID(),
      visitorId,
      ip: getRequestIp(req),
      country: cleanText(geo.country, 80) || 'unknown',
      region: cleanText(geo.region, 120),
      city: cleanText(geo.city, 120),
      browser: parseBrowser(userAgent),
      os: parseOs(userAgent),
      device: parseDevice(userAgent),
      page: cleanText(req.body?.page || req.get('referer') || '/', 500) || '/',
      referrer: cleanText(req.body?.referrer, 500),
      userAgent,
      language: cleanText(req.body?.language, 80),
      timezone: cleanText(req.body?.timezone, 100),
      screen: cleanText(req.body?.screen, 80),
      viewport: cleanText(req.body?.viewport, 80),
      timestamp: now,
      createdAt: now,
    });

    if (!existingVisitorId) {
      res.cookie('wos_visitor_id', visitorId, {
        sameSite: 'lax',
        secure: adminCookieSecure,
        path: '/',
        maxAge: 365 * 24 * 60 * 60 * 1000,
      });
    }

    res.json({ ok: true });
  } catch {
    res.status(202).json({ ok: false });
  }
});

app.get('/api/admin/visits', async (req, res) => {
  try {
    const session = await requireAdminSession(req, res);
    if (!session) {
      return;
    }

    const { siteVisits, users, adminSessions } = await getCollections();
    const limit = Math.max(1, Math.min(Number(req.query.limit) || 100, 500));
    const page = cleanText(req.query.page, 500);
    const ip = cleanText(req.query.ip, 80);
    const query: Record<string, unknown> = {};
    if (page) query.page = page;
    if (ip) query.ip = ip;

    const [visitDocs, totalVisits, uniqueVisitors, uniqueIps, userCount, activeSessions, topPages, topCountries, topBrowsers] =
      await Promise.all([
        siteVisits.find(query).sort({ timestamp: -1 }).limit(limit).toArray(),
        siteVisits.countDocuments(query),
        siteVisits.distinct('visitorId').then((values) => values.filter(Boolean).length),
        siteVisits.distinct('ip').then((values) => values.filter(Boolean).length),
        users.countDocuments().catch(() => 0),
        adminSessions.countDocuments({ expiresAt: { $gt: new Date() } }),
        siteVisits.aggregate([{ $group: { _id: '$page', count: { $sum: 1 } } }, { $sort: { count: -1 } }, { $limit: 8 }]).toArray(),
        siteVisits.aggregate([{ $group: { _id: '$country', count: { $sum: 1 } } }, { $sort: { count: -1 } }, { $limit: 8 }]).toArray(),
        siteVisits.aggregate([{ $group: { _id: '$browser', count: { $sum: 1 } } }, { $sort: { count: -1 } }, { $limit: 8 }]).toArray(),
      ]);

    res.json({
      summary: {
        totalVisits,
        uniqueVisitors,
        uniqueIps,
        userCount,
        activeAdminSessions: activeSessions,
      },
      visits: visitDocs.map(serializeVisit),
      topPages: topPages.map((item) => ({ name: item._id || 'unknown', count: item.count })),
      topCountries: topCountries.map((item) => ({ name: item._id || 'unknown', count: item.count })),
      topBrowsers: topBrowsers.map((item) => ({ name: item._id || 'unknown', count: item.count })),
    });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Admin visits failed' });
  }
});

app.get('/api/gift-codes', async (_req, res) => {
  try {
    const settled = await Promise.allSettled([
      fetchWosToolsGiftCodes(),
      fetchWosGiftCodesHtml(),
      fetchBotDashboardGiftCodes(),
    ]);
    const sourceLists = settled.map((result) => (result.status === 'fulfilled' ? result.value : []));
    const codes = mergeGiftCodes(sourceLists);

    res.set('Cache-Control', 'public, max-age=30, s-maxage=30, stale-while-revalidate=120');
    res.json({
      codes,
      lastUpdated: new Date().toISOString(),
      refreshAfterSeconds: 30,
    });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Failed to fetch gift codes' });
  }
});

app.post('/api/gift-codes/captcha', async (req, res) => {
  try {
    const playerId = cleanPlayerId(req.body?.playerId);
    if (!/^\d{8,10}$/.test(playerId)) {
      res.status(400).json({ error: 'Enter a valid player ID.' });
      return;
    }

    const player = await fetchPlayerProfile(playerId);
    if (!player) {
      res.status(404).json({ error: 'Player not found. Check the ID and try again.' });
      return;
    }

    const captchaResponse = await fetch('https://wos-giftcode-api.centurygame.com/api/captcha', {
      method: 'POST',
      headers: wosApiHeaders,
      body: encodeWosGiftPayload({
        fid: playerId,
        time: String(Date.now()),
      }),
    });

    if (!captchaResponse.ok) {
      res.status(captchaResponse.status === 429 ? 429 : 502).json({
        error: captchaResponse.status === 429 ? 'Captcha service is busy. Try again shortly.' : 'Unable to load captcha.',
      });
      return;
    }

    const payload = await captchaResponse.json().catch(() => null);
    if (String(payload?.msg || '').toLowerCase() !== 'success' || !payload?.data) {
      res.status(502).json({ error: 'Unable to load captcha. Try again.' });
      return;
    }

    const captchaImage =
      typeof payload.data === 'string'
        ? payload.data
        : payload.data.img || payload.data.data || payload.data.image || '';

    res.json({
      player,
      captchaImage: String(captchaImage).startsWith('data:image')
        ? captchaImage
        : `data:image/png;base64,${captchaImage}`,
      issuedAt: new Date().toISOString(),
    });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Unable to load captcha.' });
  }
});

app.post('/api/gift-codes/redeem', async (req, res) => {
  try {
    const playerId = cleanPlayerId(req.body?.playerId);
    const code = cleanGiftCode(req.body?.code);
    const captchaCode = cleanText(req.body?.captchaCode, 12).replace(/[^A-Za-z0-9]/g, '').trim();

    if (!/^\d{8,10}$/.test(playerId)) {
      res.status(400).json({ error: 'Enter a valid player ID.' });
      return;
    }
    if (!code) {
      res.status(400).json({ error: 'Gift code is required.' });
      return;
    }

    if (!captchaCode) {
      const player = await fetchPlayerProfile(playerId);
      if (!player) {
        res.status(404).json({ state: 'not_found', message: 'Player not found. Check the ID and try again.' });
        return;
      }

      const autoResult = await redeemViaBotDashboard(playerId, code);
      res.json({
        ...autoResult,
        player,
        checkedAt: new Date().toISOString(),
      });
      return;
    }

    if (!/^[A-Za-z0-9]{4,8}$/.test(captchaCode)) {
      res.status(400).json({ error: 'Captcha is invalid.' });
      return;
    }

    const response = await fetch('https://wos-giftcode-api.centurygame.com/api/gift_code', {
      method: 'POST',
      headers: wosApiHeaders,
      body: encodeWosGiftPayload({
        captcha_code: captchaCode,
        cdk: code,
        fid: playerId,
        time: String(Date.now()),
      }),
    });

    if (response.status === 429) {
      res.status(429).json({ state: 'rate_limited', message: 'Redemption service is busy. Try again in a moment.' });
      return;
    }

    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload) {
      res.status(502).json({ state: 'error', message: 'Unable to redeem right now. Try again shortly.' });
      return;
    }

    res.json({
      ...normalizeRedeemStatus(payload),
      rawCode: payload.err_code ?? null,
      checkedAt: new Date().toISOString(),
    });
  } catch (error) {
    res.status(500).json({ state: 'error', message: error instanceof Error ? error.message : 'Unable to redeem.' });
  }
});

app.get('/api/auth/providers', (_req, res) => {
  res.json({
    providers: {
      google: isProviderConfigured('google'),
      discord: isProviderConfigured('discord'),
    },
  });
});

app.get('/api/auth/session', async (req, res) => {
  try {
    const user = await getCurrentUser(req);
    res.json({ user: user ? toUserResponse(user) : null });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/auth/:provider', async (req, res) => {
  const provider = req.params.provider as AuthProvider;
  if (!['google', 'discord'].includes(provider)) {
    res.status(404).json({ error: 'Auth provider not found' });
    return;
  }

  if (!isProviderConfigured(provider)) {
    res.redirect(`${appUrl}?auth_error=${encodeURIComponent(`${provider} sign-in is not configured`)}`);
    return;
  }

  try {
    const state = randomBytes(24).toString('base64url');
    const now = new Date();
    const { oauthStates } = await getCollections();
    await oauthStates.insertOne({
      stateHash: sha256(state),
      provider,
      returnTo: safeReturnTo(req.query.returnTo),
      createdAt: now,
      expiresAt: new Date(now.getTime() + 10 * 60 * 1000),
    });

    const config = oauthConfig[provider];
    const params = new URLSearchParams({
      client_id: config.clientId,
      redirect_uri: getRedirectUri(provider),
      response_type: 'code',
      scope: config.scope,
      state,
      prompt: provider === 'google' ? 'select_account' : 'none',
    });

    res.redirect(`${config.authorizeUrl}?${params.toString()}`);
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/auth/:provider/callback', async (req, res) => {
  const provider = req.params.provider as AuthProvider;
  if (!['google', 'discord'].includes(provider)) {
    res.redirect(`${appUrl}?auth_error=${encodeURIComponent('Auth provider not found')}`);
    return;
  }

  try {
    const code = cleanText(req.query.code, 1200);
    const state = cleanText(req.query.state, 240);
    if (!code || !state) {
      throw new Error('Missing OAuth callback data');
    }

    const { oauthStates } = await getCollections();
    const stateDocument = await oauthStates.findOneAndDelete({
      stateHash: sha256(state),
      provider,
      expiresAt: { $gt: new Date() },
    });
    if (!stateDocument) {
      throw new Error('OAuth state expired or invalid');
    }

    const profile = await exchangeOAuthCode(provider, code);
    const user = await upsertOAuthUser(profile);
    if (!user._id) {
      throw new Error('Unable to create user session');
    }

    await createSession(res, user._id);
    res.redirect(stateDocument.returnTo || appUrl);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Sign-in failed';
    res.redirect(`${appUrl}?auth_error=${encodeURIComponent(message)}`);
  }
});

app.post('/api/auth/logout', async (req, res) => {
  try {
    const sessionToken = parseCookies(req.get('cookie'))[authCookieName];
    if (sessionToken) {
      const { sessions } = await getCollections();
      await sessions.deleteOne({ sessionHash: sha256(sessionToken) });
    }

    clearSessionCookie(res);
    res.json({ ok: true });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.post('/api/profile/player-accounts', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const playerId = cleanPlayerId(req.body.playerId);
    if (!/^\d{8,9}$/.test(playerId)) {
      res.status(400).json({ error: 'Player ID must be 8 or 9 digits' });
      return;
    }

    const player = await fetchPlayerProfile(playerId);
    if (!player) {
      res.status(404).json({ error: 'Player not found' });
      return;
    }

    const { users } = await getCollections();
    const now = new Date();
    const linkedPlayer: LinkedPlayerAccount = { ...player, linkedAt: now };
    await users.updateOne(
      { _id: user._id, 'playerAccounts.playerId': { $ne: playerId } },
      { $push: { playerAccounts: linkedPlayer }, $set: { updatedAt: now } },
    );

    const updatedUser = await users.findOne({ _id: user._id });
    res.json({ user: updatedUser ? toUserResponse(updatedUser) : toUserResponse(user) });
  } catch (error) {
    res.status(500).json({ error: error instanceof Error ? error.message : 'Unable to link player account' });
  }
});

app.get('/api/message-templates', async (req, res) => {
  try {
    const sort: Sort =
      req.query.sort === 'popular'
        ? { likes: -1 as const, createdAt: -1 as const }
        : { createdAt: -1 as const };
    const limit = parsePositiveInt(req.query.limit, 48, 100);
    const skip = Math.min(Math.max(Number(req.query.skip) || 0, 0), 5000);
    const tag = cleanText(req.query.tag, 60).replace(/^#/, '');
    const category = cleanText(req.query.category, 40);
    const query: Record<string, unknown> = {};
    if (tag) {
      query.tags = { $regex: new RegExp(`^${escapeRegExp(tag)}$`, 'i') };
    }
    if (validTemplateCategories.has(category as MessageTemplateCategory)) {
      query.category = category;
    }

    const viewer = await getCurrentUser(req).catch(() => null);
    const { templates } = await getCollections();
    const [results, total] = await Promise.all([
      templates.find(query).sort(sort).skip(skip).limit(limit).toArray(),
      Object.keys(query).length ? templates.countDocuments(query) : templates.estimatedDocumentCount(),
    ]);

    res.json({
      templates: results.map((template) => toTemplateResponse(template, viewer)),
      page: { limit, skip, total, tag: tag || undefined, category: query.category },
    });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/message-templates/me/uploads', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const limit = parsePositiveInt(req.query.limit, 48, 100);
    const { templates } = await getCollections();
    const results = await templates.find({ creatorUserId: user._id }).sort({ createdAt: -1 }).limit(limit).toArray();
    res.json({ templates: results.map((template) => toTemplateResponse(template, user)) });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/message-templates/me/favorites', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const limit = parsePositiveInt(req.query.limit, 48, 100);
    const { templates, templateLikes } = await getCollections();
    const likeDocs = await templateLikes.find({ viewerId: viewerIdForUser(user) }).sort({ createdAt: -1 }).limit(limit).toArray();
    const templateIds = likeDocs.map((like) => like.templateId);
    const results = templateIds.length
      ? await templates.find({ _id: { $in: templateIds } }).toArray()
      : [];
    const byId = new Map(results.map((template) => [template._id?.toString(), template]));
    res.json({
      favoriteIds: templateIds.map((id) => id.toString()),
      templates: templateIds.map((id) => byId.get(id.toString())).filter(Boolean).map((template) => toTemplateResponse(template as MessageTemplateDocument, user)),
    });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.post('/api/message-templates', upload.single('image'), async (req: UploadedRequest, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const title = cleanText(req.body.title, 90);
    const description = cleanText(req.body.description, 360);
    const text = cleanTemplateBody(req.body.text);
    const previewText = cleanTemplateBody(req.body.previewText);
    const category = cleanTemplateCategory(req.body.category);
    const imageUrlInput = normalizeExternalImageUrl(req.body.imageUrl);

    if (!title || !text) {
      res.status(400).json({ error: 'Template title and text are required' });
      return;
    }

    const now = new Date();
    const uploadedImage = req.file
      ? await uploadToR2(req.file, title, 'message-templates')
      : imageUrlInput
        ? await uploadRemoteImageToR2(imageUrlInput, title, 'message-templates')
        : null;
    const document: MessageTemplateDocument = {
      title,
      description: description || undefined,
      text,
      previewText: previewText || undefined,
      imageUrl: uploadedImage?.imageUrl,
      imageObjectKey: uploadedImage?.objectKey,
      category,
      tags: parseTags(req.body.tags),
      creatorName: cleanText(user.playerAccounts[0]?.nickname || user.displayName, 80) || 'WOS Player',
      creatorUserId: user._id,
      likes: 0,
      shares: 0,
      createdAt: now,
      updatedAt: now,
    };

    const { templates } = await getCollections();
    const result = await templates.insertOne(document);
    res.status(201).json({ template: toTemplateResponse({ ...document, _id: result.insertedId }, user) });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.patch('/api/message-templates/:id', upload.single('image'), async (req: UploadedRequest, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const templateId = new ObjectId(String(req.params.id));
    const title = cleanText(req.body.title, 90);
    const description = cleanText(req.body.description, 360);
    const text = cleanTemplateBody(req.body.text);
    const previewText = cleanTemplateBody(req.body.previewText);
    const category = cleanTemplateCategory(req.body.category);
    const imageUrlInput = normalizeExternalImageUrl(req.body.imageUrl);

    if (!title || !text) {
      res.status(400).json({ error: 'Template title and text are required' });
      return;
    }

    const { templates } = await getCollections();
    const template = await templates.findOne({ _id: templateId });
    if (!template) {
      res.status(404).json({ error: 'Template not found' });
      return;
    }
    if (!userCanManageTemplate(user, template)) {
      res.status(403).json({ error: 'You can only edit templates you created.' });
      return;
    }

    const uploadedImage = req.file
      ? await uploadToR2(req.file, title, 'message-templates')
      : imageUrlInput
        ? await uploadRemoteImageToR2(imageUrlInput, title, 'message-templates')
        : null;
    const setFields: Partial<MessageTemplateDocument> = {
      title,
      description: description || undefined,
      text,
      previewText: previewText || undefined,
      category,
      tags: parseTags(req.body.tags),
      updatedAt: new Date(),
    };

    if (uploadedImage) {
      setFields.imageUrl = uploadedImage.imageUrl;
      setFields.imageObjectKey = uploadedImage.objectKey;
    }

    const updated = await templates.findOneAndUpdate(
      { _id: templateId },
      {
        $set: setFields,
      },
      { returnDocument: 'after' },
    );

    res.json({ template: toTemplateResponse(updated || template, user) });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to update template',
      detail: error instanceof Error ? error.message : undefined,
    });
  }
});

app.post('/api/message-templates/:id/like', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const templateId = new ObjectId(req.params.id);
    const viewerId = viewerIdForUser(user);
    const { templates, templateLikes } = await getCollections();
    const likeResult = await templateLikes.updateOne(
      { templateId, viewerId },
      { $setOnInsert: { templateId, viewerId, createdAt: new Date() } },
      { upsert: true },
    );
    if (likeResult.upsertedCount) {
      await templates.updateOne({ _id: templateId }, { $inc: { likes: 1 }, $set: { updatedAt: new Date() } });
    }

    const template = await templates.findOne({ _id: templateId });
    if (!template) {
      res.status(404).json({ error: 'Template not found' });
      return;
    }

    res.json({ template: toTemplateResponse(template, user), liked: true });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({ error: 'Unable to like template' });
  }
});

app.delete('/api/message-templates/:id', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const templateId = new ObjectId(req.params.id);
    const { templates, templateLikes } = await getCollections();
    const template = await templates.findOne({ _id: templateId });
    if (!template) {
      res.status(404).json({ error: 'Template not found' });
      return;
    }
    if (!userCanManageTemplate(user, template)) {
      res.status(403).json({ error: 'You can only delete templates you created.' });
      return;
    }

    await templates.deleteOne({ _id: templateId });
    void templateLikes.deleteMany({ templateId });
    res.json({ deleted: true, id: templateId.toString() });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({ error: 'Unable to delete template' });
  }
});

app.post('/api/message-templates/:id/share', async (req, res) => {
  try {
    const templateId = new ObjectId(req.params.id);
    const viewer = await getCurrentUser(req).catch(() => null);
    const { templates } = await getCollections();
    const result = await templates.findOneAndUpdate(
      { _id: templateId },
      { $inc: { shares: 1 }, $set: { updatedAt: new Date() } },
      { returnDocument: 'after' },
    );
    if (!result) {
      res.status(404).json({ error: 'Template not found' });
      return;
    }
    res.json({ template: toTemplateResponse(result, viewer) });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({ error: 'Unable to share template' });
  }
});

app.get('/api/daybreak/islands', async (req, res) => {
  try {
    const sort: Sort =
      req.query.sort === 'popular'
        ? { likes: -1 as const, createdAt: -1 as const }
        : { createdAt: -1 as const };
    const limit = parsePositiveInt(req.query.limit, 24, 60);
    const skip = Math.min(Math.max(Number(req.query.skip) || 0, 0), 5000);
    const tag = cleanText(req.query.tag, 60).replace(/^#/, '');
    const query = tag ? { tags: { $regex: new RegExp(`^${escapeRegExp(tag)}$`, 'i') } } : {};
    const viewer = await getCurrentUser(req).catch(() => null);
    const { islands } = await getCollections();
    const [results, total] = await Promise.all([
      islands.find(query).sort(sort).skip(skip).limit(limit).toArray(),
      tag ? islands.countDocuments(query) : islands.estimatedDocumentCount(),
    ]);
    res.json({ islands: results.map((island) => toIslandResponse(island, viewer)), page: { limit, skip, total, tag: tag || undefined } });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/daybreak/me/uploads', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const limit = parsePositiveInt(req.query.limit, 24, 60);
    const linkedPlayerIds = user.playerAccounts.map((player) => player.playerId);
    const query = linkedPlayerIds.length
      ? { $or: [{ creatorUserId: user._id }, { playerId: { $in: linkedPlayerIds } }] }
      : { creatorUserId: user._id };
    const { islands } = await getCollections();
    const results = await islands.find(query).sort({ createdAt: -1 }).limit(limit).toArray();
    res.json({ islands: results.map((island) => toIslandResponse(island, user)) });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/daybreak/me/favorites', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const limit = parsePositiveInt(req.query.limit, 24, 60);
    const { islands, likes } = await getCollections();
    const likeDocs = await likes.find({ viewerId: viewerIdForUser(user) }).sort({ createdAt: -1 }).limit(limit).toArray();
    const islandIds = likeDocs.map((like) => like.islandId);
    const results = islandIds.length
      ? await islands.find({ _id: { $in: islandIds } }).toArray()
      : [];
    const byId = new Map(results.map((island) => [island._id?.toString(), island]));
    res.json({
      favoriteIds: islandIds.map((id) => id.toString()),
      islands: islandIds.map((id) => byId.get(id.toString())).filter(Boolean).map((island) => toIslandResponse(island as IslandDocument, user)),
    });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.get('/api/daybreak/players/:playerId', async (req, res) => {
  try {
    const playerId = cleanPlayerId(req.params.playerId);
    if (!/^\d{8,9}$/.test(playerId)) {
      res.status(400).json({ error: 'Player ID must be 8 or 9 digits' });
      return;
    }

    const player = await fetchPlayerProfile(playerId);
    if (!player) {
      res.status(404).json({ error: 'Player not found' });
      return;
    }

    res.json({ player });
  } catch (error) {
    res.status(500).json({ error: 'Unable to fetch player details' });
  }
});

app.post('/api/daybreak/islands', upload.single('image'), async (req: UploadedRequest, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user) {
      return;
    }

    const title = cleanText(req.body.title, 90);
    const description = cleanText(req.body.description, 420) || 'Shared Daybreak Island layout.';
    const playerId = cleanPlayerId(req.body.playerId);
    const coordinateX = parseCoordinate(req.body.coordinateX);
    const coordinateY = parseCoordinate(req.body.coordinateY);
    const imageUrlInput = normalizeExternalImageUrl(req.body.imageUrl);

    if (!title || !/^\d{8,9}$/.test(playerId) || coordinateX === null || coordinateY === null || (!req.file && !imageUrlInput)) {
      res.status(400).json({ error: 'Island title, player ID, X/Y coordinates, and an image file or image link are required' });
      return;
    }

    const player = await fetchPlayerProfile(playerId);
    if (!player) {
      res.status(404).json({ error: 'Player details could not be fetched from the WOS player API' });
      return;
    }

    const { imageUrl, objectKey } = req.file
      ? await uploadToR2(req.file, title)
      : await uploadRemoteImageToR2(imageUrlInput, title);
    const now = new Date();
    const document: IslandDocument = {
      title,
      creatorName: player.nickname,
      creatorUserId: user._id,
      description,
      playerId,
      coordinates: {
        x: coordinateX,
        y: coordinateY,
      },
      player,
      server: player.stateId,
      alliance: undefined,
      tags: parseTags(req.body.tags),
      imageUrl,
      objectKey,
      likes: 0,
      shares: 0,
      commentsCount: 0,
      createdAt: now,
      updatedAt: now,
    };

    const { islands } = await getCollections();
    const result = await islands.insertOne(document);
    res.status(201).json({ island: toIslandResponse({ ...document, _id: result.insertedId }, user) });
  } catch (error) {
    sendStorageError(res, error);
  }
});

app.patch('/api/daybreak/islands/:id', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const islandId = new ObjectId(req.params.id);
    const title = cleanText(req.body.title, 90);
    const coordinateX = parseCoordinate(req.body.coordinateX);
    const coordinateY = parseCoordinate(req.body.coordinateY);

    if (!title || coordinateX === null || coordinateY === null) {
      res.status(400).json({ error: 'Island title and X/Y coordinates are required' });
      return;
    }

    const { islands } = await getCollections();
    const island = await islands.findOne({ _id: islandId });
    if (!island) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    if (!userCanManageIsland(user, island)) {
      res.status(403).json({ error: 'You can only edit islands you uploaded.' });
      return;
    }

    const updated = await islands.findOneAndUpdate(
      { _id: islandId },
      {
        $set: {
          title,
          coordinates: {
            x: coordinateX,
            y: coordinateY,
          },
          tags: parseTags(req.body.tags),
          updatedAt: new Date(),
        },
      },
      { returnDocument: 'after' },
    );

    res.json({ island: toIslandResponse(updated || island, user) });
  } catch (error) {
    console.error('Unable to update island', error);
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to update island',
      detail: error instanceof Error ? error.message : undefined,
    });
  }
});

app.get('/api/daybreak/islands/:id/comments', async (req, res) => {
  try {
    const islandId = new ObjectId(req.params.id);
    const { comments } = await getCollections();
    const results = await comments.find({ islandId }).sort({ createdAt: -1 }).limit(30).toArray();
    res.json({ comments: results.map(toCommentResponse) });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to load comments',
    });
  }
});

app.post('/api/daybreak/islands/:id/comments', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user) {
      return;
    }

    const islandId = new ObjectId(req.params.id);
    const authorName = cleanText(user.playerAccounts[0]?.nickname || user.displayName, 60);
    const message = cleanText(req.body.message, 360);

    if (!message) {
      res.status(400).json({ error: 'Comment is required' });
      return;
    }

    const { islands, comments } = await getCollections();
    const island = await islands.findOne({ _id: islandId });
    if (!island) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    const now = new Date();
    await comments.insertOne({ islandId, authorName, message, createdAt: now });
    const updated = await islands.findOneAndUpdate(
      { _id: islandId },
      { $inc: { commentsCount: 1 }, $set: { updatedAt: now } },
      { returnDocument: 'after' },
    );
    const latest = await comments.find({ islandId }).sort({ createdAt: -1 }).limit(30).toArray();

    res.status(201).json({
      island: updated ? toIslandResponse(updated, user) : toIslandResponse(island, user),
      comments: latest.map(toCommentResponse),
    });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to add comment',
    });
  }
});

app.post('/api/daybreak/islands/:id/like', async (req, res) => {
  try {
    const islandId = new ObjectId(req.params.id);
    const user = await getCurrentUser(req).catch(() => null);
    const viewerId =
      (user ? viewerIdForUser(user) : '') ||
      cleanText(req.body.viewerId, 120) ||
      cleanText(req.get('x-viewer-id'), 120) ||
      req.ip ||
      'anonymous';
    const { islands, likes } = await getCollections();

    const likeResult = await likes.updateOne(
      { islandId, viewerId },
      { $setOnInsert: { islandId, viewerId, createdAt: new Date() } },
      { upsert: true },
    );

    if (likeResult.upsertedCount) {
      await islands.updateOne({ _id: islandId }, { $inc: { likes: 1 }, $set: { updatedAt: new Date() } });
    }

    const island = await islands.findOne({ _id: islandId });
    if (!island) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    res.json({ island: toIslandResponse(island, user), liked: true });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to like island',
    });
  }
});

app.delete('/api/daybreak/islands/:id', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) {
      return;
    }

    const islandId = new ObjectId(req.params.id);
    const { islands, likes, comments } = await getCollections();
    const island = await islands.findOne({ _id: islandId });
    if (!island) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    if (!userCanManageIsland(user, island)) {
      res.status(403).json({ error: 'You can only delete islands you uploaded.' });
      return;
    }

    const deleteResult = await islands.deleteOne({ _id: islandId });
    if (!deleteResult.deletedCount) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    res.json({ deleted: true, id: islandId.toString() });

    void Promise.allSettled([
      likes.deleteMany({ islandId }),
      comments.deleteMany({ islandId }),
      deleteFromR2(island.objectKey),
    ]).then((results) => {
      const rejected = results.filter((result) => result.status === 'rejected');
      if (rejected.length) {
        console.warn('Island deleted, but cleanup had failures', rejected);
      }
    });
  } catch (error) {
    console.error('Unable to delete island', error);
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to delete island',
      detail: error instanceof Error ? error.message : undefined,
    });
  }
});

app.post('/api/daybreak/islands/:id/share', async (req, res) => {
  try {
    const islandId = new ObjectId(req.params.id);
    const viewer = await getCurrentUser(req).catch(() => null);
    const { islands } = await getCollections();
    const result = await islands.findOneAndUpdate(
      { _id: islandId },
      { $inc: { shares: 1 }, $set: { updatedAt: new Date() } },
      { returnDocument: 'after' },
    );

    if (!result) {
      res.status(404).json({ error: 'Island not found' });
      return;
    }

    res.json({ island: toIslandResponse(result, viewer) });
  } catch (error) {
    res.status(error instanceof Error && error.message.includes('hex string') ? 400 : 500).json({
      error: 'Unable to share island',
    });
  }
});

const errorHandler: ErrorRequestHandler = (error, _req, res, _next) => {
  const message = error instanceof Error ? error.message : 'Request failed';
  const isUploadLimit = message.includes('File too large');
  const isUploadType = message.includes('Only image uploads are supported');
  const isCors = message.includes('Origin is not allowed by CORS');

  res.status(isCors ? 403 : isUploadLimit || isUploadType ? 400 : 500).json({
    error: isCors ? 'Origin is not allowed' : isUploadLimit || isUploadType ? 'Invalid upload' : 'Request failed',
    detail: message,
  });
};

app.use(errorHandler);

app.listen(port, () => {
  console.log(`Server is running on port ${port}`);
});
