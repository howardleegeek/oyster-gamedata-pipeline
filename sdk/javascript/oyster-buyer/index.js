/**
 * @module oyster-buyer
 * @description JavaScript SDK for Oyster Buyer operations.
 *              Provides a programmatic interface for buyers to search,
 *              purchase, and manage orders within the Oyster platform.
 *              Designed for Node.js training-pipeline integration.
 *
 * @version 1.0.0
 * @license MIT
 *
 * @example
 *   const { BuyerClient } = require('./sdk/javascript/oyster-buyer');
 *
 *   const client = new BuyerClient({
 *     baseUrl: process.env.OYSTER_API_URL,
 *     apiKey:  process.env.OYSTER_API_KEY,
 *   });
 *
 *   const results = await client.searchProducts({ query: 'oyster', limit: 20 });
 *   const order   = await client.createOrder({ productId: results[0].id, quantity: 5 });
 */

'use strict';

const https = require('https');
const http  = require('http');
const url   = require('url');

/* ------------------------------------------------------------------ */
/*  Constants                                                         */
/* ------------------------------------------------------------------ */

const DEFAULT_BASE_URL = 'https://api.oyster.example.com/v1';
const DEFAULT_TIMEOUT  = 30_000;  // ms
const DEFAULT_RETRIES  = 3;

/** @enum {string} HTTP methods used by the SDK */
const Methods = { GET: 'GET', POST: 'POST', PUT: 'PUT', DELETE: 'DELETE' };

/* ------------------------------------------------------------------ */
/*  Custom Error Classes                                              */
/* ------------------------------------------------------------------ */

/**
 * Base error for all Oyster Buyer SDK exceptions.
 * @extends Error
 */
class OysterError extends Error {
  /**
   * @param {string} message  Human-readable error message.
   * @param {number} [code]   Optional numeric error code.
   * @param {object} [meta]   Optional metadata (e.g. HTTP status).
   */
  constructor(message, code, meta) {
    super(message);
    this.name = 'OysterError';
    this.code = code ?? null;
    this.meta = meta ?? {};
  }
}

/** Raised when the API returns a 4xx client error. */
class BuyerApiError extends OysterError {
  constructor(message, statusCode, body) {
    super(message, statusCode, { body });
    this.name = 'BuyerApiError';
    this.statusCode = statusCode;
  }
}

/** Raised when a network / transport failure occurs. */
class NetworkError extends OysterError {
  constructor(message, cause) {
    super(message, -1, { cause });
    this.name = 'NetworkError';
  }
}

/** Raised when required configuration is missing. */
class ConfigError extends OysterError {
  constructor(message) {
    super(message, -2);
    this.name = 'ConfigError';
  }
}

/* ------------------------------------------------------------------ */
/*  Internal HTTP helper                                              */
/* ------------------------------------------------------------------ */

/**
 * Perform a single HTTP(S) request and return a parsed JSON body.
 *
 * @private
 * @param {object}  opts
 * @param {string}  opts.method   - HTTP verb.
 * @param {string}  opts.href     - Full URL string.
 * @param {object}  [opts.headers] - Extra headers to merge.
 * @param {object}  [opts.body]    - JSON-serialisable payload.
 * @param {number}  [opts.timeout] - ms before aborting.
 * @returns {Promise<object>} Parsed JSON response.
 */
