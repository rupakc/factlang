/* FactLang playground engine: real verdicts in the browser.
 *
 * FactLang runs in Pyodide (glue.py, loaded by playground.js); the SQL it writes runs in
 * DuckDB-WASM in a Web Worker. This module is imported lazily by playground.js only on the
 * playground page, and bridges the two:
 *
 *   Checker.acheck (Python) -> PlaygroundConnector.aexecute -> run(sql, params_json) (here)
 *     -> DESCRIBE + prepared statement in the DuckDB worker -> Arrow -> tagged JSON -> Python
 *
 * The wire format is documented in glue.py: exact values (integers, DECIMAL, dates,
 * timestamps) cross as strings, so a DECIMAL sum never arrives as a scaled integer and a
 * BIGINT never becomes a float. Parameters arrive tagged and are bound into explicit
 * `CAST(? AS <type>)` placeholders that glue.py wrote. The DuckDB type -> value kind table is
 * glue.py's (VALUE_KINDS_JSON), read from Pyodide, so the two sides cannot drift.
 *
 * DuckDB-WASM @duckdb/duckdb-wasm@1.32.0 (DuckDB engine v1.4.3, source id d1dc88f950), from
 * jsDelivr at that exact version. Only the `mvp` / `eh` bundles are used (GitHub Pages cannot
 * send COOP/COEP, so never `coi`/pthreads); the worker starts from a Blob URL that
 * importScripts() the bundle's worker (a cross-origin `new Worker(url)` fails).
 *
 * Hosts this module contacts (the e2e suite asserts nothing else is requested):
 *   cdn.jsdelivr.net       the duckdb-wasm module, apache-arrow, the worker and the .wasm
 *   extensions.duckdb.org  DuckDB's ICU extension, pinned by the engine version
 *                          (https://extensions.duckdb.org/v1.4.3/wasm_<bundle>/icu.duckdb_extension.wasm)
 * ICU is loaded explicitly at boot (`LOAD icu`, then `SET TimeZone = 'UTC'`; the browser's
 * own zone is DuckDB-WASM's default otherwise), so behaviour does not depend on autoload
 * timing; extension autoloading is then switched off. (Measured: DuckDB-WASM still fetches
 * ICU by itself for `timezone(...)` with autoloading off, from the same URL; that is the only
 * extension it fetches. To test the "ICU unavailable" path, block extensions.duckdb.org.) FactLang's
 * DuckDB SQL needs ICU only for `timezone('UTC', current_timestamp)` (FRESH and other
 * "now"-relative claims) and, in this build, `current_date` (the sample's `shipped_at`). If
 * ICU cannot be loaded, those claims come back as ERROR with a message saying so, never as a
 * verdict, and the sample is built with today's UTC date written as a literal instead.
 *
 * Every database operation goes through one queue, so queries run one at a time (the checker
 * also runs with concurrency 1), and every call into the worker has a watchdog: a check query
 * gets the query timeout (30 s), whose clock starts when the query leaves the queue; loading
 * a CSV file gets 120 s; anything else 60 s. A check query past its timeout is rejected with
 * a JS Error named "TimeoutError" (FL702 in Python). A worker that stops answering (watchdog)
 * or fails (its `error` event, e.g. out of memory) rejects all work in flight. In both cases
 * the worker is restarted before the queue moves on: a new worker, the sample rebuilt, and the CSV
 * files still held as `File` objects registered and loaded again. A CheckResult carries
 * `restarted: true` when that happened during the check, so the page can mark other results
 * stale. If a restart fails, the engine says so and every later call rejects at once.
 *
 * Test-only URL parameters (honoured only on localhost / 127.0.0.1; used by the e2e suite):
 *   ?fl-timeout=<seconds>  lower the check timeout (0.05 to 30; it can never be raised)
 *   ?fl-slow=<n>           the first <n> (at most 5) check queries are preceded, in the
 *                          worker, by a deliberately endless statement: the timeout path
 * and, on localhost only, `engine.simulateWorkerError()` fires the worker's `error` event.
 *
 * Interface (consumed by playground.js):
 *   startEngine({ pyodide, onStatus }) -> Promise<Engine>   (idempotent: one engine, one worker)
 *   Engine = { loadSample, loadCsv, reset, tables, check, lint, suggest, terminate, ... }
 */

