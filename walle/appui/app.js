/* walle app: the page. React 18 and the walle design system (window.WallE) come from vendor/.
   Every URL is relative to /<token>/, so the token never shows in the code. */
(function () {
  "use strict";
  var React = window.React, ReactDOM = window.ReactDOM, W = window.WallE, h = React.createElement;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;
  var BASE = location.pathname.replace(/[^/]*$/, "");
  var VIEWS = [["efficiency", "Efficiency"], ["scan", "Scan"], ["measure", "Measure"], ["trim", "Trim"], ["runs", "Runs"], ["keep", "Keep"]];
  var VERDICT_TONE = { "OVER BUDGET": "walle", "TRIM": "trim", "LEAN": "leaf" };
  var VERDICT_SHORT = { "OVER BUDGET": ["Over", "over"], "TRIM": ["Trim", "trim"], "LEAN": ["Lean", "lean"] };
  var VERB_TONE = { "DELETE": "walle", "PICK ONE": "walle", "REWRITE": "trim", "MOVE": "trim" };
  var AGENT = { claude: "Claude Code", codex: "Codex" };
  var METRIC = { first_call: "First call (tokens)", first_call_billed: "Billed (cache-weighted)", calls_per_prompt: "Calls per prompt", calls_before_edit: "Calls before first edit" };
  var SIDE = { period: "Period", before: "Before", after: "After" };
  var CHANGE_TONE = { lower: "leaf", higher: "walle", within_noise: "neutral", too_few: "neutral" };

  // ------------------------------------------------------------------ helpers
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }
  function num(n) { return n == null ? "-" : Number(n).toLocaleString("en-US"); }
  function kTokens(n) {
    if (n == null) return "";
    if (n >= 10000) return "~" + Math.round(n / 1000) + "k";
    if (n >= 1000) return "~" + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return "~" + n;
  }
  function plural(n, word) { return num(n) + " " + word + (n === 1 ? "" : "s"); }

  function getJSON(path) {
    return fetch(BASE + path, { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (r) {
      return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ("HTTP " + r.status)); return d; });
    });
  }

  // POST one command; onEvent gets each NDJSON event, then {event: "end"}. A refused command gives {event: "refused"}.
  function runCommand(command, folder, onEvent, signal) {
    return fetch(BASE + "api/run", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
      body: JSON.stringify({ command: command, folder: folder }), signal: signal
    }).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return { error: "HTTP " + r.status }; }).then(function (d) {
          onEvent({ event: d.refused ? "refused" : "error", text: d.error || ("HTTP " + r.status) });
          onEvent({ event: "end" });
        });
      }
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (res) {
          buf += dec.decode(res.value || new Uint8Array(), { stream: !res.done });
          var lines = buf.split("\n");
          buf = lines.pop();
          lines.forEach(function (line) {
            if (!line.trim()) return;
            try { onEvent(JSON.parse(line)); } catch (e) { onEvent({ event: "stderr", text: line }); }
          });
          if (res.done) { onEvent({ event: "end" }); return null; }
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      onEvent({ event: e && e.name === "AbortError" ? "cancelled" : "error", text: String(e && e.message || e) });
      onEvent({ event: "end" });
    });
  }

  function readHash() {
    var out = {};
    location.hash.replace(/^#/, "").split("&").forEach(function (kv) {
      var i = kv.indexOf("=");
      if (i > 0) out[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
    });
    return out;
  }
  function writeHash(view, folder) {
    var hash = "#view=" + encodeURIComponent(view) + (folder ? "&folder=" + encodeURIComponent(folder) : "");
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }

  // ------------------------------------------------------------------ small parts
  function Copy(p) {
    var st = useState(""), msg = st[0], setMsg = st[1], ref = useRef(null);
    function copy() {
      var done = function () { setMsg("Copied"); setTimeout(function () { setMsg(""); }, 1500); };
      var select = function () {
        var r = document.createRange(); r.selectNodeContents(ref.current);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); setMsg("Selected");
      };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(p.text).then(done, select);
      else select();
    }
    return h("div", { className: "app-copy" },
      h("code", { ref: ref }, p.text),
      h(W.Button, { variant: "ghost", className: "we-btn-sm", onClick: copy, "aria-label": "Copy " + p.text }, msg || "Copy"));
  }

  function More(p) {
    if (p.total <= p.shown) return null;
    return h("button", { type: "button", className: "app-more", onClick: p.onClick }, "Show " + num(p.total - p.shown) + " more");
  }

  function None(p) {
    return h("div", { className: "app-none" },
      h("p", { className: "app-p" }, p.text),
      p.command ? h("div", { className: "app-row" },
        h("code", { className: "app-src" }, p.command),
        h(W.Button, { onClick: function () { p.onRun(p.command); }, disabled: p.running }, p.label || "Run")) : null,
      p.children);
  }

  // ------------------------------------------------------------------ scan view
  function Item(p) {
    var it = p.item;
    return h("li", { className: "app-item" },
      h("div", { className: "app-item-head" },
        h(W.Badge, { tone: VERB_TONE[it.verb] || "neutral" }, it.verb_text),
        h("span", null, it.label),
        h("code", { className: "app-loc" }, it.location),
        h("span", { className: "app-src" }, it.certainty_text + " · " + it.rule + (it.tokens_est ? " · " + kTokens(it.tokens_est) + " est. tokens" : ""))),
      it.text ? h("p", { className: "app-quote", title: it.text }, it.text) : null,
      it.reason ? h("p", { className: "app-reason" }, it.reason) : null,
      h(Copy, { text: it.command }));
  }

  function Group(p) {
    var g = p.group, st = useState(false), open = st[0], setOpen = st[1], lim = useState(10), limit = lim[0], setLimit = lim[1];
    return h("div", { className: "app-group" },
      h("button", { type: "button", className: "app-group-head", "aria-expanded": open ? "true" : "false", onClick: function () { setOpen(!open); } },
        h("span", { className: "app-caret", "aria-hidden": "true" }, open ? "−" : "+"),
        h("span", null, g.name + " (" + num(g.n) + ")"),
        g.note ? h("span", { className: "app-src" }, g.note) : null),
      open ? h("ol", { className: "app-items" }, g.items.slice(0, limit).map(function (it) { return h(Item, { key: it.id, item: it }); })) : null,
      open ? h(More, { total: g.items.length, shown: limit, onClick: function () { setLimit(limit + 20); } }) : null);
  }

  function Stack(p) {
    var s = p.stack;
    return h("div", { className: "app-card" },
      h("div", { className: "app-verdict" },
        h(W.Badge, { tone: VERDICT_TONE[s.verdict] || "neutral" }, s.verdict_text),
        h("span", { className: "app-big" }, s.tokens_text),
        h("span", { className: "app-src" }, "est. tokens before the first prompt · budget " + num(s.budget)),
        h("span", { className: "app-agent app-row-end" }, AGENT[s.agent] || s.agent)),
      // The first sentence repeats the head (tokens and budget); the rest leads.
      s.lead.length ? h("p", { className: "app-lead" }, s.lead.join(" ")) : null,
      h("p", { className: "app-src" }, s.split),
      s.shared.length ? h("p", { className: "app-src" }, "Shared: " + s.shared.join("; ")) : null,
      s.start_here.length ? h("h2", { className: "app-h" }, "Start here") : null,
      s.start_here.length ? h("ol", { className: "app-items is-numbered" }, s.start_here.map(function (it) { return h(Item, { key: it.id, item: it }); })) : null,
      s.groups.map(function (g) { return h(Group, { key: g.group, group: g }); }));
  }

  function Judge(p) {
    var j = p.judge, lim = useState(12), limit = lim[0], setLimit = lim[1];
    if (!j || !j.items.length) return null;
    return h("section", { className: "app-sec" },
      h("h2", { className: "app-h" }, "Judge answers"),
      h("p", { className: "app-lead" }, plural(j.real, "real problem") + " in " + plural(j.items.length, "answer") +
        (j.not_judged ? ", " + num(j.not_judged) + " not judged" : "") + "."),
      h("p", { className: "app-src" }, "From ", h("code", null, j.path), " (" + j.generated + "): items with a rule in this folder."),
      h("div", { className: "app-judges" }, j.items.slice(0, limit).map(function (c) {
        return h(W.JudgeAnswer, { key: c.id, id: c.id, type: c.type, pair: c.pair, refs: c.refs, verdict: c.verdict, judge: c.judge, cached: c.cached });
      })),
      h(More, { total: j.items.length, shown: limit, onClick: function () { setLimit(limit + 24); } }));
  }

  function ScanView(p) {
    var d = p.data, f = p.folder;
    if (!d.scan) {
      return h(W.EmptyState, {
        body: "A scan shows the instruction files of " + f.display + " and their token cost here.",
        suggestions: [
          { kicker: "Scan", text: "Collect and check the instruction files", command: "walle scan ." },
          { kicker: "Measure", text: "Measure the first call of past sessions in this folder", command: "walle eve ." },
          { kicker: "Trim", text: "Find skills, plugins and servers that sessions do not use", command: "walle trim --plan" },
          { kicker: "Context", text: "Break down the first call of the newest session", command: "walle context --last --json" }
        ],
        onPick: function (s) { p.onRun(s.command, s.kicker === "Scan" ? "scan" : s.kicker === "Context" ? "measure" : s.kicker.toLowerCase()); },
        note: "Commands run in " + f.display + ". walle shows a plan before it changes a file."
      });
    }
    var s = d.scan;
    return h(React.Fragment, null,
      h("section", { className: "app-sec app-narrow" },
        h("h1", { className: "app-title" }, d.display),
        h("p", { className: "app-src" }, "Scan ", h("code", null, s.id), (s.label ? " (" + s.label + ")" : "") + " · " + s.generated +
          " · " + s.estimator + " estimates · " + num(s.files) + " files under " + s.roots.join(", ")),
        s.stacks.map(function (st) { return h(Stack, { key: st.key, stack: st }); })),
      h(Judge, { judge: d.judge }));
  }

  // ------------------------------------------------------------------ measure view
  function cellText(c) { return c ? c.text : "-"; }

  function MeasureTable(p) {
    var m = p.m, metrics = m.metrics;
    var head = h("tr", null, h("th", null, "Model"), m.split ? h("th", null, "Window") : null, h("th", { className: "app-num" }, "n"),
      metrics.map(function (k) { return h("th", { key: k, className: "app-num" }, METRIC[k] || k); }));
    var body = [];
    m.rows.forEach(function (r) {
      m.sides.forEach(function (s, i) {
        var side = r.sides[s] || { cells: {} };
        body.push(h("tr", { key: r.family + s },
          h("td", { "data-l": "Model" }, h("code", null, r.family)),
          m.split ? h("td", { "data-l": "Window" }, SIDE[s] || s) : null,
          h("td", { className: "app-num", "data-l": "n" }, num(side.sessions)),
          metrics.map(function (k) { return h("td", { key: k, className: "app-num", "data-l": METRIC[k] || k }, cellText(side.cells[k])); })));
      });
      if (m.split) {
        body.push(h("tr", { key: r.family + "change" },
          h("td", { "data-l": "Model" }, h("code", null, r.family)), h("td", null, h("strong", null, "Change")), h("td", null, ""),
          metrics.map(function (k) {
            var c = r.change[k];
            return h("td", { key: k, className: "app-num", "data-l": METRIC[k] || k },
              c ? h(W.Badge, { tone: CHANGE_TONE[c.state] || "neutral" }, c.text) : "-");
          })));
      }
    });
    return h("div", { className: "app-tablewrap" }, h("table", { className: "app-table is-stack" }, h("thead", null, head), h("tbody", null, body)));
  }

  function measureLead(m) {
    var r = m.rows[0];
    if (!r) return "No sessions of this folder in the period.";
    if (m.split && r.change.first_call) {
      return "First call " + r.change.first_call.text + " after " + (m.around || "the split") + " (" + r.family + ").";
    }
    var side = r.sides[m.sides[0]] || {}, c = (side.cells || {}).first_call;
    return "First call " + (c ? c.text : "-") + " tokens, median [p25-p75] of " + plural(side.sessions || 0, "session") + " (" + r.family + ").";
  }

  function MeasureView(p) {
    var d = p.data, m = d.measure, ctx = d.context || {}, fc = ctx.first_call, us = ctx.usage;
    return h(React.Fragment, null,
      h("section", { className: "app-sec" },
        h("h1", { className: "app-title" }, "Measure"),
        m ? h(React.Fragment, null,
          h("p", { className: "app-lead" }, measureLead(m)),
          h("p", { className: "app-src" }, "walle eve run ", h("code", null, m.id), " · " + m.generated + " · " + (AGENT[m.agent] || m.agent) +
            " sessions in " + d.display + (m.period && (m.period.since || m.period.before) ? " · " + (m.period.since || "") + " ~ " + (m.period.before || "") : "") +
            ". A value needs n ≥ " + ((m.rules || {}).min_show || 5) + "; a change needs n ≥ " + ((m.rules || {}).min_compare || 10) +
            " on each side and is within noise when the two IQRs overlap."),
          m.rows.length ? h(MeasureTable, { m: m }) : null,
          (m.left_out || {}).resumed ? h("p", { className: "app-src" }, plural(m.left_out.resumed, "resumed session") + " left out: their first call holds the history of another session.") : null)
          : h(None, { text: "No measure run for this folder yet.", command: "walle eve .", label: "Measure", onRun: p.onRun, running: p.running })),
      h("section", { className: "app-sec" },
        h("h2", { className: "app-h" }, "Context"),
        fc || us ? h("p", { className: "app-src" },
          fc ? ["First call: walle context run ", h("code", { key: "c" }, fc.id), " · " + fc.generated + " · a session in " + fc.cwd + (fc.here ? "" : " (no run of this folder)") + ". "] : null,
          us ? ["Usage: run ", h("code", { key: "u" }, us.id), " · " + us.generated + "."] : null) : null,
        fc || us ? h(W.ContextPanel, { context: fc ? fc.data : undefined, usage: us ? us.data : undefined }) : null,
        h("div", { className: "app-row" },
          h(W.Button, { onClick: function () { p.onRun("walle context --last --json"); }, disabled: p.running }, fc ? "Break down the newest session again" : "Break down the newest session"),
          h(W.Button, { onClick: function () { p.onRun("walle context --usage --json"); }, disabled: p.running }, "Count loaded against used"))));
  }

  // ------------------------------------------------------------------ trim view
  function saving(r) {
    var s = r.saving || {}, x = r.sessions || {};
    if (r.already) return null;  // the key already has this state: there is nothing to save
    if (!r.counted) return h("div", { className: "app-save" }, "Saving not counted: " + (r.not_counted || "-") + ".");
    var parts = [];
    if (s.tokens_per_session_min != null) parts.push(h("span", { key: "t" }, "≈ ", h("strong", null, num(s.tokens_per_session_min)), " tokens per " + (s.per || "session")));
    if (x.period != null) parts.push(h("span", { key: "s" }, (parts.length ? " · " : "") + "loaded in " + num(x.loaded) + " of " + num(x.period) + " sessions, used in " + num(x.used)));
    return parts.length ? h("div", { className: "app-save" }, parts) : null;
  }

  var APPLIES = { walle: ["walle applies", "eve"], manual: ["Manual", "neutral"], patch: ["Patch", "trim"] };

  function TrimRow(p) {
    var r = p.row, a = APPLIES[r.applies] || [r.applies || "-", "neutral"], v = r.verdict;
    return h("li", null,
      h("div", { className: "app-row" },
        h(W.Badge, { tone: a[1] }, a[0]),
        r.already ? h(W.Badge, { tone: "leaf" }, "Already set") : null,
        r.here ? h(W.Badge, { tone: "eve" }, "This folder") : null,
        h("span", { className: "app-src" }, (r.source && r.source.type ? r.source.type + " · " : "") + (r.action || ""))),
      h("div", { className: "app-trim-title" }, r.title),
      saving(r),
      r.already ? h("div", { className: "app-src" },
        "In the file since " + (r.set_at ? r.set_at.replace("T", " ").slice(0, 16) : "an earlier apply") + ". No apply needed.",
        r.set_status.length ? h("ul", { className: "app-status" }, r.set_status.map(function (x) {
          return h("li", { key: x.entrypoint }, h("code", null, x.entrypoint), " " + x.text);
        })) : null) : null,
      v ? h("div", { className: "app-fix" },
        h(W.Badge, { tone: v.real ? "walle" : "leaf" }, v.real ? "Real problem" : "Not a problem"),
        v.fix && v.fix !== "-" ? [h("span", { key: "k", className: "app-fix-k" }, "fix"), h("span", { key: "f" }, v.fix)] : null,
        v.why ? h("span", { className: "app-muted" }, v.why) : null) : null,
      r.risk ? h("p", { className: "app-src" }, "Risk " + (r.risk.level || "-") + ": " + (r.risk.why || "")) : null,
      r.command ? h(Copy, { text: r.command }) : null,
      h("details", { className: "app-details" },
        h("summary", null, "Where, undo and check"),
        h("dl", null,
          r.where ? [h("dt", { key: "w" }, "Where"), h("dd", { key: "wd" }, r.where)] : null,
          r.undo ? [h("dt", { key: "u" }, "Undo"), h("dd", { key: "ud" }, r.undo)] : null,
          r.check ? [h("dt", { key: "c" }, "Check"), h("dd", { key: "cd" }, r.check)] : null,
          r.used_in && r.used_in.length ? [h("dt", { key: "i" }, "Used in"), h("dd", { key: "id" }, r.used_in.join(", "))] : null)));
  }

  function TrimView(p) {
    var t = p.data.trim, ft = useState("all"), filter = ft[0], setFilter = ft[1], lim = useState(15), limit = lim[0], setLimit = lim[1];
    if (!t) {
      return h("section", { className: "app-sec" }, h("h1", { className: "app-title" }, "Trim plan"),
        h(None, { text: "No trim plan yet. A plan reads the sessions of the period and lists what they load and do not use. It changes nothing.", command: "walle trim --plan", label: "Make a plan", onRun: p.onRun, running: p.running }));
    }
    var here = t.rows.filter(function (r) { return r.here; });
    var rows = filter === "here" ? here : t.rows;
    var count = {};
    t.rows.forEach(function (r) { var k = r.already ? "already" : r.applies; count[k] = (count[k] || 0) + 1; });
    var per = t.period || {};
    return h("section", { className: "app-sec" },
      h("h1", { className: "app-title" }, "Trim plan"),
      h("p", { className: "app-lead" }, plural(t.rows.length, "row") + ": " + num(count.walle || 0) + " walle can apply, " +
        num(count.already || 0) + " already set, " + num(count.manual || 0) + " manual, " + num(count.patch || 0) +
        " patches. The app changes nothing: copy a command and run it in a terminal."),
      h("p", { className: "app-src" }, "walle trim --plan run ", h("code", null, t.id), (t.label ? " (" + t.label + ")" : "") + " · " + t.generated +
        (per.sessions ? " · " + num(per.sessions) + " sessions, " + String(per.first || "").slice(0, 10) + " ~ " + String(per.last || "").slice(0, 10) : "") +
        " · sorted by tokens loaded and not used, rows already set last"),
      h("div", { className: "app-row" },
        h("div", { className: "app-tabs", role: "tablist" },
          [["all", "All rows", t.rows.length], ["here", "This folder", here.length]].map(function (x) {
            return h("button", { key: x[0], type: "button", role: "tab", "aria-selected": filter === x[0] ? "true" : "false",
              className: cx("app-tab", filter === x[0] && "is-on"), onClick: function () { setFilter(x[0]); setLimit(15); } },
              x[1], h("span", { className: "app-tab-n" }, x[2]));
          })),
        h(W.Button, { className: "app-row-end", onClick: function () { p.onRun("walle trim --plan"); }, disabled: p.running }, "Make a new plan")),
      rows.length ? h("ol", { className: "app-trim" }, rows.slice(0, limit).map(function (r) { return h(TrimRow, { key: r.id, row: r }); }))
        : h("p", { className: "app-p app-muted" }, "No row of this plan names this folder."),
      h(More, { total: rows.length, shown: limit, onClick: function () { setLimit(limit + 30); } }),
      t.notes.length ? h("p", { className: "app-src" }, t.notes.join(" ")) : null);
  }

  // ------------------------------------------------------------------ efficiency view
  var STATUS = { applied: ["Applied", "eve"], failed: ["Failed", "walle"], lost: ["Lost", "walle"], writing: ["Writing", "trim"] };
  var PAIR_TONE = { gone: "leaf", still_loaded: "walle", "new": "walle", not_loaded: "neutral" };

  function usdText(x) { return x && x.usd != null ? "$" + Number(x.usd).toFixed(4) : null; }

  function Tile(p) {
    return h("div", { className: "app-tile" },
      h("span", { className: "app-agent" }, p.k),
      h("div", { className: "app-verdict" }, p.badge || null, h("span", { className: "app-big" }, p.value), p.tag || null),
      p.sub ? h("p", { className: "app-src" }, p.sub) : null);
  }

  function NextStep(p) {
    var n = p.next;
    return h("div", { className: "app-next" },
      h("div", { className: "app-row" }, h(W.Badge, { tone: "eve" }, "Next step"), h("span", { className: "app-p" }, n.text)),
      n.run ? h("div", { className: "app-row" },
        h("code", { className: "app-src" }, n.command),
        h(W.Button, { variant: "primary", onClick: function () { p.onRun(n.command); }, disabled: p.running }, "Run"))
        : h(React.Fragment, null, h(Copy, { text: n.command }),
          h("p", { className: "app-src" }, "This command changes a settings file: run it in a terminal. walle keeps a backup, and walle undo puts it back.")));
  }

  function EffTiles(p) {
    var e = p.e, s = e.stack, f = e.first_call, t = e.trim, ss = e.session;
    var tiles = [];
    tiles.push(s ? h(Tile, { key: "s", k: "Instruction files", badge: h(W.Badge, { tone: VERDICT_TONE[s.verdict] || "neutral" }, s.verdict_text),
      value: s.tokens_text, sub: "est. tokens before the first prompt · budget " + num(s.budget) + " · " + plural(s.start_here_n, "Start here item") })
      : h(Tile, { key: "s", k: "Instruction files", value: "-", sub: "No scan of this folder yet." }));
    tiles.push(f && f.first_call ? h(Tile, { key: "f", k: "First call", value: num(f.first_call.median),
      tag: f.change ? h(W.Badge, { tone: CHANGE_TONE[f.change.state] || "neutral" }, f.change.state === "too_few" ? "too few" : f.change.text) : null,
      sub: "tokens, median [" + num(f.first_call.p25) + "-" + num(f.first_call.p75) + "] of " + plural(f.sessions, "session") + " · " + f.family +
        (f.side === "after" && f.around ? " · since " + f.around : "") })
      : h(Tile, { key: "f", k: "First call", value: "-", sub: "No measure run of this folder yet." }));
    var billed = f && f.billed && f.billed.median != null ? f.billed : null;
    tiles.push(billed ? h(Tile, { key: "b", k: "Billed first call", value: num(billed.median),
      sub: "input-equivalent tokens, median, cache reads and writes weighted by price" +
        (f.usd ? " · " + usdText(f.usd) + " at " + f.usd.model + " list prices of " + f.usd.prices_as_of : "") })
      : ss && ss.billed ? h(Tile, { key: "b", k: "Billed first call", value: num(ss.billed.input_equiv),
        sub: "input-equivalent tokens of the newest session" + (ss.here ? "" : " in " + ss.cwd + ", not this folder") +
          (usdText(ss.billed) ? " · " + usdText(ss.billed) + " at " + ss.billed.priced_as + " list prices of " + ss.billed.prices_as_of : "") })
      : h(Tile, { key: "b", k: "Billed first call", value: "-", sub: "No billed count yet: walle eve measures it for Claude Code sessions." }));
    tiles.push(t ? h(Tile, { key: "t", k: "Trim", value: t.saving_text,
      sub: "est. tokens over " + plural(t.sessions || 0, "session") + " in rows not yet set · " + num(t.to_apply) + " walle can apply, " +
        num(t.manual) + " manual, " + num(t.patch) + " patches, " + num(t.already + t.applied_since) + " already set" })
      : h(Tile, { key: "t", k: "Trim", value: "-", sub: "No trim plan yet." }));
    return h("div", { className: "app-tiles" }, tiles);
  }

  function AppliedCard(p) {
    var a = p.apply, m = a.measured;
    var st = a.undone ? (a.undo_status === "lost" ? ["Undo lost", "walle"] : ["Undone", "neutral"]) : (STATUS[a.status] || [a.status || "-", "neutral"]);
    var firsts = m && m.measure ? m.measure.metrics.filter(function (k) { return k.indexOf("first_call") === 0; }) : [];
    return h("li", null,
      h("div", { className: "app-row" },
        h(W.Badge, { tone: st[1] }, st[0]), h("code", null, a.id),
        h("span", { className: "app-src" }, a.generated + (a.plan_id ? " · plan " + a.plan_id : "") + (a.files.length ? " · " + a.files.join(", ") : ""))),
      h("ul", { className: "app-status" }, a.rows.map(function (r) {
        return h("li", { key: r.id }, h("code", null, r.id), " " + (r.title || r.what || "-"));
      })),
      a.undo_command ? h(Copy, { text: a.undo_command }) : null,
      a.undo_status === "lost" ? h("p", { className: "app-src" }, "walle undo put the files back, then another program rewrote one. Check the file before you apply again.") : null,
      m ? h("div", { className: "app-measured" },
        h("p", { className: "app-src" }, "Measured by walle context --compare run ", h("code", null, m.id), " · " + m.generated +
          (m.folder ? " · first call of " + m.folder + (m.here ? "" : " (another folder)") : "")),
        m.applied.length ? h("ul", { className: "app-status" }, m.applied.map(function (r) {
          return h("li", { key: r.id },
            r.pair ? h(W.Badge, { tone: PAIR_TONE[r.pair.state] || "neutral" }, r.pair.state.replace("_", " ")) : null, " ", r.text,
            r.pair ? h("div", { className: "app-src" }, r.pair.text) : null,
            r.surfaces.map(function (s) { return h("div", { key: s.entrypoint, className: "app-src" }, s.text); }));
        })) : null,
        m.newest ? h("p", { className: "app-src" }, "Newest session before and since: " + num(m.newest.before) + " -> " + num(m.newest.since) + " tokens" +
          (m.newest.same_family && m.newest.delta != null ? " (" + (m.newest.delta > 0 ? "+" : "") + num(m.newest.delta) + ")" : " (model families differ)") +
          (m.newest.billed ? "; billed " + num(m.newest.billed.before.input_equiv) + " -> " + num(m.newest.billed.since.input_equiv) +
            (usdText(m.newest.billed.before) && usdText(m.newest.billed.since) ? " (" + usdText(m.newest.billed.before) + " -> " + usdText(m.newest.billed.since) + ")" : "") : "") +
          ". One session on each side is not a median.") : null,
        m.measure && m.measure.rows.length && firsts.length ? h(MeasureTable, { m: { metrics: firsts, sides: m.measure.sides, split: true, rows: m.measure.rows } }) : null,
        h("div", { className: "app-row" }, h(W.Button, { onClick: function () { p.onRun(a.compare_command); }, disabled: p.running }, "Measure again")))
        : h(None, { text: "Not measured yet. walle context --compare compares the sessions before this apply and since it.",
          command: a.compare_command, label: "Measure since", onRun: p.onRun, running: p.running }));
  }

  // The measured first call of the newest session as one bar: what fills it, largest part first.
  var PART_COLOR = ["var(--eve)", "var(--trim)", "var(--lean)", "var(--rust)", "var(--link)", "var(--line-strong)", "var(--muted)"];
  function CompositionBar(p) {
    var c = p.c, i = 0;
    var parts = c.parts.map(function (x) {
      return Object.assign({ color: x.source === "residual" ? "var(--sunk)" : PART_COLOR[i++ % PART_COLOR.length] }, x);
    });
    return h("div", { className: "app-comp" },
      h("div", { className: "app-comp-bar", role: "img", "aria-label": "First call of " + num(c.total) + " tokens: " +
        parts.map(function (x) { return x.label + " " + x.share + "%"; }).join(", ") },
        parts.map(function (x) {
          return h("span", { key: x.source, className: x.source === "residual" ? "is-rest" : null,
            style: { width: Math.max(x.share, 0.5) + "%", background: x.color }, title: x.label + ": " + num(x.tokens) + " tokens (" + x.share + "%)" });
        })),
      h("ul", { className: "app-comp-key" }, parts.map(function (x) {
        return h("li", { key: x.source },
          h("i", { className: x.source === "residual" ? "is-rest" : null, style: { background: x.color } }),
          h("span", null, x.label), h("span", { className: "app-src" }, kTokens(x.tokens) + " · " + x.share + "%"));
      })),
      h("p", { className: "app-src" }, num(c.total) + " tokens measured; walle's estimates per source add up to " + num(c.estimated) +
        ". The rest is framing and estimate error."));
  }

  // The open trim rows as savings, largest first, with who can do each. A manual row opens its steps; its
  // checkbox is a note in this browser only, the next plan says if the source is still loaded.
  var DONE_KEY = "walle.done.";
  function readDone(id) { try { return localStorage.getItem(DONE_KEY + id) === "1"; } catch (e) { return false; } }
  function writeDone(id, v) { try { if (v) localStorage.setItem(DONE_KEY + id, "1"); else localStorage.removeItem(DONE_KEY + id); } catch (e) { /* no storage */ } }
  function monthText(r) {
    if (r.month_tokens == null) return "";
    return " · " + kTokens(r.month_tokens) + " a month" + (r.month_usd != null ? " (" + (r.month_usd >= 0.01 ? "$" + r.month_usd.toFixed(2) : "under $0.01") + ")" : "");
  }
  function SavingRow(p) {
    var r = p.row, a = APPLIES[r.applies] || [r.applies || "-", "neutral"];
    var st = useState(function () { return readDone(r.id); }), done = st[0], setDone = st[1];
    return h("li", { className: done ? "is-done" : null },
      h("div", { className: "app-row" },
        h("span", { className: "app-sav-n" }, kTokens(r.per_session)),
        h("span", { className: "app-sav-title" }, r.title),
        h(W.Badge, { tone: a[1] }, r.who)),
      h("div", { className: "app-src" }, "est. tokens per session that loads it · " + plural(r.sessions, "session") + " loaded it and did not use it" + monthText(r)),
      r.applies === "walle" && r.command ? h(Copy, { text: r.command }) : null,
      h("details", { className: "app-details" }, h("summary", null, r.applies === "walle" ? "What it changes, check and undo" : r.applies === "manual" ? "How to turn it off" : "How to change it"),
        h("ol", { className: "app-steps" },
          r.applies === "walle" ? h("li", null, "Run the command above in a terminal. It changes: ", r.where || "-")
            : h(React.Fragment, null, r.where ? h("li", null, "Open: ", r.where) : null,
              h("li", null, "Make the change: ", r.title.charAt(0).toLowerCase() + r.title.slice(1), ".")),
          r.check ? h("li", null, "Check: ", r.check) : null),
        r.undo ? h("p", { className: "app-src" }, "Undo: " + r.undo) : null,
        r.risk ? h("p", { className: "app-src" }, "Risk " + r.risk.level + ": " + r.risk.why) : null,
        r.applies !== "walle" ? h("label", { className: "app-check" }, h("input", { type: "checkbox", checked: done, onChange: function (ev) { setDone(ev.target.checked); writeDone(r.id, ev.target.checked); } }),
          " I did this (a note in this browser; the next plan shows if it still loads)") : null));
  }
  function Savings(p) {
    var s = p.s, shares = Object.keys(s.share).sort(function (x, y) { return s.share[y] - s.share[x]; });
    return h("div", { className: "app-sav" },
      h("p", { className: "app-p" }, plural(s.n, "open saving") + ", largest first" +
        (shares.length ? ". Of the tokens they save: " + shares.map(function (k) { return s.share[k] + "% " + ((APPLIES[k] || [k])[0]).toLowerCase(); }).join(", ") : "") + "."),
      h("ol", { className: "app-trim" }, s.rows.map(function (r) { return h(SavingRow, { key: r.id, row: r }); })),
      h("p", { className: "app-src" }, "Per session is walle's low estimate. A month is the plan's " + (s.days ? num(s.days) + " days" : "period") + " scaled to 30 days" +
        (s.priced_as ? "; dollars at the cache-read list price of " + s.priced_as + " (" + s.prices_as_of + "), the low end, since a trimmed prefix is mostly read from the cache." : ".")));
  }

  function EfficiencyView(p) {
    var d = p.data, e = d.efficiency, applies = d.applies || [];
    var ss = e.session, t = e.trim, s = e.stack;
    return h(React.Fragment, null,
      h("section", { className: "app-sec" },
        h("h1", { className: "app-title" }, d.display),
        h("p", { className: "app-lead" }, "Measure, trim, measure again. The page reads the runs of this folder and changes nothing."),
        h(NextStep, { next: e.next, onRun: p.onRun, running: p.running }),
        h(EffTiles, { e: e })),
      h("section", { className: "app-sec" },
        h("h2", { className: "app-h" }, "1 · Instruction files"),
        s ? h(React.Fragment, null,
          s.lead.length ? h("p", { className: "app-p" }, s.lead.join(" ")) : null,
          s.start_here.length ? h("ol", { className: "app-items is-numbered" }, s.start_here.map(function (it) { return h(Item, { key: it.id, item: it }); })) : null,
          h("p", { className: "app-src" }, "Scan ", h("code", null, s.scan_id), " · " + s.generated + " · the stack with the highest verdict of " + plural(s.stacks, "stack") + ". ",
            h("button", { type: "button", className: "app-more", onClick: function () { p.onView("scan"); } }, "Open Scan")))
          : h(None, { text: "No scan of this folder yet.", command: "walle scan .", label: "Scan", onRun: p.onRun, running: p.running })),
      h("section", { className: "app-sec" },
        h("h2", { className: "app-h" }, "2 · Measured first call"),
        d.measure ? h(React.Fragment, null,
          h("p", { className: "app-src" }, "walle eve run ", h("code", null, d.measure.id), " · " + d.measure.generated + ". The billed column weights cache reads and writes by price: it is what a trim of cached tokens saves."),
          d.measure.rows.length ? h(MeasureTable, { m: d.measure }) : h("p", { className: "app-p app-muted" }, "No sessions of this folder in the period."))
          : h(None, { text: "No measure run of this folder yet.", command: "walle eve .", label: "Measure", onRun: p.onRun, running: p.running }),
        ss && ss.composition ? h(CompositionBar, { c: ss.composition }) : null,
        ss ? h("p", { className: "app-src" }, "Newest session " + (ss.session || "-") + " (walle context run ", h("code", null, ss.id), ", " + ss.generated + ", " + ss.cwd + (ss.here ? "" : ", not this folder") + "): " +
          num(ss.total) + " tokens in the first call" + (ss.billed ? ", billed " + num(ss.billed.input_equiv) + " input-equivalent" +
            (usdText(ss.billed) ? ", " + usdText(ss.billed) + " at " + ss.billed.priced_as + " list prices of " + ss.billed.prices_as_of : "") : "") + ".") : null,
        h("div", { className: "app-row" },
          d.measure ? h(W.Button, { onClick: function () { p.onRun("walle eve ."); }, disabled: p.running }, "Measure again") : null,
          h(W.Button, { onClick: function () { p.onRun("walle context --last --json"); }, disabled: p.running }, "Break down the newest session"),
          h("button", { type: "button", className: "app-more", onClick: function () { p.onView("measure"); } }, "Open Measure"))),
      h("section", { className: "app-sec" },
        h("h2", { className: "app-h" }, "3 · Trim"),
        t ? h(React.Fragment, null,
          t.savings && t.savings.n ? h(Savings, { s: t.savings }) : null,
          h("p", { className: "app-p" }, t.to_apply ? plural(t.to_apply, "row") + " that walle can apply. Copy a command and run it in a terminal; each row has its undo and check."
            : "No row of this plan is left for walle to apply."),
          t.applied_since ? h("p", { className: "app-src" }, plural(t.applied_since, "row") + " of this plan changed by an apply after it are left out. A new plan shows them as already set.") : null,
          t.top.length && !(t.savings && t.savings.n) ? h("ol", { className: "app-trim" }, t.top.map(function (r) { return h(TrimRow, { key: r.id, row: r }); })) : null,
          t.already_rows.length ? h("details", { className: "app-details" }, h("summary", null, plural(t.already, "row") + " already set: status per surface"),
            h("ul", { className: "app-status" }, t.already_rows.map(function (r) {
              return h("li", { key: r.id }, r.title, r.set_status.map(function (x) { return h("div", { key: x.entrypoint, className: "app-src" }, h("code", null, x.entrypoint), " " + x.text); }));
            }))) : null,
          h("p", { className: "app-src" }, "walle trim --plan run ", h("code", null, t.plan_id), " · " + t.generated + ". ",
            h("button", { type: "button", className: "app-more", onClick: function () { p.onView("trim"); } }, "Open Trim for every row")))
          : h(None, { text: "No trim plan yet. A plan reads the sessions of the period and lists what they load and do not use. It changes nothing.", command: "walle trim --plan", label: "Make a plan", onRun: p.onRun, running: p.running })),
      h("section", { className: "app-sec" },
        h("h2", { className: "app-h" }, "4 · Applied"),
        applies.length ? h(React.Fragment, null,
          h("p", { className: "app-p" }, plural(e.applies.standing, "apply") .replace("applys", "applies") + " in place, " + num(e.applies.measured) + " measured since. A settings key acts on every folder, so every apply is listed."),
          h("ol", { className: "app-trim" }, applies.map(function (a) { return h(AppliedCard, { key: a.id, apply: a, onRun: p.onRun, running: p.running }); })))
          : h("p", { className: "app-p app-muted" }, "No apply yet. walle apply runs in a terminal: it takes a backup first, and walle undo puts the file back.")));
  }

  // ------------------------------------------------------------------ runs and keep
  function RunsView(p) {
    var rows = p.data.runs || [], here = rows.filter(function (r) { return r.here; }).length;
    var lim = useState(40), limit = lim[0], setLimit = lim[1];
    return h("section", { className: "app-sec" },
      h("h1", { className: "app-title" }, "Runs"),
      h("p", { className: "app-lead" }, rows.length ? plural(rows.length, "run") + ", " + num(here) + " of " + p.data.display + ". Newest first." : "No runs yet."),
      h("p", { className: "app-src" }, "From ", h("code", null, p.out + "/index.jsonl"), ". A marked row is a run of this folder."),
      rows.length ? h("div", { className: "app-tablewrap" }, h("table", { className: "app-table is-stack" },
        h("thead", null, h("tr", null, h("th", null, "Run"), h("th", null, "Kind"), h("th", null, "When"), h("th", null, "Label"), h("th", null, "What"))),
        h("tbody", null, rows.slice(0, limit).map(function (r) {
          return h("tr", { key: r.id, className: r.here ? "is-here" : null },
            h("td", null, h("code", null, r.id)),
            h("td", { "data-l": "Kind" }, h("span", { className: "app-kind" }, r.kind)),
            h("td", { className: "app-when", "data-l": "When" }, r.generated),
            h("td", { "data-l": "Label" }, r.label || h("span", { className: "app-muted" }, "-")),
            h("td", null, r.what, r.roots.length ? h("div", { className: "app-src" }, r.roots.join(", ")) : null));
        })))) : null,
      h(More, { total: rows.length, shown: limit, onClick: function () { setLimit(limit + 60); } }));
  }

  function KeepView(p) {
    var rows = p.data.keep || [];
    return h("section", { className: "app-sec app-narrow" },
      h("h1", { className: "app-title" }, "Keep"),
      h("p", { className: "app-lead" }, rows.length ? plural(rows.length, "kept entry").replace("entrys", "entries") + " in keep.json. walle scan and walle trim --plan leave them out." : "Nothing is kept."),
      rows.length ? h("ol", { className: "app-items" }, rows.map(function (e) {
        return h("li", { key: e.id, className: "app-item" },
          h("div", { className: "app-item-head" }, h(W.Badge, { tone: "leaf" }, "Kept"), h("span", null, e.name || e.rule || e.id),
            e.file ? h("code", { className: "app-loc" }, e.file + (e.line ? ":" + e.line : "")) : null,
            h("span", { className: "app-src" }, [e.source || e.kind, e.date].filter(Boolean).join(" · "))),
          e.why ? h("p", { className: "app-reason" }, e.why) : null);
      })) : null,
      h("p", { className: "app-src" }, "Keep and unkeep from the app come in a later version. In a terminal: ", h("code", null, "walle keep <id> --why \"…\""), "."));
  }

  // ------------------------------------------------------------------ command output
  function Output(p) {
    var r = p.run;
    var badge = r.refused ? h(W.Badge, { tone: "walle" }, "Refused") : r.running ? h(W.Badge, { tone: "eve" }, "Running")
      : r.code === 0 ? h(W.Badge, { tone: "leaf" }, "Exit 0") : r.code != null ? h(W.Badge, { tone: "walle" }, "Exit " + r.code)
      : h(W.Badge, { tone: "walle" }, r.cancelled ? "Cancelled" : "Error");
    var text = r.lines.join("\n");
    return h("section", { className: "app-card", "aria-live": "polite", "aria-label": "Command output" },
      h("div", { className: "app-out-head" },
        h("span", { className: "app-agent" }, "Output"), h("code", null, r.command), badge,
        r.running ? h(W.Button, { variant: "ghost", className: "we-btn-sm app-row-end", onClick: p.onCancel }, "Cancel")
          : h(W.Button, { variant: "ghost", className: "we-btn-sm app-row-end", onClick: p.onClose }, "Close")),
      r.refused ? h("p", { className: "app-p app-err" }, r.error) : null,
      !r.refused && r.cwd ? h("p", { className: "app-src" }, "In " + r.cwd + (r.seconds != null ? " · " + r.seconds.toFixed(1) + " s" : "")) : null,
      !r.refused && r.error ? h("p", { className: "app-p app-err" }, r.error) : null,
      text ? h("pre", { className: "app-pre" }, text) : null);
  }

  // ------------------------------------------------------------------ app
  function App() {
    var hash = readHash();
    var s1 = useState(null), state = s1[0], setState = s1[1];
    var s2 = useState(hash.view && VIEWS.some(function (v) { return v[0] === hash.view; }) ? hash.view : "efficiency"), view = s2[0], setView = s2[1];
    var s3 = useState(hash.folder || null), folder = s3[0], setFolder = s3[1];
    var s4 = useState(null), data = s4[0], setData = s4[1];
    var s5 = useState(null), error = s5[0], setError = s5[1];
    var s6 = useState(null), run = s6[0], setRun = s6[1];
    var s7 = useState(false), sideOpen = s7[0], setSideOpen = s7[1];
    var abort = useRef(null), mainRef = useRef(null), folderRef = useRef(folder);
    folderRef.current = folder;

    function loadState() {
      return getJSON("api/state").then(function (st) {
        setState(st);
        setFolder(function (f) {
          if (f && st.folders.some(function (x) { return x.path === f; })) return f;
          // The folder the app started in comes first, with a scan or without one.
          var first = st.folders.find(function (x) { return x.launch; }) || st.folders.find(function (x) { return x.has_scan; }) || st.folders[0];
          return first ? first.path : null;
        });
      }).catch(function (e) { setError(String(e.message || e)); });
    }
    function loadFolder(f) {
      if (!f) return Promise.resolve();
      // A view that comes back after the user picked another folder is dropped.
      return getJSON("api/folder?path=" + encodeURIComponent(f)).then(function (d) {
        if (d.path !== folderRef.current) return;
        setData(d); setError(null);
      }).catch(function (e) { if (f === folderRef.current) setError(String(e.message || e)); });
    }

    useEffect(function () { loadState(); }, []);
    useEffect(function () { setData(null); loadFolder(folder); }, [folder]);
    useEffect(function () { writeHash(view, folder); }, [view, folder]);

    function start(command, nextView) {
      if (run && run.running) return;
      var isScan = /^walle\s+scan(\s|$)/.test(command);
      if (nextView) setView(nextView);
      if (isScan) setView("scan");
      var ctl = new AbortController(); abort.current = ctl;
      var acc = { command: command, running: true, isScan: isScan, lines: [], progress: { step: 0, counts: {}, elapsed: 0, done: false } };
      setRun(acc);
      if (mainRef.current) mainRef.current.scrollTop = 0;
      function push(next) { acc = Object.assign({}, acc, next); setRun(acc); }
      runCommand(command, folder, function (ev) {
        var pr = acc.progress;
        switch (ev.event) {
          case "start": push({ command: ev.command, cwd: ev.cwd }); break;
          case "stdout": case "stderr": case "note": push({ lines: acc.lines.concat([ev.text]) }); break;
          case "progress": {
            var d = ev.data || {}, counts = Object.assign({}, pr.counts);
            ["files", "pairs", "items"].forEach(function (k) { if (d[k] != null) counts[k] = d[k]; });
            if (d.event === "step") {
              var idx = ["collect", "stack", "classify", "pairs", "triage"].indexOf(d.step);
              var step = d.state === "end" ? Math.max(pr.step, idx + 1) : Math.max(pr.step, idx);
              push({ progress: Object.assign({}, pr, { step: step, counts: counts, elapsed: d.t }) });
            } else if (d.event === "done") {
              push({ progress: Object.assign({}, pr, { step: 5, counts: counts, elapsed: d.t, done: true, scan: d.scan }) });
            } else if (d.event === "note") {
              push({ lines: acc.lines.concat([d.text]) });
            }
            break;
          }
          case "exit": push({ code: ev.code, seconds: ev.seconds }); break;
          case "refused": push({ refused: true, error: ev.text }); break;
          case "cancelled": push({ cancelled: true }); break;
          case "error": push({ error: ev.text }); break;
          case "end":
            push({ running: false });
            loadState(); loadFolder(folderRef.current);  // the folder selected now, not the one of the start
            break;
        }
      }, ctl.signal);
    }
    function cancel() { if (abort.current) abort.current.abort(); }

    if (!state) {
      return h("div", { className: "app-col" }, error ? h("p", { className: "app-p app-err" }, "walle app: " + error) : h("p", { className: "app-src" }, "Loading"));
    }
    var f = state.folders.find(function (x) { return x.path === folder; }) || { path: folder, display: folder || "-", has_scan: false };
    var running = !!(run && run.running);
    var items = VIEWS.map(function (v) {
      return { id: "v:" + v[0], group: "Actions", title: v[1], meta: v[0] === "runs" ? num(state.runs) : v[0] === "keep" ? num(state.keep) : undefined };
    }).concat(state.folders.map(function (x) {
      var vs = VERDICT_SHORT[x.verdict];
      return {
        id: "f:" + x.path, group: "Folders",
        title: h("span", { className: x.path === folder ? "app-cur" : null, title: x.display }, x.short),
        meta: x.has_scan ? h("span", { className: "app-fm", title: (x.verdict || "-") + " · " + x.tokens_text + " est. tokens (" + (AGENT[x.agent] || x.agent) + ", scan " + x.scan_id + ")" },
          vs ? h("span", { className: "app-fv-" + vs[1] }, vs[0]) : null, kTokens(x.tokens)) : h("span", null, "no scan")
      };
    }));
    var showProgress = run && run.isScan && !run.refused && (run.running || run.progress.done) && !run.dismissed && view === "scan";
    var body;
    if (showProgress) {
      body = h(W.ScanProgress, {
        step: run.progress.step, done: run.progress.done, roots: [run.cwd || f.display], counts: run.progress.counts,
        elapsed: run.progress.elapsed, onCancel: cancel
      }, h(W.Button, { onClick: function () { setRun(Object.assign({}, run, { dismissed: true })); loadFolder(folderRef.current); } }, "Show results"));
    } else if (!data) {
      body = error ? h("p", { className: "app-p app-err" }, "walle app: " + error) : h("p", { className: "app-src" }, "Loading " + f.display);
    } else if (view === "efficiency") body = h(EfficiencyView, { data: data, onRun: start, running: running, onView: setView });
    else if (view === "scan") body = h(ScanView, { data: data, folder: f, onRun: start, running: running });
    else if (view === "measure") body = h(MeasureView, { data: data, onRun: start, running: running });
    else if (view === "trim") body = h(TrimView, { data: data, onRun: start, running: running });
    else if (view === "runs") body = h(RunsView, { data: data, out: state.out });
    else body = h(KeepView, { data: data });
    var showOutput = run && !(run.isScan && !run.refused && !run.error && (run.code == null || run.code === 0) && !run.cancelled) && !run.closed;
    var device = state.device || {};

    return h("div", { className: "app" },
      h("button", { type: "button", className: cx("app-scrim", sideOpen && "is-open"), "aria-label": "Close the menu", tabIndex: -1, onClick: function () { setSideOpen(false); } }),
      h("aside", { className: cx("app-side", sideOpen && "is-open") },
        h(W.Sidebar, {
          items: items, activeId: "v:" + view, label: "walle",
          onSelect: function (id) {
            if (id.indexOf("v:") === 0) setView(id.slice(2)); else setFolder(id.slice(2));
            setSideOpen(false);
          },
          onAction: function () { setSideOpen(false); start("walle scan .", "scan"); },
          footer: h(React.Fragment, null, "walle " + state.version, h("br"), h("span", { className: "app-foot-out", title: state.out }, state.out))
        })),
      h("main", { className: "app-main", ref: mainRef },
        h("div", { className: "app-bar" },
          h(W.Button, { variant: "ghost", className: "we-btn-sm", onClick: function () { setSideOpen(true); }, "aria-label": "Open the menu" }, "Menu"),
          h("span", { className: "we-wordmark" }, "walle"),
          h("span", { className: "app-bar-view" }, (VIEWS.find(function (v) { return v[0] === view; }) || [0, ""])[1])),
        h("div", { className: "app-col" },
          h("div", { className: "app-cmd" },
            h(W.CommandBar, {
              project: { path: f.display, branch: data && data.path === f.path ? data.branch : undefined },
              device: { name: device.name || "this machine", status: running ? "busy" : "online",
                        detail: running ? "walle running" : (device.load != null ? "load " + device.load : "") },
              agent: (state.agents || []).join(" · ") || "-", placeholder: "scan .", running: running,
              hint: h(React.Fragment, null, "Type the subcommand only. It runs in ", h("code", null, f.display),
                ". Read commands only: ", state.commands.allowed.join(", "), " (", h("code", null, "trim --plan"), ", ",
                h("code", null, "judge --dry-run"), "). apply, undo and keep stay in the terminal."),
              onRun: function (cmd) { start(cmd); }
            })),
          showOutput ? h(Output, { run: run, onCancel: cancel, onClose: function () { setRun(Object.assign({}, run, { closed: true })); } }) : null,
          body)));
  }

  ReactDOM.createRoot(document.getElementById("root")).render(h(App));
})();