function _request({ method, href, headers = {}, body, timeout = DEFAULT_TIMEOUT }) {
  return new Promise((resolve, reject) => {
    const parsed = new url.URL(href);
    const lib    = parsed.protocol === 'https:' ? https : http;

    const reqOpts = {
      hostname: parsed.hostname,
      port:     parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path:     parsed.pathname + parsed.search,
      method,
      headers: {
        'Accept':       'application/json',
        'Content-Type': 'application/json',
        ...headers,
      },
      timeout,
    };

    if (body !== undefined) {
      const payload = JSON.stringify(body);
      reqOpts.headers['Content-Length'] = Buffer.byteLength(payload);
    }

    const req = lib.request(reqOpts, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        let parsedBody;
        try { parsedBody = raw ? JSON.parse(raw) : {}; }
        catch { parsedBody = { _raw: raw }; }

        if (res.statusCode >= 200 && res.statusCode < 300) {
          return resolve(parsedBody);
        }
        reject(new BuyerApiError(
          parsedBody.message || parsedBody.error || `HTTP ${res.statusCode}`,
          res.statusCode,
          parsedBody,
        ));
      });
    });

    req.on('timeout', () => { req.destroy(); reject(new NetworkError('Request timed out')); });
    req.on('error',   (err) => reject(new NetworkError(err.message, err)));

    if (body !== undefined) req.write(JSON.stringify(body));
    req.end();
  });
}

/**
 * Retry wrapper with exponential back-off.
 *
 * @private
 * @param {Function} fn       Async function to retry.
 * @param {number}   retries  Max retry attempts.
 * @returns {Promise<*>}
 */