const DUCKDB_WASM_VERSION = "1.32.0";
const DUCKDB_ESM = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@" + DUCKDB_WASM_VERSION + "/+esm";
const DUCKDB_ENGINE_VERSION = "v1.4.3";
const ALLOWED_HOSTS = Object.freeze(["cdn.jsdelivr.net", "extensions.duckdb.org"]);

const MB = 1024 * 1024;
const LIMITS = Object.freeze({ fileBytes: 100 * MB, totalBytes: 200 * MB, files: 10 });
const DEFAULT_TIMEOUT_S = 30;
const MAX_SLOW = 5;
const SLOW_SQL = "SELECT SUM(i % 7) FROM range(100000000000000) AS t(i)"; // never overflows
// watchdogs (ms)
const BOOT_MS = 300000; // instantiate downloads ~35 MB of wasm
const CSV_MS = 120000;
const OP_MS = 60000;
const PING_MS = 5000;
// Python's own limit is timeout + this margin: only a backstop, the timer here is the clock
const PY_MARGIN_S = 2 * BOOT_MS / 1000;

// Arrow type ids (apache-arrow 17 `Type`), as the duckdb-wasm `+esm` build uses them.
const ARROW = { Null: 1, Int: 2, Float: 3, Utf8: 5, Bool: 6, Decimal: 7, Date: 8, Time: 9, Timestamp: 10, LargeUtf8: 20 };
const DATE_UNIT_DAY = 0;
const TIME_UNIT_TO_MICROS = [1000000n, 1000n, 1n, null]; // SECOND, MILLISECOND, MICROSECOND, NANOSECOND
const MICROS_PER_DAY = 86400000000n;
// how DuckDB exports its infinite DATE / TIMESTAMP values through Arrow
const DATE_INFINITY = 2147483647;
const TIMESTAMP_INFINITY = 9223372036854775807n;

let current = null; // { pyodide, promise, engine, onStatus }

function noop() {}

function testParams() {
  var out = { timeoutS: DEFAULT_TIMEOUT_S, slow: 0, local: false };
  try {
    var host = window.location.hostname;
    if (host !== "localhost" && host !== "127.0.0.1") return out;
    out.local = true;
    var query = new URLSearchParams(window.location.search);
    var t = parseFloat(query.get("fl-timeout"));
    if (isFinite(t) && t > 0) out.timeoutS = Math.min(DEFAULT_TIMEOUT_S, Math.max(0.05, t));
    var s = parseInt(query.get("fl-slow"), 10);
    if (isFinite(s) && s > 0) out.slow = Math.min(MAX_SLOW, s);
  } catch (e) { /* no window or URL: defaults */ }
  return out;
}

// -- SQL text helpers -----------------------------------------------------------------------

