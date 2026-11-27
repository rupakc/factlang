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
 * `CAST(? AS <type>)` placeholders that glue.py wrote.
 *
 * DuckDB-WASM @duckdb/duckdb-wasm@1.32.0 (DuckDB engine v1.4.3, source id d1dc88f950), from
 * jsDelivr at that exact version. Only the `mvp` / `eh` bundles are used (GitHub Pages cannot
 * send COOP/COEP, so never `coi`/pthreads); the worker starts from a Blob URL that
 * importScripts() the bundle's worker (a cross-origin `new Worker(url)` fails).
 *
 * Every database operation goes through one queue, so queries run one at a time (the checker
 * also runs with concurrency 1). A check query that runs past the timeout (30 s) is rejected
 * with a JS Error named "TimeoutError" (FL702 in Python), the worker is terminated and a new
 * one is booted: the sample is rebuilt and the CSV files still held as `File` objects are
 * registered and loaded again. The CheckResult then carries `restarted: true` so the page can
 * mark other results stale.
 *
 * Test-only URL parameters (read once, from the page's query string; used by the e2e suite):
 *   ?fl-timeout=<seconds>  lower the check timeout (0.05 to 30; it can never be raised)
 *   ?fl-slow=<n>           the first <n> check queries are preceded, in the worker, by a
 *                          deliberately endless statement, so they hit the timeout path
 *
 * Interface (consumed by playground.js):
 *   startEngine({ pyodide, onStatus }) -> Promise<Engine>   (idempotent: one engine, one worker)
 *   Engine = { loadSample, loadCsv, reset, tables, check, lint, suggest, terminate, ... }
 */

const DUCKDB_WASM_VERSION = "1.32.0";
const DUCKDB_ESM = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@" + DUCKDB_WASM_VERSION + "/+esm";
const DUCKDB_ENGINE_VERSION = "v1.4.3";

const MB = 1024 * 1024;
const LIMITS = Object.freeze({ fileBytes: 100 * MB, totalBytes: 200 * MB, files: 10 });
const DEFAULT_TIMEOUT_S = 30;
const SLOW_SQL = "SELECT SUM((i * i) % 7) FROM range(100000000000000) AS t(i)";

// Arrow type ids (apache-arrow 17 `Type`), as the duckdb-wasm `+esm` build uses them.
const ARROW = { Int: 2, Float: 3, Utf8: 5, Bool: 6, Decimal: 7, Date: 8, Time: 9, Timestamp: 10, LargeUtf8: 20 };
const DATE_UNIT_DAY = 0;
const TIME_UNIT_TO_MICROS = [1000000n, 1000n, 1n, null]; // SECOND, MILLISECOND, MICROSECOND, NANOSECOND
const MICROS_PER_DAY = 86400000000n;

let current = null; // { pyodide, promise, engine, setStatus }

function noop() {}

function testParams() {
  var out = { timeoutS: DEFAULT_TIMEOUT_S, slow: 0 };
  try {
    var query = new URLSearchParams(window.location.search);
    var t = parseFloat(query.get("fl-timeout"));
    if (isFinite(t) && t > 0) out.timeoutS = Math.min(DEFAULT_TIMEOUT_S, Math.max(0.05, t));
    var s = parseInt(query.get("fl-slow"), 10);
    if (isFinite(s) && s > 0) out.slow = s;
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

function valueKind(duckType) {
  var t = String(duckType).trim().toUpperCase();
  if (/^U?(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)$/.test(t)) return "integer";
  if (/^(DECIMAL|NUMERIC)(\s*\(.*\))?$/.test(t)) return "decimal";
  if (/^(DOUBLE|FLOAT|REAL)$/.test(t)) return "float";
  if (/^VARCHAR(\s*\(.*\))?$/.test(t)) return "string";
  if (t === "BOOLEAN") return "boolean";
  if (t === "DATE") return "date";
  if (/^TIMESTAMP(_S|_MS|_NS| WITH TIME ZONE|TZ)?$/.test(t)) return "timestamp";
  if (t === "TIME") return "time";
  return "other";
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
  if (!data.getValid(i)) return { t: "null", v: null };
  var type = data.type;
  var id = type.typeId;
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
      var days = type.unit === DATE_UNIT_DAY ? data.values[i] : floorDiv(BigInt(data.values[i]), 86400000n);
      return { t: kind, v: isoDate(days) };
    }
  } else if (kind === "timestamp") {
    if (id === ARROW.Timestamp) return { t: kind, v: isoTimestamp(toMicros(data.values[i], type.unit), !!type.timezone) };
  } else if (kind === "time") {
    if (id === ARROW.Time) return { t: kind, v: isoTimeOfDay(toMicros(data.values[i], type.unit)) };
  }
  // anything else (lists, structs, intervals, blobs, or an Arrow type the kind did not
  // expect) crosses as text; Python shows it as it is and never computes with it
  return { t: "other", v: plainText(vector.get(globalIndex)) };
}

function encodeTable(table, columns) {
  var width = table.numCols;
  var rows = [];
  for (var r = 0; r < table.numRows; r++) rows.push(new Array(width));
  for (var c = 0; c < width; c++) {
    var vector = table.getChildAt(c);
    var kind = valueKind(columns[c] ? columns[c][1] : "");
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

function friendly(err, fileName) {
  var message = String((err && err.message) || err);
  if (/out of memory|memory limit|failed to allocate|allocation|Aborted\(|RangeError/i.test(message)) {
    return "There is not enough memory in this browser tab to load " + fileName +
      ". Try a smaller file, or reset the loaded files first.";
  }
  return message.replace(/^Error:\s*/, "");
}

// -- the engine -----------------------------------------------------------------------------

function createEngine(pyodide, duckdb, setStatus) {
  var params = testParams();
  var timeoutS = params.timeoutS;
  var slowLeft = params.slow;
  var db = null;
  var conn = null;
  var bundleName = null;
  var version = null;
  var chain = Promise.resolve();
  var sampleTables = [];
  var csv = []; // { name, file, internal, info }
  var seq = 0;
  var restarts = 0;
  var dead = false;
  var sampleSql = JSON.parse(pyodide.globals.get("SAMPLE_SQL_JSON"));

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

  async function boot() {
    var all = duckdb.getJsDelivrBundles();
    var bundle = await duckdb.selectBundle({ mvp: all.mvp, eh: all.eh }); // never coi
    bundleName = bundle.mainModule === all.eh.mainModule ? "eh" : "mvp";
    var workerUrl = URL.createObjectURL(
      new Blob(["importScripts(" + JSON.stringify(bundle.mainWorker) + ");"], { type: "text/javascript" })
    );
    try {
      var fresh = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
      await fresh.instantiate(bundle.mainModule, bundle.pthreadWorker);
      await fresh.open({
        query: { castBigIntToDouble: false, castDecimalToDouble: false },
        arrowLosslessConversion: false
      });
      db = fresh;
      conn = await db.connect();
      version = await db.getVersion();
    } finally {
      URL.revokeObjectURL(workerUrl);
    }
  }

  async function query(sql, values) {
    if (!values || !values.length) return conn.query(sql);
    var stmt = await conn.prepare(sql);
    try {
      return await stmt.query.apply(stmt, values);
    } finally {
      stmt.close().catch(noop);
    }
  }

  async function describeTable(name, source) {
    var described = await conn.query("DESCRIBE " + ident(name));
    var names = columnValues(described, "column_name");
    var types = columnValues(described, "column_type");
    var counted = await conn.query("SELECT COUNT(*) AS n FROM " + ident(name));
    var rows = Number(columnValues(counted, "n")[0]);
    var columns = names.map(function (n, i) { return [String(n), String(types[i])]; });
    return JSON.parse(py("fl_table_info", name, rows, JSON.stringify(columns), source));
  }

  async function buildSample() {
    var names = [];
    for (var i = 0; i < sampleSql.length; i++) {
      await conn.query(sampleSql[i]);
      var m = /^CREATE TABLE\s+(\w+)/i.exec(sampleSql[i]);
      if (m) names.push(m[1]);
    }
    return names;
  }

  async function loadCsvTable(entry) {
    await db.registerFileHandle(entry.internal, entry.file, duckdb.DuckDBDataProtocol.BROWSER_FILEREADER, true);
    await conn.query("CREATE TABLE " + ident(entry.name) + " AS SELECT * FROM read_csv_auto(" + literal(entry.internal) + ")");
  }

  async function restart() {
    setStatus("A query ran too long; restarting the database…");
    var old = db;
    db = null;
    conn = null;
    if (old) { try { await old.terminate(); } catch (e) { /* already gone */ } }
    try {
      await boot();
      if (sampleTables.length) await buildSample();
      var kept = [];
      for (var i = 0; i < csv.length; i++) {
        try { await loadCsvTable(csv[i]); kept.push(csv[i]); } catch (e) { /* the file is gone */ }
      }
      csv = kept;
      restarts += 1;
      setStatus("");
    } catch (err) {
      dead = true;
      setStatus("The database could not be restarted: " + (err.message || err) + ". Reload the page.");
      throw err;
    }
  }

  async function alive() {
    try { await conn.query("SELECT 1"); return true; } catch (e) { return false; }
  }

  function ready() {
    if (dead || !conn) throw new Error("the database is not running; reload the page");
  }

  // run(sql, params_json) -> Promise<JSON string>: the bridge glue.PlaygroundConnector awaits.
  function run(sql, paramsJson) {
    return serial(async function () {
      ready();
      var values = JSON.parse(paramsJson || "[]").map(function (p) { return p.v; });
      var slow = slowLeft > 0;
      if (slow) slowLeft -= 1;
      var work = (async function () {
        if (slow) await conn.query(SLOW_SQL);
        var described = await query("DESCRIBE " + sql, values);
        var names = columnValues(described, "column_name");
        var types = columnValues(described, "column_type");
        var columns = names.map(function (n, i) { return [String(n), String(types[i])]; });
        var table = await query(sql, values);
        return JSON.stringify({ columns: columns, rows: encodeTable(table, columns) });
      })();
      work.catch(noop); // after a timeout nobody waits for it
      var timer = null;
      var limit = new Promise(function (_, reject) {
        timer = setTimeout(function () { reject(timeoutError(timeoutS)); }, timeoutS * 1000);
      });
      try {
        return await Promise.race([work, limit]);
      } catch (err) {
        if (err && err.name === "TimeoutError") await restart();
        else if (!(await alive())) await restart();
        throw err;
      } finally {
        clearTimeout(timer);
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
    version: version,
    duckdbWasm: DUCKDB_WASM_VERSION,
    limits: LIMITS,
    timeoutS: timeoutS,
    get bundle() { return bundleName; },
    get restarts() { return restarts; },

    loadSample: function () {
      return serial(async function () {
        ready();
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
              try { await conn.query("DROP TABLE IF EXISTS " + ident(entry.name)); } catch (e) { /* not created */ }
              try { await db.dropFile(entry.internal); } catch (e) { /* not registered */ }
            } else {
              await restart(); // e.g. the allocation failed and took the worker down
            }
          }
        }
        return { tables: tables, errors: errors };
      });
    },

    reset: function () {
      return serial(async function () {
        ready();
        for (var i = 0; i < csv.length; i++) {
          try { await conn.query("DROP TABLE IF EXISTS " + ident(csv[i].name)); } catch (e) { /* gone */ }
          try { await db.dropFile(csv[i].internal); } catch (e) { /* gone */ }
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
      var out = JSON.parse(await pyAsync("fl_check", String(text), run, catalogJson(), timeoutS));
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
      for (var i = 0; i < sqls.length; i++) profiles.push(JSON.parse(await run(sqls[i], "[]")));
      return JSON.parse(py("fl_suggest", tableJson, JSON.stringify(profiles)));
    },

    terminate: function () {
      dead = true;
      if (current && current.engine === engine) current = null;
      var old = db;
      db = null;
      conn = null;
      if (old) old.terminate().catch(noop);
    }
  };

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
      throw new Error("could not download DuckDB from the CDN (" + (err.message || err) + ")");
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

export { DUCKDB_ENGINE_VERSION, DUCKDB_WASM_VERSION, LIMITS };