async function _withRetry(fn, retries = DEFAULT_RETRIES) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try { return await fn(); }
    catch (err) {
      lastErr = err;
      if (err instanceof BuyerApiError && err.statusCode < 500) throw err; // non-retryable
      if (attempt < retries) {
        const delay = Math.min(1000 * 2 ** attempt, 10_000);
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  throw lastErr;
}

/* ------------------------------------------------------------------ */
/*  BuyerClient                                                       */
/* ------------------------------------------------------------------ */

/**
 * Main client for buyer-facing operations.
 *
 * @example
 *   const client = new BuyerClient({ apiKey: 'sk-...' });
 *   const products = await client.searchProducts({ query: 'pearl' });
 */
class BuyerClient {
  /**
   * @param {object}  config
   * @param {string}  [config.baseUrl]  API base URL.
   * @param {string}  config.apiKey     Authentication key.
   * @param {number}  [config.timeout]  Request timeout in ms.
   * @param {number}  [config.retries]  Number of retries on transient errors.
   */
  constructor({ baseUrl = DEFAULT_BASE_URL, apiKey, timeout, retries } = {}) {
    if (!apiKey) throw new ConfigError('apiKey is required');
    this.baseUrl  = baseUrl.replace(/\/+$/, '');
    this.apiKey   = apiKey;
    this.timeout  = timeout ?? DEFAULT_TIMEOUT;
    this.retries  = retries ?? DEFAULT_RETRIES;
  }

  /** Build full URL from a path. @private */
  _url(path) { return `${this.baseUrl}${path}`; }

  /** Common auth headers. @private */
  _authHeaders() { return { Authorization: `Bearer ${this.apiKey}` }; }

  /**
   * Generic request helper with retry.
   *
   * @private
   * @param {string} method
   * @param {string} path
   * @param {object} [body]
   * @returns {Promise<object>}
   */
  async _fetch(method, path, body) {
    return _withRetry(
      () => _request({
        method,
        href:    this._url(path),
        headers: this._authHeaders(),
        body,
        timeout: this.timeout,
      }),
      this.retries,
    );
  }

  /* ---- Product catalogue ----------------------------------------- */

  /**
   * Search the product catalogue.
   *
   * @param {object}  params
   * @param {string}  [params.query]   Free-text search term.
   * @param {string}  [params.category] Filter by category slug.
   * @param {number}  [params.limit]   Max results (default 20).
   * @param {number}  [params.offset]  Pagination offset.
   * @param {string}  [params.sort]    Sort field (e.g. "price_asc").
   * @returns {Promise<{ items: object[], total: number }>}
   */
  async searchProducts({ query, category, limit = 20, offset = 0, sort } = {}) {
    const qs = new url.URLSearchParams();
    if (query)    qs.set('query',    query);
    if (category) qs.set('category', category);
    if (limit)    qs.set('limit',    String(limit));
    if (offset)   qs.set('offset',   String(offset));
    if (sort)     qs.set('sort',     sort);
    const path = `/products/search?${qs.toString()}`;
    return this._fetch(Methods.GET, path);
  }

  /**
   * Retrieve a single product by ID.
   *
   * @param {string} productId
   * @returns {Promise<object>}
   */
  async getProduct(productId) {
    return this._fetch(Methods.GET, `/products/${encodeURIComponent(productId)}`);
  }

  /* ---- Orders ---------------------------------------------------- */

  /**
   * Create a new purchase order.
   *
   * @param {object}  params
   * @param {string}  params.productId  Product to purchase.
   * @param {number}  params.quantity   Number of units.
   * @param {string}  [params.shippingAddress]  Delivery address string.
   * @param {object}  [params.metadata]         Arbitrary key-value metadata.
   * @returns {Promise<{ orderId: string, status: string }>}
   */
  async createOrder({ productId, quantity, shippingAddress, metadata } = {}) {
    if (!productId) throw new ConfigError('productId is required for createOrder');
    if (!quantity || quantity < 1) throw new ConfigError('quantity must be >= 1');
    return this._fetch(Methods.POST, '/orders', {
      productId,
      quantity,
      shippingAddress: shippingAddress ?? null,
      metadata:        metadata        ?? {},
    });
  }

  /**
   * Retrieve order details.
   *
   * @param {string} orderId
   * @returns {Promise<object>}
   */
  async getOrder(orderId) {
    return this._fetch(Methods.GET, `/orders/${encodeURIComponent(orderId)}`);
  }

  /**
   * List orders for the authenticated buyer.
   *
   * @param {object}  [params]
   * @param {string}  [params.status]  Filter by order status.
   * @param {number}  [params.limit]   Max results.
   * @param {number}  [params.offset]  Pagination offset.
   * @returns {Promise<{ items: object[], total: number }>}
   */
  async listOrders({ status, limit = 20, offset = 0 } = {}) {
    const qs = new url.URLSearchParams();
    if (status)  qs.set('status',  status);
    if (limit)   qs.set('limit',   String(limit));
    if (offset)  qs.set('offset',  String(offset));
    return this._fetch(Methods.GET, `/orders?${qs.toString()}`);
  }

  /**
   * Cancel a pending order.
   *
   * @param {string} orderId
   * @returns {Promise<{ orderId: string, status: string }>}
   */
  async cancelOrder(orderId) {
    return this._fetch(Methods.DELETE, `/orders/${encodeURIComponent(orderId)}`);
  }

  /* ---- Payments -------------------------------------------------- */

  /**
   * Initiate payment for an order.
   *
   * @param {object}  params
   * @param {string}  params.orderId    Order to pay for.
   * @param {string}  params.method     Payment method token / identifier.
   * @returns {Promise<{ paymentId: string, status: string }>}
   */
  async initiatePayment({ orderId, method }) {
    if (!orderId) throw new ConfigError('orderId is required for initiatePayment');
    if (!method)  throw new ConfigError('payment method is required');
    return this._fetch(Methods.POST, `/orders/${encodeURIComponent(orderId)}/payment`, { method });
  }

  /**
   * Get payment status.
   *
   * @param {string} paymentId
   * @returns {Promise<object>}
   */
  async getPayment(paymentId) {
    return this._fetch(Methods.GET, `/payments/${encodeURIComponent(paymentId)}`);
  }

  /* ---- Buyer profile --------------------------------------------- */

  /**
   * Fetch the authenticated buyer's profile.
   *
   * @returns {Promise<object>}
   */
  async getProfile() {
    return this._fetch(Methods.GET, '/buyer/profile');
  }

  /**
   * Update buyer profile fields.
   *
   * @param {object} fields  Partial profile object to merge.
   * @returns {Promise<object>}
   */
  async updateProfile(fields) {
    return this._fetch(Methods.PUT, '/buyer/profile', fields);
  }
}

/* ------------------------------------------------------------------ */
/*  Exports                                                           */
/* ------------------------------------------------------------------ */

module.exports = {
  BuyerClient,
  OysterError,
  BuyerApiError,
  NetworkError,
  ConfigError,
};
