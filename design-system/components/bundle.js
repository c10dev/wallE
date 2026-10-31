/* @ds-bundle: {"format":4,"namespace":"WallE","components":[{"name":"Button"},{"name":"Badge"},{"name":"JudgeAnswer"},{"name":"CommandBar"},{"name":"Sidebar"},{"name":"EmptyState"},{"name":"ContextPanel"},{"name":"ScanProgress"},{"name":"Snippet"},{"name":"CommandOutput"},{"name":"Card"},{"name":"StatTile"},{"name":"StatGrid"},{"name":"Tabs"},{"name":"Table"}]} */
(function () {
  var React = window.React, h = React.createElement;
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }

  function Button(p) {
    var variant = p.variant || "secondary", rest = {};
    for (var k in p) if (k !== "variant" && k !== "className") rest[k] = p[k];
    rest.className = cx("we-btn", "we-btn-" + variant, p.className);
    if (!rest.type) rest.type = "button";
    return h("button", rest);
  }

  function Badge(p) {
    return h("span", { className: cx("we-badge", "we-badge-" + (p.tone || "neutral"), p.className) }, p.children);
  }

  var KEEP = {
    a: ["Keep", "Remove"], b: ["Remove", "Keep"], both: ["Keep", "Keep"],
    neither: ["Rewrite", "Rewrite"], ask: ["You choose", "You choose"]
  };
  var KEEP_TONE = { "Keep": "leaf", "Remove": "walle", "Rewrite": "trim", "You choose": "eve" };

  // Rule text, fix and why are markdown-ish: render `code` spans, keep the rest as text.
  function inline(text) {
    var parts = String(text || "").split(/`([^`]+)`/);
    return parts.map(function (t, i) { return i % 2 ? h("code", { key: i, className: "we-ic" }, t) : t; });
  }
  var CLAMP_CHARS = 160;
  // Compact rows keep the end of a path: the folder and file tell candidates apart.
  function shortLoc(r) {
    var seg = String(r.file || "").split("/");
    return (seg.length > 2 ? "…/" + seg.slice(-2).join("/") : r.file) + ":" + r.line;
  }

  function JudgeAnswer(p) {
    var v = p.verdict, refs = p.refs || [], conflict = p.type !== "cost";
    var compact = p.compact === undefined ? !!(v && !v.real) : p.compact;
    var os = React.useState(!compact), open = os[0], setOpen = os[1];
    var es = React.useState(false), full = es[0], setFull = es[1];
    var cs = React.useState(false), copied = cs[0], setCopied = cs[1];
    var head = conflict ? "conflict" + (p.pair ? " · " + p.pair : "") : "cost";
    var state = !v ? ["Not judged", "neutral"] : v.real ? ["Real problem", "walle"] : ["Not a problem", "leaf"];
    var marks = v && conflict && v.real ? KEEP[v.keep] : null;
    var long = refs.some(function (r) { return (r.text || "").length > CLAMP_CHARS; });

    if (!open) {
      var first = refs[0] || {};
      return h("article", { className: "we-judge is-compact" },
        h("button", { type: "button", className: "we-judge-row", "aria-expanded": "false", onClick: function () { setOpen(true); } },
          h("span", { className: "we-judge-kind" }, head),
          h("code", { className: "we-judge-loc", title: first.file + ":" + first.line }, shortLoc(first) + (refs.length > 1 ? " +" + (refs.length - 1) : "")),
          v && v.why ? h("span", { className: "we-judge-why1" }, inline(v.why)) : h("span", { className: "we-judge-why1" }),
          h(Badge, { tone: state[1] }, state[0])));
    }

    function copy() {
      try {
        navigator.clipboard.writeText(p.id).then(function () { setCopied(true); setTimeout(function () { setCopied(false); }, 1500); });
      } catch (e) { /* clipboard blocked: the button stays as is */ }
    }

    return h("article", { className: "we-judge" },
      h("header", { className: "we-judge-head" },
        h("span", { className: "we-judge-kind" }, head),
        h("span", { className: "we-judge-hr" },
          compact ? h("button", { type: "button", className: "we-btn we-btn-ghost we-btn-sm", "aria-expanded": "true", onClick: function () { setOpen(false); setFull(false); } }, "Collapse") : null,
          h(Badge, { tone: state[1] }, state[0]))),
      h("ol", { className: "we-judge-refs" }, refs.map(function (r, i) {
        var m = marks && marks[i];
        return h("li", { key: i, className: "we-judge-ref" },
          conflict ? h("span", { className: "we-judge-ab" }, i ? "B" : "A") : null,
          h("div", { className: "we-judge-rb" },
            h("code", { className: "we-judge-loc" }, r.file + ":" + r.line),
            r.text ? h("div", { className: cx("we-judge-text", long && !full && "is-clamped") }, inline(r.text)) : null),
          m ? h(Badge, { tone: KEEP_TONE[m] }, m) : null);
      })),
      long ? h("button", { type: "button", className: "we-judge-more", "aria-expanded": full ? "true" : "false", onClick: function () { setFull(!full); } },
        full ? "Show less of the rules" : "Show the full rules") : null,
      v && v.fix && v.fix !== "-" ? h("div", { className: "we-judge-fix" },
        h("span", { className: "we-judge-k" }, "fix"), h("p", null, inline(v.fix))) : null,
      v && v.why ? h("p", { className: "we-judge-why" }, inline(v.why)) : null,
      h("footer", { className: "we-judge-foot" },
        h("span", { className: cx("we-eve", !v && "is-busy"), "aria-hidden": "true" }, h("span", { className: "we-eve-eye" })),
        h("span", null, v ? "Judged by " + (p.judge || "agent") : "Waiting for the answer file"),
        p.cached ? h("span", null, "· cached") : null,
        p.id ? h("button", { type: "button", className: "we-btn we-btn-ghost we-btn-sm we-judge-id", title: p.id, onClick: copy }, copied ? "Copied" : "Copy id") : null));
  }

  var LAMP = { online: ["Online", "is-on"], busy: ["Running", "is-busy"], offline: ["Offline", "is-off"] };

  function CommandBar(p) {
    var st = React.useState(p.defaultValue || ""), cmd = st[0], setCmd = st[1];
    var pr = p.project || {}, dv = p.device, lamp = dv ? (LAMP[dv.status] || LAMP.offline) : null;
    var can = !p.running && cmd.trim().length > 0 && !(dv && dv.status === "offline");
    function run(e) {
      if (e) e.preventDefault();
      if (!can) return;
      if (p.onRun) p.onRun("walle " + cmd.trim());
      if (p.clearOnRun) setCmd("");
    }
    return h("form", { className: "we-cmd", onSubmit: run },
      h("dl", { className: "we-cmd-ctx" },
        h("div", { className: "we-cmd-f" }, h("dt", null, "Project"),
          h("dd", null, h("code", null, pr.path || "-"), pr.branch ? h("span", { className: "we-cmd-sub" }, pr.branch) : null)),
        dv ? h("div", { className: "we-cmd-f" }, h("dt", null, "Device"),
          h("dd", null, h("span", { className: cx("we-lamp", lamp[1]), "aria-hidden": "true" }), dv.name,
            h("span", { className: "we-cmd-sub" }, lamp[0] + (dv.detail ? " · " + dv.detail : "")))) : null,
        p.agent ? h("div", { className: "we-cmd-f" }, h("dt", null, "Agent"), h("dd", null, p.agent)) : null),
      h("div", { className: "we-cmd-row" },
        h("span", { className: "we-cmd-prompt", "aria-hidden": "true" }, "$ walle"),
        h("input", {
          className: "we-cmd-input", value: cmd, placeholder: p.placeholder || "scan . --html",
          "aria-label": "walle command", spellCheck: false, autoComplete: "off", disabled: p.running,
          onChange: function (e) { setCmd(e.target.value); }
        }),
        h("button", { type: "submit", className: "we-btn we-btn-primary", disabled: !can }, p.running ? "Running" : "Run")),
      p.hint ? h("p", { className: "we-cmd-hint" }, p.hint) : null);
  }

  function Sidebar(p) {
    var items = p.items || [], groups = [], seen = {};
    items.forEach(function (it) {
      var g = it.group || "";
      if (!seen[g]) { seen[g] = { name: g, items: [] }; groups.push(seen[g]); }
      seen[g].items.push(it);
    });
    return h("nav", { className: "we-side", "aria-label": p.label || "walle navigation" },
      h("div", { className: "we-side-top" },
        h("span", { className: "we-wordmark" }, "walle"),
        h("button", { type: "button", className: "we-side-new", onClick: p.onAction },
          h("svg", { viewBox: "0 0 16 16", width: 16, height: 16, "aria-hidden": "true" },
            h("path", { d: "M8 3v10M3 8h10", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "square" })),
          p.actionLabel || "New scan")),
      h("div", { className: "we-side-list" },
        groups.map(function (g) {
          return h("div", { key: g.name || "_", className: "we-side-group" },
            g.name ? h("div", { className: "we-side-head" }, g.name) : null,
            h("ul", null, g.items.map(function (it) {
              var on = it.id === p.activeId;
              return h("li", { key: it.id },
                h("button", { type: "button", className: cx("we-side-item", on && "is-active"), "aria-current": on ? "page" : undefined,
                  onClick: function () { if (p.onSelect) p.onSelect(it.id); } },
                  h("span", { className: "we-side-t" }, it.title),
                  it.meta != null ? h("span", { className: "we-side-m" }, it.meta) : null));
            })));
        })),
      p.footer ? h("div", { className: "we-side-foot" }, p.footer) : null);
  }

  function EmptyState(p) {
    var sug = p.suggestions || [];
    return h("section", { className: "we-empty" },
      h("svg", { className: "we-empty-mark", viewBox: "0 0 88 40", width: 88, height: 40, "aria-hidden": "true" },
        h("rect", { className: "we-mk-y", x: 0, y: 16, width: 24, height: 24, rx: 2 }),
        h("rect", { className: "we-mk-y", x: 28, y: 16, width: 24, height: 24, rx: 2 }),
        h("rect", { className: "we-mk-y", x: 14, y: -12, width: 24, height: 24, rx: 2, transform: "translate(0 12)" }),
        h("rect", { className: "we-mk-g", x: 60, y: 22, width: 28, height: 12, rx: 6 })),
      h("h1", { className: "we-empty-title" }, p.title || "This folder has no scan yet."),
      p.body ? h("p", { className: "we-empty-body" }, p.body) : null,
      p.children ? h("div", { className: "we-empty-slot" }, p.children) : null,
      sug.length ? h("div", { className: "we-empty-sug" }, sug.map(function (s, i) {
        return h("button", { key: i, type: "button", className: "we-sug", onClick: function () { if (p.onPick) p.onPick(s); } },
          s.kicker ? h("span", { className: "we-sug-k" }, s.kicker) : null,
          h("span", { className: "we-sug-t" }, s.text),
          s.command ? h("code", { className: "we-sug-c" }, s.command) : null);
      })) : null,
      p.note ? h("p", { className: "we-empty-note" }, p.note) : null);
  }

  // ---- ContextPanel: reads `walle context --json` and `walle context --usage --json` as they are ----
  // Sources of both agents fold into four groups. A kind not listed here goes to host·other.
  var CTX_GROUPS = [
    { id: "host", name: "Host and other" },
    { id: "skills", name: "Skill list", src: ["skill_listing", "skill_bodies"] },
    { id: "mcp", name: "MCP and tools", src: ["mcp_tools", "deferred_tools", "mcp_instructions", "tool_namespaces", "plugins_apps"] },
    { id: "instr", name: "Instruction files", src: ["instructions", "agents_md", "memory"] }
  ];
  var CTX_LABEL = {
    system_prompt: "System prompt and host blocks", builtin_tools: "Built-in tool schemas", agent_listing: "Agent list",
    first_prompt: "First prompt", hook_context: "Hook output", other_attachments: "Other attachments",
    skill_listing: "Skill list", mcp_tools: "MCP tool schemas loaded at start", deferred_tools: "Deferred tool names (ToolSearch)",
    mcp_instructions: "MCP server instructions", instructions: "CLAUDE.md, MEMORY.md and rules",
    base_instructions: "Base instructions", agents_md: "AGENTS.md chain", app_context: "Desktop app context",
    tool_namespaces: "Tool namespaces and dynamic tool schemas", plugins_apps: "Plugin and app instructions", permissions: "Permission instructions",
    collaboration_mode: "Collaboration mode", multi_agent: "Multi-agent instructions", memory: "Memory", environment: "Environment context",
    skill_bodies: "Skill bodies the prompt invoked", history: "History before the first call", other: "Other context items"
  };
  var CTX_MEASURED = [["cache_creation", "cache write"], ["cache_read", "cache read"], ["cached", "cached"], ["cache_write", "cache write"], ["input", "input"]];
  var CTX_AGENT = { claude: "Claude Code", codex: "Codex" };
  function fmt(n) {
    if (n == null) return "–";
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
    return Math.round(n).toLocaleString("en-US");
  }
  function pct(n, total) { return total ? (100 * n / total).toFixed(1) + "%" : "–"; }
  function tilde(p) { return p ? String(p).replace(/^\/(home|Users)\/[^/]+/, "~") : p; }
  function day(t) { return t ? String(t).slice(0, 10) : ""; }
  function groupOf(kind) {
    for (var i = 1; i < CTX_GROUPS.length; i++) if (CTX_GROUPS[i].src.indexOf(kind) >= 0) return i;
    return 0;
  }
  // Usage rows of one kind, from the usage object as walle writes it.
  function usageRows(u, tab) {
    if (tab === "skills") return (u.skills || []).map(function (s) {
      return { key: s.skill, name: s.skill, loaded: s.sessions_listed, used: s.sessions_used, unused: null };
    });
    return (u.servers || []).map(function (s) {
      return { key: s.key || s.server, name: s.server, loaded: s.sessions_loaded, used: s.sessions_used,
               unused: s.tokens_unused_est, where: s.used_in && s.used_in[0] ? tilde(s.used_in[0][0]) : null };
    });
  }

  function ContextPanel(p) {
    var ctx = p.context || {}, se = ctx.session || {}, run = ctx.run || {}, fc = ctx.first_call;
    var urun = p.usage || null, u = urun && urun.usage;
    var hv = React.useState(-1), hover = hv[0], setHover = hv[1];
    var al = React.useState(false), all = al[0], setAll = al[1];
    var tb = React.useState("servers"), tab = tb[0], setTab = tb[1];
    var m = (fc && fc.measured) || {}, total = m.total || 0;
    var groups = CTX_GROUPS.map(function (g) { return { id: g.id, name: g.name, rows: [], tokens: 0 }; });
    ((fc && fc.sources) || []).forEach(function (r) {
      var g = groups[groupOf(r.source)]; g.rows.push(r); g.tokens += r.tokens_est || 0;
    });
    var est = fc ? (fc.estimated_total != null ? fc.estimated_total : groups.reduce(function (a, g) { return a + g.tokens; }, 0)) : 0;
    var resid = fc && fc.residual ? fc.residual.tokens : total - est;
    var g = hover >= 0 ? groups[hover] : null;

    var rows = u ? usageRows(u, tab).sort(function (a, b) {
      return ((b.unused || 0) - (a.unused || 0)) || ((b.loaded - b.used) - (a.loaded - a.used));
    }) : [];
    var limit = p.limit || 6, shown = all ? rows : rows.slice(0, limit), n = (u && u.sessions) || 1;
    var period = u ? (day(u.since || (u.window && u.window.first)) + " ~ " + day(u.before || (u.window && u.window.last))) : "";
    var agent = CTX_AGENT[run.agent] || run.agent;

    return h("section", { className: "we-ctx", "aria-label": "Context status" },
      fc ? h("header", { className: "we-ctx-head" },
        h("div", null,
          h("div", { className: "we-ctx-k" }, "First-call context"),
          h("div", { className: "we-ctx-hero" }, fmt(total), h("span", { className: "we-ctx-unit" }, "tokens")),
          h("div", { className: "we-ctx-sub" },
            CTX_MEASURED.filter(function (k) { return m[k[0]] != null && !(k[0] === "input" && run.agent === "codex"); })
              .map(function (k) { return k[1] + " " + fmt(m[k[0]]); }).join(" · ") +
            (p.window ? " · " + pct(total, p.window) + " of the window" : ""))),
        h("dl", { className: "we-ctx-meta" },
          se.cwd ? h("div", null, h("dt", null, "Project"), h("dd", null, h("code", null, tilde(se.cwd)))) : null,
          agent ? h("div", null, h("dt", null, "Agent"), h("dd", null, agent + (se.entrypoint ? " · " + se.entrypoint : "") +
            (se.claude_code_version ? " " + se.claude_code_version : ""))) : null,
          se.model ? h("div", null, h("dt", null, "Model"), h("dd", null, h("code", null, se.model))) : null,
          se.start ? h("div", null, h("dt", null, "Started"), h("dd", null, h("code", null, String(se.start).replace("T", " ").slice(0, 16) + " UTC"))) : null,
          run.estimator ? h("div", null, h("dt", null, "Estimator"), h("dd", null, h("code", null, run.estimator))) : null)) : null,

      fc ? h("div", { className: "we-ctx-block" },
        h("div", { className: "we-ctx-bar", onMouseLeave: function () { setHover(-1); } },
          groups.map(function (gr, i) {
            if (!gr.tokens) return null;
            return h("span", {
              key: gr.id, className: "we-ctx-seg we-ctx-" + gr.id, tabIndex: 0, role: "img",
              "aria-label": gr.name + " " + fmt(gr.tokens) + " tokens, " + pct(gr.tokens, total),
              style: { flexGrow: gr.tokens }, onMouseEnter: function () { setHover(i); },
              onFocus: function () { setHover(i); }, onBlur: function () { setHover(-1); }
            });
          }),
          g ? h("div", { className: "we-ctx-tip", role: "tooltip", style: { left: "clamp(0px, " + (100 * groups.slice(0, hover).reduce(function (a, x) { return a + x.tokens; }, 0) / (est || 1)) + "%, calc(100% - 300px))" } },
            h("strong", null, g.name), h("span", null, fmt(g.tokens) + " tokens · " + pct(g.tokens, total)),
            g.rows.filter(function (r) { return r.tokens_est; }).map(function (r) {
              return h("span", { key: r.source, className: "we-ctx-tip-r" }, (CTX_LABEL[r.source] || r.label) + " " + fmt(r.tokens_est));
            })) : null),
        h("ul", { className: "we-ctx-legend" }, groups.map(function (gr) {
          return h("li", { key: gr.id },
            h("span", { className: "we-ctx-sw we-ctx-" + gr.id, "aria-hidden": "true" }),
            h("span", { className: "we-ctx-ln" }, gr.name),
            h("span", { className: "we-ctx-lv" }, fmt(gr.tokens) + " · " + pct(gr.tokens, total)));
        })),
        h("table", { className: "we-ctx-table" },
          h("thead", null, h("tr", null, h("th", null, "Source"), h("th", null, "Items"), h("th", null, "Est. tokens"), h("th", null, "Share"))),
          h("tbody", null,
            groups.map(function (gr) {
              return gr.rows.map(function (r, j) {
                return h("tr", { key: r.source, className: r.tokens_est ? null : "is-zero" },
                  h("td", null, j === 0 ? h("span", { className: "we-ctx-sw we-ctx-" + gr.id, "aria-hidden": "true" }) : h("span", { className: "we-ctx-sw is-blank" }),
                    CTX_LABEL[r.source] || r.label || r.source),
                  h("td", null, fmt(r.items)), h("td", null, fmt(r.tokens_est)), h("td", null, pct(r.tokens_est, total)));
              });
            }),
            h("tr", { className: "we-ctx-resid" },
              h("td", null, h("span", { className: "we-ctx-sw is-blank" }), "Residual (measured minus estimates)"),
              h("td", null, ""), h("td", null, fmt(resid)), h("td", null, pct(resid, total)))))) : null,

      u ? h("div", { className: "we-ctx-block" },
        h("div", { className: "we-ctx-h2" },
          h("span", { className: "we-ctx-k" }, "Loaded against used"),
          h("span", { className: "we-ctx-sub" }, period + " · " + fmt(u.sessions) + " " + (CTX_AGENT[(urun.run || {}).agent] || "") + " sessions")),
        h("div", { className: "we-ctx-tabs", role: "tablist" },
          [["servers", "MCP servers", (u.servers || []).length], ["skills", "Skills", (u.skills || []).length]].map(function (t) {
            return h("button", { key: t[0], type: "button", role: "tab", "aria-selected": tab === t[0] ? "true" : "false",
              className: cx("we-ctx-tab", tab === t[0] && "is-on"), onClick: function () { setTab(t[0]); setAll(false); } },
              t[1], h("span", { className: "we-ctx-tab-n" }, t[2]));
          })),
        h("div", { className: "we-ctx-key", "aria-hidden": "true" },
          h("span", null, h("i", { className: "we-ctx-k-used" }), "Used"),
          h("span", null, h("i", { className: "we-ctx-k-load" }), tab === "skills" ? "Listed, not used" : "Loaded, not used"),
          h("span", null, h("i", { className: "we-ctx-k-none" }), "Not loaded")),
        h("ol", { className: "we-ctx-usage" }, shown.map(function (r) {
          return h("li", { key: r.key },
            h("div", { className: "we-ctx-un" },
              h("span", { className: "we-ctx-name" }, r.name),
              r.where ? h("span", { className: "we-ctx-where", title: r.where }, "Mostly in " + r.where) : null),
            h("div", { className: "we-ctx-track", role: "img", "aria-label": "Loaded " + r.loaded + ", used " + r.used + ", of " + n },
              h("span", { className: "we-ctx-load", style: { width: (100 * r.loaded / n) + "%" } },
                h("span", { className: "we-ctx-used", style: { width: r.loaded ? (100 * r.used / r.loaded) + "%" : 0 } }))),
            h("div", { className: "we-ctx-nums" },
              h("span", null, "Loaded " + fmt(r.loaded) + " · used " + fmt(r.used)),
              h("span", { className: "we-ctx-waste" }, r.unused != null ? "≈ " + fmt(r.unused) + " tokens loaded, not used"
                : fmt(r.loaded - r.used) + " sessions loaded, not used")));
        })),
        rows.length > limit ? h("button", { type: "button", className: "we-judge-more", onClick: function () { setAll(!all); } },
          all ? "Show the top " + limit + " only" : "Show " + (rows.length - limit) + " more") : null) : null,
      p.note || (ctx.note && fc) ? h("p", { className: "we-ctx-note" }, p.note || "walle: " + ctx.note) : null);
  }

  // ---- ScanProgress: the wait after `walle scan` starts. Five steps of scan.py; one cube per finished step. ----
  var SCAN_STEPS = [
    { id: "collect", name: "Collect instruction files", count: "files", unit: " files" },
    { id: "stack", name: "Build load stacks" },
    { id: "classify", name: "Split and classify lines" },
    { id: "pairs", name: "Find copy and conflict pairs", count: "pairs", unit: " pairs" },
    { id: "triage", name: "Sort Start here", count: "items", unit: " items" }
  ];
  // Cube places in an 88 x 56 box: three on the floor, two on top, in the order the steps finish.
  var SCAN_CUBES = [[4, 30], [32, 30], [60, 30], [18, 4], [46, 4]];

  function ScanProgress(p) {
    var steps = SCAN_STEPS, n = steps.length, counts = p.counts || {};
    var at = typeof p.step === "string" ? steps.map(function (x) { return x.id; }).indexOf(p.step) : (p.step || 0);
    var done = !!p.done || at >= n;
    if (done) at = n;
    var roots = (p.roots || []).map(tilde);
    return h("section", { className: cx("we-scan", done && "is-done"), "aria-busy": done ? "false" : "true" },
      h("svg", { className: "we-scan-mark", viewBox: "0 0 88 56", width: 88, height: 56, "aria-hidden": "true" },
        SCAN_CUBES.map(function (c, i) {
          return h("rect", { key: i, x: c[0], y: c[1], width: 22, height: 22, rx: 2,
            className: i < at ? "we-cube is-done" : i === at ? "we-cube is-now" : "we-cube" });
        })),
      h("h1", { className: "we-scan-title" }, done ? "Scan done" : "Scanning"),
      roots.length ? h("p", { className: "we-scan-roots" }, roots.map(function (r, i) { return h("code", { key: i }, r); })) : null,
      h("ol", { className: "we-scan-steps", "aria-live": "polite" }, steps.map(function (st, i) {
        var state = i < at ? "done" : i === at ? "now" : "todo";
        var c = st.count && counts[st.count] != null ? fmt(counts[st.count]) + st.unit : null;
        return h("li", { key: st.id, className: "is-" + state, "aria-current": state === "now" ? "step" : undefined },
          h("span", { className: "we-scan-dot", "aria-hidden": "true" }),
          h("span", { className: "we-scan-name" }, st.name),
          h("span", { className: "we-scan-state" },
            state === "done" ? (c || "Done") : state === "now" ? (c ? c + "…" : "Running") : "Waiting"));
      })),
      h("div", { className: "we-scan-foot" },
        h("span", { className: "we-scan-time" },
          "Step " + (done ? at : at + 1) + " of " + n + (p.elapsed != null ? " · " + Number(p.elapsed).toFixed(1) + " s" : "")),
        !done && p.onCancel ? h(Button, { variant: "ghost", className: "we-btn-sm", onClick: p.onCancel }, "Cancel") : null,
        done && p.children ? p.children : null));
  }

  // Copy to the clipboard in a secure context. Else select the text, so the person can copy it with the keyboard.
  // The message clears first and comes back a moment later, so a screen reader hears each press.
  function useCopy(ref, text) {
    var ms = React.useState(""), msg = ms[0], setMsg = ms[1], timers = React.useRef([]), live = React.useRef(true);
    React.useEffect(function () {
      live.current = true;
      return function () { live.current = false; timers.current.forEach(clearTimeout); };
    }, []);
    function show(m, ms) {
      if (!live.current) return;
      timers.current.forEach(clearTimeout);
      setMsg("");
      timers.current = [setTimeout(function () { setMsg(m); }, 50), setTimeout(function () { setMsg(""); }, 50 + ms)];
    }
    function select() {
      if (!live.current || !ref.current) return;
      var r = document.createRange(); r.selectNodeContents(ref.current);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      show("Selected", 4000);
    }
    function copy() {
      if (!(navigator.clipboard && window.isSecureContext)) return select();
      navigator.clipboard.writeText(text).then(function () { show("Copied", 1500); }, select);
    }
    return [msg, copy];
  }

  function Snippet(p) {
    var ref = React.useRef(null), c = useCopy(ref, p.text), msg = c[0];
    return h("div", { className: cx("we-snippet", p.face === "surface" && "is-face-surface", p.className) },
      h("code", { ref: ref, className: "we-snippet-text" }, p.text),
      h(Button, { variant: "ghost", className: "we-btn-sm", onClick: c[1], "aria-label": "Copy " + p.text }, msg || "Copy"),
      h("span", { className: "we-sr", role: "status" }, msg === "Selected" ? "Selected. Copy it with the keyboard." : msg));
  }

  function outState(p) {
    if (p.refused) return ["walle", "Refused"];
    if (p.running) return ["eve", "Running"];
    if (p.code === 0) return ["leaf", "Exit 0"];
    if (p.code != null) return ["walle", "Exit " + p.code];
    return ["walle", p.cancelled ? "Cancelled" : "Error"];
  }

  function CommandOutput(p) {
    var st = outState(p), text = (p.lines || []).join("\n");
    // One live region, filled after mount: a panel that shows first with its final state is still announced.
    var said = st[1] + (p.error ? ": " + p.error : "");
    var ns = React.useState(""), news = ns[0], setNews = ns[1];
    React.useEffect(function () { setNews(said); }, [said]);
    var action = p.running ? (p.onCancel ? h(Button, { variant: "ghost", className: "we-btn-sm we-out-end", onClick: p.onCancel }, "Cancel") : null)
      : (p.onClose ? h(Button, { variant: "ghost", className: "we-btn-sm we-out-end", onClick: p.onClose }, "Close") : null);
    return h("section", { className: cx("we-out", p.className), "aria-label": "Command output" },
      h("span", { className: "we-sr", role: "status" }, news),
      h("div", { className: "we-out-head" },
        h("span", { className: "we-out-k" }, "Output"),
        h("code", { className: "we-out-cmd" }, p.command),
        h(Badge, { tone: st[0] }, st[1]),
        action),
      !p.refused && p.cwd ? h("p", { className: "we-out-where" },
        "In " + p.cwd + (p.seconds != null ? " · " + Number(p.seconds).toFixed(1) + " s" : "")) : null,
      p.error ? h("p", { className: "we-out-err" }, p.error) : null,
      text ? h("pre", { className: "we-out-text", tabIndex: 0, "aria-label": "Output of " + p.command }, text) : null);
  }

  function rest(p, omit) {
    var o = {};
    for (var k in p) if (omit.indexOf(k) < 0) o[k] = p[k];
    return o;
  }

  function Card(p) {
    var o = rest(p, ["as", "className", "children"]);
    o.className = cx("we-card", p.className);
    return h(p.as || "div", o, p.children);
  }

  function StatTile(p) {
    return h("div", { className: cx("we-stat", p.className) },
      h("span", { className: "we-stat-k" }, p.label),
      h("div", { className: "we-stat-v" }, p.badge || null, h("span", { className: "we-stat-n" }, p.value), p.tag || null),
      p.note ? h("p", { className: "we-stat-note" }, p.note) : null);
  }

  function StatGrid(p) {
    return h("div", { className: cx("we-stats", p.className), role: p.label ? "group" : undefined, "aria-label": p.label }, p.children);
  }

  // Tabs with one tab stop: Left, Right, Home and End move the selection and the focus.
  function Tabs(p) {
    var auto = React.useId(), items = p.items || [], refs = React.useRef({});
    var base = p.panelId || "we-tabs" + auto.replace(/[^A-Za-z0-9_-]/g, "");
    function pick(i) {
      var it = items[(i + items.length) % items.length];
      if (!it) return;
      p.onChange && p.onChange(it.id);
      var el = refs.current[it.id];
      if (el) el.focus();
    }
    function key(e, i) {
      var to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: items.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      pick(to);
    }
    return h("div", { className: cx("we-tabs", p.className), role: "tablist", "aria-label": p.label },
      items.map(function (it, i) {
        var on = it.id === p.value;
        return h("button", {
          key: it.id, type: "button", role: "tab", id: base + "-" + it.id, "aria-selected": on ? "true" : "false",
          "aria-controls": p.panelId, tabIndex: on ? 0 : -1, className: cx("we-tab", on && "is-on"),
          ref: function (el) { refs.current[it.id] = el; },
          onClick: function () { p.onChange && p.onChange(it.id); }, onKeyDown: function (e) { key(e, i); }
        }, it.label, it.count != null ? h("span", { className: "we-tab-n" }, it.count) : null);
      }));
  }

  // A data table. On a narrow screen (860px or less) each row becomes a block, and each cell shows its column label.
  // A marked row also says so in hidden text, for a screen reader.
  function Table(p) {
    var cols = p.columns || [], markedLabel = p.markedLabel || "marked";
    var colClass = function (c) { return c.type === "num" ? "is-num" : c.type === "time" ? "is-time" : null; };
    return h("div", { className: cx("we-table-wrap", p.className) },
      h("table", { className: cx("we-table", p.stack !== false && "is-stack") },
        p.caption ? h("caption", { className: "we-sr" }, p.caption) : null,
        h("thead", null, h("tr", null, cols.map(function (c) { return h("th", { key: c.key, scope: "col", className: colClass(c) }, c.label); }))),
        h("tbody", null, (p.rows || []).map(function (r) {
          return h("tr", { key: r.key, className: r.marked ? "is-marked" : null },
            cols.map(function (c, i) {
              var v = r.cells[c.key], bare = c.stackLabel === false || (r.unlabeled || []).indexOf(c.key) >= 0;
              return h("td", { key: c.key, className: colClass(c), "data-l": bare ? undefined : c.label },
                v == null ? "" : v, i === 0 && r.marked ? h("span", { className: "we-sr" }, " (" + markedLabel + ")") : null);
            }));
        }))));
  }

  window.WallE = Object.assign(window.WallE || {}, { Button: Button, Badge: Badge, JudgeAnswer: JudgeAnswer, CommandBar: CommandBar, Sidebar: Sidebar, EmptyState: EmptyState, ContextPanel: ContextPanel, ScanProgress: ScanProgress, Snippet: Snippet, CommandOutput: CommandOutput, Card: Card, StatTile: StatTile, StatGrid: StatGrid, Tabs: Tabs, Table: Table });
})();
