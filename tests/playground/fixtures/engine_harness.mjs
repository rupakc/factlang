// A fake browser + DuckDB module + Pyodide for test_engine_fixes.py: enough of each for
// startEngine, loadCsv and reset. It pins the engine's control flow, not DuckDB.
globalThis.window = { location: { hostname: "example.org", search: "" } };
class FakeWorker {
  constructor() { this.listeners = []; FakeWorker.all.push(this); }
  addEventListener(type, fn) { if (type === "error") this.listeners.push(fn); }
  crash(message) { this.listeners.forEach((fn) => fn({ message })); }
  terminate() {}
}
FakeWorker.all = [];
globalThis.Worker = FakeWorker;
const log = [];
globalThis.__log = log;
globalThis.__crashOn = null; // a SQL prefix: that query crashes the worker and never answers
function table(cols) {
  const names = Object.keys(cols);
  const n = names.length ? cols[names[0]].length : 0;
  return { numRows: n, getChild: (name) => ({ get: (i) => cols[name][i] }) };
}
class FakeDuckDB {
  constructor(logger, worker) { this.worker = worker; }
  async instantiate() {}
  async open() {}
  async getVersion() { return "v1.4.3"; }
  async registerFileHandle(name) { log.push("register " + name); }
  async dropFile(name) { log.push("dropFile " + name); }
  async terminate() {}
  async connect() {
    const worker = this.worker;
    return {
      query(sql) {
        log.push(sql);
        if (globalThis.__crashOn && sql.startsWith(globalThis.__crashOn)) {
          globalThis.__crashOn = null;
          setTimeout(() => worker.crash("boom"), 0);
          return new Promise(() => {});
        }
        if (sql.startsWith("DESCRIBE")) return Promise.resolve(table({ column_name: ["id"], column_type: ["INTEGER"] }));
        if (sql.startsWith("SELECT COUNT(*)")) return Promise.resolve(table({ n: [3] }));
        if (sql === "SELECT 1") return Promise.resolve(table({ x: [1] }));
        return Promise.resolve(table({}));
      },
      async prepare() { throw new Error("not used"); },
    };
  }
}
globalThis.__duckdb = {
  getJsDelivrBundles: () => ({ mvp: { mainModule: "mvp.wasm", mainWorker: "mvp.js" }, eh: { mainModule: "eh.wasm", mainWorker: "eh.js" } }),
  selectBundle: async (b) => ({ mainModule: b.eh.mainModule, mainWorker: b.eh.mainWorker, pthreadWorker: null }),
  AsyncDuckDB: FakeDuckDB,
  VoidLogger: class {},
  DuckDBDataProtocol: { BROWSER_FILEREADER: 2 },
};
function pyfn(fn) { fn.destroy = () => {}; return fn; }
const globals = {
  VALUE_KINDS_JSON: JSON.stringify([["INTEGER", "integer"]]),
  SAMPLE_SQL_JSON: JSON.stringify(["CREATE TABLE customers AS SELECT 1 AS id"]),
  fl_table_info: pyfn((name, rows, cols, source) => JSON.stringify({ name, spelling: name, rows, source, columns: [] })),
  table_name_for: pyfn((file) => file.replace(/\.csv$/, "")),
  fl_catalog: pyfn(() => "{}"),
};
globalThis.__pyodide = { globals: { get: (name) => globals[name] } };
// short helpers for the test bodies
globalThis.__fake = () => Promise.resolve(globalThis.__duckdb);
globalThis.__msg = (p) => p.then(() => "ok", (e) => e.message);