function ident(name) {
  return '"' + String(name).replace(/"/g, '""') + '"';
}

function literal(text) {
  return "'" + String(text).replace(/'/g, "''") + "'";
}

// -- Arrow cell -> tagged JSON (the rules glue.encode_cell states) ---------------------------

function kindTable(json) {
  return JSON.parse(json).map(function (entry) {
    return [new RegExp("^(?:" + entry[0] + ")$"), entry[1]];
  });
}

function pad(n, width) {
  var s = String(n);
  while (s.length < width) s = "0" + s;
  return s;
}

// A two's-complement little-endian integer held in 32-bit words (an Arrow Decimal cell, or a
// HUGEINT that DuckDB exports as DECIMAL(38,0)) as decimal text with `scale` fraction digits.
function wordsToDecimal(words, scale) {
  var v = 0n;
  for (var k = words.length - 1; k >= 0; k--) v = (v << 32n) | BigInt(words[k] >>> 0);
  if (words[words.length - 1] & 0x80000000) v -= 1n << BigInt(32 * words.length);
  var negative = v < 0n;
  var digits = (negative ? -v : v).toString();
  if (scale > 0) {
    while (digits.length < scale + 1) digits = "0" + digits;
    digits = digits.slice(0, digits.length - scale) + "." + digits.slice(digits.length - scale);
  }
  return (negative ? "-" : "") + digits;
}

function isoDate(days) {
  var d = new Date(Number(days) * 86400000);
  if (isNaN(d.getTime())) throw new Error("date out of range: " + days + " days");
  return pad(d.getUTCFullYear(), 4) + "-" + pad(d.getUTCMonth() + 1, 2) + "-" + pad(d.getUTCDate(), 2);
}

function isoTimeOfDay(micros) {
  var sec = micros / 1000000n;
  var frac = micros % 1000000n;
  var text = pad(sec / 3600n, 2) + ":" + pad((sec % 3600n) / 60n, 2) + ":" + pad(sec % 60n, 2);
  return frac === 0n ? text : text + "." + pad(frac, 6);
}

function floorDiv(a, b) {
  var q = a / b;
  if ((a % b !== 0n) && ((a < 0n) !== (b < 0n))) q -= 1n;
  return q;
}

function toMicros(raw, unit) {
  var big = BigInt(raw);
  var factor = TIME_UNIT_TO_MICROS[unit];
  return factor === null ? floorDiv(big, 1000n) : big * factor;
}

function isoTimestamp(micros, withZone) {
  var days = floorDiv(micros, MICROS_PER_DAY);
  var rest = micros - days * MICROS_PER_DAY;
  return isoDate(days) + "T" + isoTimeOfDay(rest) + (withZone ? "+00:00" : "");
}

function plainText(value) {
  if (typeof value === "bigint") return value.toString();
  if (value !== null && typeof value === "object" && typeof value.toJSON === "function") {
    try { return JSON.stringify(value, function (k, v) { return typeof v === "bigint" ? v.toString() : v; }); } catch (e) { /* fall through */ }
  }
  return String(value);
}

function encodeCell(vector, data, i, globalIndex, kind) {
  var type = data.type;
  var id = type.typeId;
  if (id === ARROW.Null || kind === "null" || !data.getValid(i)) return { t: "null", v: null };
  if (kind === "integer") {
    if (id === ARROW.Int) return { t: kind, v: data.values[i].toString() };
    if (id === ARROW.Decimal) return { t: kind, v: wordsToDecimal(data.values.subarray(data.stride * i, data.stride * (i + 1)), 0) };
  } else if (kind === "decimal") {
    if (id === ARROW.Decimal) return { t: kind, v: wordsToDecimal(data.values.subarray(data.stride * i, data.stride * (i + 1)), type.scale) };
  } else if (kind === "float") {
    if (id === ARROW.Float) {
      var x = Number(data.values[i]);
      if (isNaN(x)) return { t: kind, v: "nan" };
      if (!isFinite(x)) return { t: kind, v: x > 0 ? "inf" : "-inf" };
      return { t: kind, v: x };
    }
  } else if (kind === "string") {
    if (id === ARROW.Utf8 || id === ARROW.LargeUtf8) return { t: kind, v: vector.get(globalIndex) };
  } else if (kind === "boolean") {
    if (id === ARROW.Bool) return { t: kind, v: !!vector.get(globalIndex) };
  } else if (kind === "date") {
    if (id === ARROW.Date) {
      var days = type.unit === DATE_UNIT_DAY ? data.values[i] : Number(floorDiv(BigInt(data.values[i]), 86400000n));
      if (days === DATE_INFINITY) return { t: kind, v: "infinity" };
      if (days === -DATE_INFINITY) return { t: kind, v: "-infinity" };
      return { t: kind, v: isoDate(days) };
    }
  } else if (kind === "timestamp") {
    if (id === ARROW.Timestamp) {
      var raw = BigInt(data.values[i]);
      if (raw === TIMESTAMP_INFINITY) return { t: kind, v: "infinity" };
      if (raw === -TIMESTAMP_INFINITY) return { t: kind, v: "-infinity" };
      return { t: kind, v: isoTimestamp(toMicros(raw, type.unit), !!type.timezone) };
    }
  } else if (kind === "time") {
    if (id === ARROW.Time) return { t: kind, v: isoTimeOfDay(toMicros(data.values[i], type.unit)) };
  }
  // anything else (lists, structs, intervals, blobs, or an Arrow type the kind did not
  // expect) crosses as text; Python shows it as it is and never computes with it
  return { t: "other", v: plainText(vector.get(globalIndex)) };
}

function encodeTable(table, columns, kindOf) {
  var width = table.numCols;
  var rows = [];
  for (var r = 0; r < table.numRows; r++) rows.push(new Array(width));
  for (var c = 0; c < width; c++) {
    var vector = table.getChildAt(c);
    var kind = kindOf(columns[c] ? columns[c][1] : "");
    var offset = 0;
    vector.data.forEach(function (data) {
      for (var i = 0; i < data.length; i++) rows[offset + i][c] = encodeCell(vector, data, i, offset + i, kind);
      offset += data.length;
    });
  }
  return rows;
}

function columnValues(table, name) {
  var vector = table.getChild(name);
  var out = [];
  for (var i = 0; i < table.numRows; i++) out.push(vector.get(i));
  return out;
}

function timeoutError(seconds) {
  var err = new Error("query timed out after " + seconds + "s");
  err.name = "TimeoutError";
  return err;
}

function workerError(message) {
  var err = new Error(message);
  err.workerDown = true;
  return err;
}

function messageOf(err) {
  return String((err && err.message) || err).replace(/^Error:\s*/, "");
}

function friendly(err, fileName) {
  var message = messageOf(err);
  if (/out of memory|memory limit|failed to allocate|allocation|Aborted\(|RangeError/i.test(message)) {
    return "There is not enough memory in this browser tab to load " + fileName +
      ". Try a smaller file, or reset the loaded files first.";
  }
  if (err && err.workerDown) {
    return "The database stopped while loading " + fileName + " (" + message + "); it was restarted without this file.";
  }
  return message;
}

// -- the engine -----------------------------------------------------------------------------

function createEngine(pyodide, duckdb, setStatus) {
  var params = testParams();
  var timeoutS = params.timeoutS;
  var slowLeft = params.slow;
  var kinds = kindTable(pyodide.globals.get("VALUE_KINDS_JSON"));
  var sampleSql = JSON.parse(pyodide.globals.get("SAMPLE_SQL_JSON"));
  var db = null;
  var conn = null;
  var life = null; // the running worker's { failed, fail(err), promise }: one per boot
  var bundleName = null;
  var version = null;
  var icu = { loaded: false, url: null, error: null };
  var chain = Promise.resolve();
  var sampleTables = [];
  var csv = []; // { name, file, internal, info }
  var seq = 0;
  var restarts = 0;
  var dead = null; // the reason, once a restart has failed

  function kindOf(duckType) {
    var name = String(duckType).trim().toUpperCase();
    for (var i = 0; i < kinds.length; i++) if (kinds[i][0].test(name)) return kinds[i][1];
    return "other";
  }

  function serial(task) {
    var result = chain.then(function () { return task(); });
    chain = result.then(noop, noop);
    return result;
  }

  function py(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    var fn = pyodide.globals.get(name);
    if (!fn) throw new Error("glue.py has no " + name);
    try { return fn.apply(null, args); } finally { fn.destroy(); }
  }

  async function pyAsync(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    var fn = pyodide.globals.get(name);
    if (!fn) throw new Error("glue.py has no " + name);
    var awaitable = null;
    try {
      awaitable = fn.apply(null, args); // a PyProxy of the coroutine; awaiting runs it on Pyodide's loop
      return await awaitable;
    } finally {
      fn.destroy();
      if (awaitable && typeof awaitable.destroy === "function") {
        try { awaitable.destroy(); } catch (e) { /* already released */ }
      }
    }
  }

  function newLife() {
    var state = { failed: null, fail: null, promise: null };
    state.promise = new Promise(function (_, reject) {
      state.fail = function (err) {
        if (state.failed) return;
        state.failed = err;
        reject(err);
      };
    });
    state.promise.catch(noop);
    return state;
  }

  // `promise`, or a rejection when the worker fails or `ms` pass without an answer (the
  // watchdog: duckdb-wasm drops pending requests on a worker error without settling them).
  function guard(promise, ms, what, state) {
    var lifeNow = state || life;
    if (!lifeNow) return Promise.reject(workerError("the database is not running"));
    if (lifeNow.failed) return Promise.reject(lifeNow.failed);
    var timer = null;
    var watchdog = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        var err = workerError("the database did not answer within " + Math.round(ms / 1000) + "s (" + what + ")");
        lifeNow.fail(err);
        reject(err);
      }, ms);
    });
    Promise.resolve(promise).catch(noop);
    return Promise.race([promise, lifeNow.promise, watchdog]).finally(function () { clearTimeout(timer); });
  }

  async function loadIcu(c, state) {
    icu.loaded = false;
    icu.error = null;
    try {
      await guard(c.query("LOAD icu"), OP_MS, "loading the ICU extension", state);
      await guard(c.query("SET TimeZone = 'UTC'"), OP_MS, "setting the time zone", state);
      icu.loaded = true;
    } catch (err) {
      if (err && err.workerDown) throw err;
      icu.error = messageOf(err).split("\n")[0];
    }
    // nothing loads behind our back from here on: no autoload timing, no other host
    await guard(c.query("SET autoinstall_known_extensions = false"), OP_MS, "settings", state);
    await guard(c.query("SET autoload_known_extensions = false"), OP_MS, "settings", state);
  }

  async function boot() {
    var all = duckdb.getJsDelivrBundles();
    var bundle = await duckdb.selectBundle({ mvp: all.mvp, eh: all.eh }); // never coi
    var name = bundle.mainModule === all.eh.mainModule ? "eh" : "mvp";
    var workerUrl = URL.createObjectURL(
      new Blob(["importScripts(" + JSON.stringify(bundle.mainWorker) + ");"], { type: "text/javascript" })
    );
    var state = newLife();
    var worker = new Worker(workerUrl);
    state.worker = worker;
    worker.addEventListener("error", function (ev) {
      state.fail(workerError("the database worker failed: " + ((ev && ev.message) || "unknown error")));
    });
    var fresh = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
    try {
      await guard(fresh.instantiate(bundle.mainModule, bundle.pthreadWorker), BOOT_MS, "starting DuckDB", state);
      await guard(fresh.open({
        query: { castBigIntToDouble: false, castDecimalToDouble: false },
        arrowLosslessConversion: false
      }), OP_MS, "opening the database", state);
      var c = await guard(fresh.connect(), OP_MS, "connecting", state);
      var v = await guard(fresh.getVersion(), OP_MS, "reading the version", state);
      icu.url = "https://extensions.duckdb.org/" + v + "/wasm_" + name + "/icu.duckdb_extension.wasm";
      await loadIcu(c, state);
      db = fresh;
      conn = c;
      life = state;
      version = v;
      bundleName = name;
    } catch (err) {
      state.fail(err);
      try { fresh.terminate().catch(noop); } catch (e) { /* gone */ }
      try { worker.terminate(); } catch (e) { /* gone */ }
      throw err;
    } finally {
      URL.revokeObjectURL(workerUrl);
    }
  }

  function q(sql, ms, what) {
    return guard(conn.query(sql), ms || OP_MS, what || "a query");
  }

  async function prepared(sql, values, ms, what) {
    if (!values || !values.length) return q(sql, ms, what);
    var stmt = await guard(conn.prepare(sql), ms, what);
    try {
      return await guard(stmt.query.apply(stmt, values), ms, what);
    } finally {
      stmt.close().catch(noop);
    }
  }

  async function describeTable(name, source) {
    var described = await q("DESCRIBE " + ident(name), OP_MS, "describing " + name);
    var names = columnValues(described, "column_name");
    var types = columnValues(described, "column_type");
    var counted = await q("SELECT COUNT(*) AS n FROM " + ident(name), CSV_MS, "counting " + name);
    var rows = Number(columnValues(counted, "n")[0]);
    var columns = names.map(function (n, i) { return [String(n), String(types[i])]; });
    return JSON.parse(py("fl_table_info", name, rows, JSON.stringify(columns), source));
  }

  // The sample SQL verbatim. In DuckDB-WASM 1.4.3 `current_date` itself lives in ICU, so
  // without ICU it is replaced by today's UTC date as a literal: the same value, because
  // with ICU the session time zone is UTC.
  function sampleStatements() {
    if (icu.loaded) return sampleSql;
    var today = new Date().toISOString().slice(0, 10);
    return sampleSql.map(function (sql) { return sql.replace(/\bcurrent_date\b/g, "DATE '" + today + "'"); });
  }

  async function buildSample() {
    var names = [];
    var statements = sampleStatements();
    for (var i = 0; i < statements.length; i++) {
      await q(statements[i], OP_MS, "building the sample shop");
      var m = /^CREATE TABLE\s+(\w+)/i.exec(statements[i]);
      if (m) names.push(m[1]);
    }
    return names;
  }

  async function loadCsvTable(entry) {
    await guard(db.registerFileHandle(entry.internal, entry.file, duckdb.DuckDBDataProtocol.BROWSER_FILEREADER, true), OP_MS, "registering " + entry.file.name);
    await q("CREATE TABLE " + ident(entry.name) + " AS SELECT * FROM read_csv_auto(" + literal(entry.internal) + ")", CSV_MS, "loading " + entry.file.name);
  }

  async function restart() {
    setStatus("The database stopped or ran too long; restarting it…");
    var old = db;
    db = null;
    conn = null;
    if (life) life.fail(workerError("the database was restarted"));
    life = null;
    if (old) { try { await Promise.race([old.terminate(), new Promise(function (r) { setTimeout(r, PING_MS); })]); } catch (e) { /* gone */ } }
    try {
      await boot();
      if (sampleTables.length) await buildSample();
      var kept = [];
      for (var i = 0; i < csv.length; i++) {
        try { await loadCsvTable(csv[i]); kept.push(csv[i]); } catch (e) {
          if (e && e.workerDown) throw e;
        }
      }
      csv = kept;
      restarts += 1;
      setStatus("");
    } catch (err) {
      dead = "the database could not be restarted (" + messageOf(err) + "); reload the page";
      setStatus("The database could not be restarted: " + messageOf(err) + ". Reload the page to try again.");
      throw err;
    }
  }

  // Restart from inside a queued task, so nothing queued behind it reaches the old worker. A
  // failed restart sets `dead` and is reported by every later call; it never replaces the
  // error that caused it (a timeout stays FL702).
  async function restartQuietly() {
    try { await restart(); } catch (e) { /* `dead` says why */ }
  }

  async function alive() {
    if (!conn || !life || life.failed) return false;
    try { await guard(conn.query("SELECT 1"), PING_MS, "ping"); return true; } catch (e) { return false; }
  }

  function ready() {
    if (dead) throw new Error(dead);
    if (!conn) throw new Error("the database is not running; reload the page");
  }

  function icuUnavailable() {
    return new Error(
      "this claim needs DuckDB's ICU extension (time zones), which could not be loaded from " +
      "extensions.duckdb.org (" + (icu.error || "unknown error") + "), so it was not measured"
    );
  }

  function icuMessage(err) {
    if (icu.loaded || !/time ?zone|\bicu\b/i.test(messageOf(err))) return err;
    return icuUnavailable();
  }

  // The bridge glue.PlaygroundConnector awaits: run(sql, params_json) -> Promise<JSON string>.
  // `slowHook` (check queries only) spends the test-only fl-slow budget.
  function runQuery(sql, paramsJson, slowHook) {
    return serial(async function () {
      ready();
      // Without ICU, DuckDB-WASM would try to fetch it again for timezone(), and a failed
      // fetch leaves the instance unreliable: refuse such a query before it reaches the worker.
      if (!icu.loaded && /\btimezone\s*\(/i.test(sql)) throw icuUnavailable();
      var values = JSON.parse(paramsJson || "[]").map(function (p) { return p.v; });
      var slow = slowHook && slowLeft > 0;
      if (slow) slowLeft -= 1;
      var ms = timeoutS * 1000;
      var work = (async function () {
        if (slow) await q(SLOW_SQL, ms + OP_MS, "the test-only slow query");
        var described = await prepared("DESCRIBE " + sql, values, ms + OP_MS, "describing a probe");
        var names = columnValues(described, "column_name");
        var types = columnValues(described, "column_type");
        var columns = names.map(function (n, i) { return [String(n), String(types[i])]; });
        var table = await prepared(sql, values, ms + OP_MS, "running a probe");
        return JSON.stringify({ columns: columns, rows: encodeTable(table, columns, kindOf) });
      })();
      work.catch(noop); // after a timeout nobody waits for it
      var timer = null;
      var limit = new Promise(function (_, reject) {
        timer = setTimeout(function () { reject(timeoutError(timeoutS)); }, ms);
      });
      try {
        return await Promise.race([work, limit]);
      } catch (err) {
        // the next query's clock starts after the restart (Python's own limit is a backstop)
        // a wasm RuntimeError (trap) leaves the instance unreliable even if it still answers
        var trapped = /RuntimeError/.test(messageOf(err));
        if ((err && err.name === "TimeoutError") || (err && err.workerDown) || trapped || !(await alive())) await restartQuietly();
        throw icuMessage(err);
      } finally {
        clearTimeout(timer);
      }
    });
  }

  function runForCheck(sql, paramsJson) {
    return runQuery(sql, paramsJson, true);
  }

  // run a queued task; a worker failure inside it queues a restart and says so
  function task(fn) {
    return serial(async function () {
      ready();
      try {
        return await fn();
      } catch (err) {
        if (err && err.workerDown) await restartQuietly();
        throw err;
      }
    });
  }

  function allTables() {
    return sampleTables.concat(csv.map(function (e) { return e.info; }));
  }

  function catalogJson() {
    return py("fl_catalog", JSON.stringify(allTables()));
  }

  var engine = {
    version: null,
    duckdbWasm: DUCKDB_WASM_VERSION,
    allowedHosts: ALLOWED_HOSTS,
    limits: LIMITS,
    timeoutS: timeoutS,
    get bundle() { return bundleName; },
    get restarts() { return restarts; },
    // { loaded, url, error }: whether ICU (time zones) is available, and from where
    get icu() { return { loaded: icu.loaded, url: icu.url, error: icu.error }; },

    loadSample: function () {
      return task(async function () {
        if (!sampleTables.length) {
          var names = await buildSample();
          var infos = [];
          for (var i = 0; i < names.length; i++) infos.push(await describeTable(names[i], "sample"));
          sampleTables = infos;
        }
        return allTables();
      });
    },

    loadCsv: function (files) {
      var list = Array.prototype.slice.call(files || []);
      return serial(async function () {
        ready();
        var tables = [];
        var errors = [];
        var total = csv.reduce(function (n, e) { return n + e.file.size; }, 0);
        for (var i = 0; i < list.length; i++) {
          var file = list[i];
          if (dead) { errors.push({ file: file.name, message: dead }); continue; }
          if (csv.length >= LIMITS.files) {
            errors.push({ file: file.name, message: "At most " + LIMITS.files + " CSV files can be loaded; reset to load others." });
            continue;
          }
          if (file.size > LIMITS.fileBytes) {
            errors.push({ file: file.name, message: "The file is larger than 100 MB, the limit for one file." });
            continue;
          }
          if (total + file.size > LIMITS.totalBytes) {
            errors.push({ file: file.name, message: "Loading it would pass 200 MB of CSV data in total; reset first." });
            continue;
          }
          var names = allTables().map(function (t) { return t.name; });
          var entry = { name: py("table_name_for", file.name, JSON.stringify(names)), file: file, internal: "fl_csv_" + (++seq) + ".csv", info: null };
          try {
            await loadCsvTable(entry);
            entry.info = await describeTable(entry.name, "csv");
            csv.push(entry);
            tables.push(entry.info);
            total += file.size;
          } catch (err) {
            errors.push({ file: file.name, message: friendly(err, file.name) });
            if (await alive()) {
              try { await q("DROP TABLE IF EXISTS " + ident(entry.name)); } catch (e) { /* not created */ }
              try { await guard(db.dropFile(entry.internal), OP_MS, "dropping a file"); } catch (e) { /* not registered */ }
            } else {
              await restartQuietly();
            }
          }
        }
        return { tables: tables, errors: errors };
      });
    },

    reset: function () {
      return task(async function () {
        for (var i = 0; i < csv.length; i++) {
          try { await q("DROP TABLE IF EXISTS " + ident(csv[i].name)); } catch (e) { if (e && e.workerDown) throw e; }
          try { await guard(db.dropFile(csv[i].internal), OP_MS, "dropping a file"); } catch (e) { if (e && e.workerDown) throw e; }
        }
        csv = [];
        return allTables();
      });
    },

    tables: function () {
      return allTables().slice();
    },

    // CheckResult = glue.fl_check's JSON + { restarted, engine: "duckdb", timeoutS }
    check: async function (text) {
      ready();
      var before = restarts;
      var out = JSON.parse(await pyAsync("fl_check", String(text), runForCheck, catalogJson(), timeoutS, PY_MARGIN_S));
      out.restarted = restarts > before;
      out.engine = "duckdb";
      out.timeoutS = timeoutS;
      return out;
    },

    // LintResult = glue.fl_lint's JSON, with the loaded tables as the catalog
    lint: async function (text) {
      return JSON.parse(py("fl_lint_with", String(text), catalogJson()));
    },

    suggest: async function (table) {
      var name = typeof table === "string" ? table : table && table.name;
      var info = allTables().filter(function (t) { return t.name === name; })[0];
      if (!info) throw new Error("no loaded table named " + name);
      var tableJson = JSON.stringify(info);
      var sqls = JSON.parse(py("fl_profile_sql", tableJson));
      var profiles = [];
      for (var i = 0; i < sqls.length; i++) profiles.push(JSON.parse(await runQuery(sqls[i], "[]", false)));
      return JSON.parse(py("fl_suggest", tableJson, JSON.stringify(profiles)));
    },

    terminate: function () {
      dead = "the database was shut down";
      if (current && current.engine === engine) current = null;
      var old = db;
      db = null;
      conn = null;
      if (life) life.fail(workerError("the database was shut down"));
      if (old) old.terminate().catch(noop);
    }
  };

  if (params.local) {
    // test-only (localhost): fire the worker's `error` event as a crash would. duckdb-wasm
    // then drops its pending requests without settling them; the engine must not wedge.
    engine.simulateWorkerError = function () {
      if (life && life.worker) life.worker.dispatchEvent(new ErrorEvent("error", { message: "simulated worker failure" }));
    };
  }

  return boot().then(function () {
    engine.version = version;
    return engine;
  });
}

/** Start (once) the DuckDB side of the playground and load the sample shop.
 *
 * `pyodide` is the ready Pyodide instance with glue.py already run; `onStatus(message)` gets
 * progress text ("" when idle). Calling it again returns the same engine (the latest
 * `onStatus` wins), so Material's instant navigation never starts a second worker. A failed
 * start is forgotten, so calling again retries. */
export async function startEngine(options) {
  var opts = options || {};
  var pyodide = opts.pyodide;
  if (!pyodide) throw new TypeError("startEngine needs the ready Pyodide instance");
  if (current && current.pyodide === pyodide) {
    if (opts.onStatus) current.onStatus = opts.onStatus;
    return current.promise;
  }
  if (current && current.engine) current.engine.terminate();
  var state = { pyodide: pyodide, onStatus: opts.onStatus || null, engine: null, promise: null };
  var setStatus = function (message) {
    if (typeof state.onStatus === "function") {
      try { state.onStatus(message); } catch (e) { /* the page's problem, not the engine's */ }
    }
  };
  current = state;
  state.promise = (async function () {
    setStatus("Starting DuckDB in your browser…");
    var duckdb;
    try {
      duckdb = await import(DUCKDB_ESM);
    } catch (err) {
      throw new Error("could not download DuckDB from the CDN (" + messageOf(err) + ")");
    }
    var engine = await createEngine(pyodide, duckdb, setStatus);
    state.engine = engine;
    setStatus("Loading the sample shop…");
    await engine.loadSample();
    setStatus("");
    return engine;
  })();
  state.promise.catch(function () {
    if (current === state) current = null;
    if (state.engine) state.engine.terminate();
  });
  return state.promise;
}

export { ALLOWED_HOSTS, DUCKDB_ENGINE_VERSION, DUCKDB_WASM_VERSION, LIMITS };
