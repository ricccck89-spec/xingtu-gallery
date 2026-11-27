const $ = (id) => document.getElementById(id);

// 由服务端静态文件 web/build.txt 提供；和 /api/health 里的 build 对不上，说明后台跑的还是旧进程
let BUILD = "";

async function loadBuild() {
  try {
    const res = await fetch("/build.txt");
    BUILD = res.ok ? (await res.text()).trim() : "";
  } catch (e) {
    BUILD = "";
  }
}

const state = {
  mode: "work",
  items: [],
  jobId: null,
  poll: null,
  running: false,
  connected: false,
  settings: null,
  qualityHigh: false,
  bestImage: true,
  parseJob: null,
  parseAbort: false,
  lastFolder: null,
  tab: "post",
  view: "gallery",
  discover: {
    platform: "xhs",
    category: "homefeed.fashion_v3",
    cursorScore: "",
    noteIndex: 0,
    loaded: false,
    loading: false,
    filterIrrelevant: true,
    blockWords: localStorage.getItem("douk_xhs_block_words") || "",
    hiddenIds: new Set(JSON.parse(localStorage.getItem("douk_xhs_hidden_ids") || "[]")),
    xhsItems: [],
    dyCategory: "beauty",
    dyOffset: 0,
    dyLoaded: false,
    dyLoading: false,
    dyItems: [],
  },
  creators: [],
  selected: new Set(),
  failedIds: [],
  manifestFilter: "all",
  manifestLimit: 200,
  jobItems: {},
  gallery: {
    loaded: false, loading: false, dirty: true,
    type: "all", folder: "", q: "", sort: "newest",
    posts: [], offset: 0, limit: 60, hasMore: false,
    counts: { types: {} }, stats: null, creators: [],
  },
  lib: {
    key: null, folder: "", path: "", nickname: "", sec_uid: "", url: "",
    tab: "post", posts: [], filter: "all", q: "", sort: "newest", shown: 60,
    files: 0, bytes: 0, exists: false,
  },
  viewer: { files: [], index: 0, title: "", video: null, creator: "" },
  live: { items: [], tools: null, id: null, running: false, poll: null, shown: 300, lastResp: null },
  frame: { items: [], selected: null, kind: "all", q: "", scanToken: 0, loaded: false, busy: false, out: "" },
  tool: "live",
};

const PLACEHOLDER = {
  work: "把抖音 / 小红书作品链接粘贴到这里，或直接粘贴分享文案",
  user: "粘贴抖音或小红书博主主页链接，例如 https://www.xiaohongshu.com/user/profile/...",
};

const HINT = {
  work: "支持抖音 / 小红书，例如：https://v.douyin.com/xxxxxxx/ 或 https://xhslink.com/...",
  user: "支持抖音 / 小红书博主主页，例如：https://www.douyin.com/user/... 或 https://www.xiaohongshu.com/user/profile/...",
};

const STATUS_TEXT = {
  pending: "等待中",
  downloading: "下载中",
  done: "完成",
  skip: "已存在",
  error: "失败",
};

async function api(path, body) {
  const opt = body === undefined
    ? { method: "GET" }
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
  const res = await fetch(path, opt);
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch (e) {
    if (res.status === 404) {
      throw new Error("接口不存在，界面服务还是旧版本：先双击「星图画廊」重启一次");
    }
    throw new Error(`服务返回了非 JSON 内容（HTTP ${res.status}）`);
  }
}

/* ---------------- 连接状态 ---------------- */

async function checkHealth() {
  const el = $("status");
  const txt = $("statusText");
  try {
    const r = await api("/api/health");
    state.connected = !!r.ok;
    el.className = r.ok ? "status is-ok" : "status is-bad";
    txt.textContent = r.ok ? "已连接" : "未连接 · 点此启动";
    el.title = r.ok ? (r.engine ? `内置引擎: ${r.engine}` : "已连接") : "解析引擎未就绪";
    // 旧版服务没有 build 字段；有字段但和 build.txt 不一致，也说明后台没重启
    $("staleBanner").hidden = !(r.ok && (!r.build || (BUILD && r.build !== BUILD)));
  } catch (e) {
    state.connected = false;
    el.className = "status is-bad";
    txt.textContent = "服务未启动";
  }
}

/* ---------------- 模式切换 ---------------- */

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => {
    const on = b.dataset.mode === mode;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("link").placeholder = PLACEHOLDER[mode];
  $("hint").textContent = HINT[mode];
  $("hint").className = "hint";
  $("quickWrap").hidden = mode !== "user";
}

document.querySelectorAll(".mode").forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});

/* ---------------- 提示 ---------------- */

function showHint(msg, isError) {
  const h = $("hint");
  h.textContent = msg;
  h.className = isError ? "hint is-error" : "hint";
}

/* ---------------- 预览图 ---------------- */

function thumbURL(path, width, v) {
  const q = new URLSearchParams({ path: String(path || ""), w: String(width || 160) });
  if (v) q.set("v", String(v));
  return "/api/thumb?" + q.toString();
}

function fillThumb(box, src, opts) {
  const o = opts || {};
  box.classList.remove("is-ready", "is-empty");
  box.innerHTML = "";
  if (!src) {
    box.classList.add("is-empty");
    return;
  }
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  if (o.noImgDrag) img.draggable = false;
  if (/^https?:/i.test(src)) {
    // 还没下载时先用抖音的封面图，拿不到就留占位
    img.referrerPolicy = "no-referrer";
    img.src = src;
  } else {
    img.src = thumbURL(src, o.width || 160, o.v);
  }
  img.addEventListener("load", () => box.classList.add("is-ready"));
  img.addEventListener("error", () => {
    img.remove();
    // 成品封面读不出来（旧版 HEIC 之类）就退回源视频那一帧
    if (o.fallback && o.fallback !== src) {
      fillThumb(box, o.fallback, { width: o.width });
      return;
    }
    box.classList.add("is-empty");
  });
  box.append(img);
}

function thumbEl(src, opts) {
  const box = document.createElement("span");
  box.className = "thumb";
  fillThumb(box, src, opts);
  return box;
}

/* ---------------- 解析 ---------------- */

let parseTimer = null;

// 解析博主主页可能要几十秒，先把结果区变成「正在读取」，带秒表，别让人以为卡死了
function startParseUI() {
  $("result").hidden = false;
  $("intro").classList.add("is-hidden");
  $("resultTitle").textContent = "正在解析";
  $("resultMeta").textContent = "";
  $("userTabs").hidden = true;
  $("picker").hidden = true;
  $("manifestFilters").hidden = true;
  $("manifestMore").hidden = true;
  $("library").hidden = true;
  $("toLive").hidden = true;
  $("cancel").hidden = true;
  $("retryFailed").hidden = true;
  state.failedIds = [];
  state.jobItems = {};
  $("barWrap").hidden = true;
  $("downloadAll").disabled = true;
  $("downloadAll").textContent = "下载";
  $("manifest").innerHTML = "";
  $("loading").hidden = false;
  state.parseAbort = false;
  $("parseStop").hidden = false;
  $("parseStop").disabled = false;
  $("parseStop").textContent = "停止解析";
  $("parseBar").hidden = false;
  $("loadNote").textContent = "";

  const t0 = Date.now();
  const tick = () => {
    if (state.parseJob && state.parseJob !== "starting") return;  // 有任务时进度由 watchParse 写
    const s = Math.floor((Date.now() - t0) / 1000);
    $("loadText").textContent = s < 15
      ? `正在读取作品列表… 已等 ${s} 秒`
      : `还在读，作品有点多… 已等 ${s} 秒`;
  };
  tick();
  clearInterval(parseTimer);
  parseTimer = setInterval(tick, 500);
}

function stopParseUI() {
  clearInterval(parseTimer);
  parseTimer = null;
  $("loading").hidden = true;
  $("parseStop").hidden = true;
  $("parseStop").disabled = false;
  $("parseStop").textContent = "停止解析";
  $("parseBar").hidden = true;
  $("loadNote").textContent = "";
}

// 停止解析：后端每隔 0.35 秒看一次这个标记，正在等的那次请求也会立刻收手
async function stopParse() {
  if (!state.parseJob || state.parseAbort) return;
  state.parseAbort = true;
  $("parseStop").disabled = true;
  $("parseStop").textContent = "正在停止…";
  $("loadText").textContent = "正在停止，已经取到的作品会保留…";
  $("loadNote").textContent = "";
  if (state.parseJob === "starting") return;  // job id 还没回来，doParse 拿到后补发
  try {
    await api("/api/cancel", { id: state.parseJob });
  } catch (e) {
    /* 就算请求失败，本地也已经不再显示了；下次解析会重新开始 */
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// 解析是一个后台任务：服务端一页一页翻、每页落盘，这里只负责轮询进度
async function watchParse(jid) {
  const t0 = Date.now();
  let last = null;
  while (true) {
    await sleep(700);
    let r;
    try {
      r = await api("/api/job?id=" + encodeURIComponent(jid));
    } catch (e) {
      if (Date.now() - t0 > 20 * 60 * 1000) throw e;
      continue;
    }
    if (!r.ok) throw new Error(r.message || "任务不存在");
    last = r.job;
    setParseProgress(last, t0);
    if (!last.running) break;
  }
  const d = await api("/api/works/items?id=" + encodeURIComponent(jid));
  return { job: last, items: d.items || [] };
}

function setParseProgress(job, t0) {
  const s = Math.floor((Date.now() - t0) / 1000);
  const bits = [];
  if (job.phase) bits.push(job.phase);
  if (job.cached) bits.push(`缓存 ${job.cached} 个`);
  bits.push(`已取 ${job.count || 0} 个`);
  if (job.pages) bits.push(`第 ${job.pages} 段`);
  bits.push(state.parseAbort ? "正在停止" : `已等 ${s} 秒`);
  $("loadText").textContent = bits.join(" · ");
  $("loadNote").textContent = state.parseAbort
    ? "停止后会保留已经取到的作品"
    : (job.note || "");
}

async function doParse() {
  const text = $("link").value.trim();
  if (!text) {
    showHint("先粘贴一个链接", true);
    return;
  }
  if (state.parseJob) {
    showHint("上一个还在解析，等它跑完再换链接", true);
    return;
  }
  if (state.running) {
    showHint("正在下载，等下载完再解析新的", true);
    return;
  }

  state.parseJob = "starting";
  $("go").disabled = true;
  $("go").textContent = "解析中";
  showHint("正在解析：会一段段往回翻并随时保存，作品多的博主需要几分钟；中途断了也留得住已经拿到的，随时可以点「停止解析」。", false);
  startParseUI();

  try {
    const recentDays = state.mode === "user" && $("quickParse").checked ? 30 : 0;
    const r = await api("/api/works/start", { text, tab: state.tab, recent_days: recentDays });
    if (!r.ok) {
      showHint(r.message || "解析失败，检查链接和 Cookie 是否有效", true);
      stopParseUI();
      $("result").hidden = true;
      $("intro").classList.remove("is-hidden");
      return;
    }
    state.parseJob = r.job;
    if (r.tab) state.tab = r.tab;
    // 点停止时 job id 可能还没回来，这里补发一次取消
    if (state.parseAbort) {
      try {
        await api("/api/cancel", { id: r.job });
      } catch (e) {
        /* 忽略 */
      }
    }

    const { job, items } = await watchParse(r.job);
    const kind = job.target_kind || r.target?.kind || "user";
    if (!items.length) {
      stopParseUI();
      $("result").hidden = true;
      $("intro").classList.remove("is-hidden");
      showHint(job.message || job.reason || "解析失败，检查链接和 Cookie 是否有效", true);
      return;
    }

    state.items = items;
    setMode(kind === "user" ? "user" : "work");
    stopParseUI();
    renderResult({
      kind, items, nickname: job.nickname || "",
      tab: job.tab || state.tab, url: job.url || "",
    });
    // 解析到主页时后端已经记下这个博主，刷新一下卡片
    loadCreators();

    // 分享文案里常常夹着一堆中文，解析成功后把输入框换成干净的网址
    const clean = (job.url || "").trim();
    if (job.reason === "已停止解析") {
      showHint(`已停止解析：保留已经取到的 ${items.length} 个作品，可以只下载这些`, false);
    } else if (clean && $("link").value.trim() !== clean) {
      $("link").value = clean;
      showHint("已自动提取链接：" + clean, false);
    } else if (job.reason === "没有更多作品") {
      showHint(`解析完成：共 ${items.length} 个作品（已经翻到底）`, false);
    } else if (job.reason) {
      showHint(`解析完成（${job.reason}）：共 ${items.length} 个作品`, false);
    } else {
      showHint(HINT[state.mode], false);
    }
  } catch (e) {
    stopParseUI();
    $("result").hidden = true;
    $("intro").classList.remove("is-hidden");
    showHint("解析失败：" + e.message, true);
  } finally {
    state.parseJob = null;
    state.parseAbort = false;
    $("go").disabled = false;
    $("go").textContent = "解析";
  }
}

function typeLabel(t) {
  return ["视频", "图集", "实况"].includes(t) ? t : "其他";
}

function renderResult(r) {
  const isUser = r.kind === "user";
  $("loading").hidden = true;

  const missingCount = state.items.filter((it) => !it.exists).length;
  const existsCount = state.items.length - missingCount;
  $("resultTitle").textContent = isUser ? `${r.nickname || "博主"} · ${state.tab === "favorite" ? "收藏" : "发布"}作品` : "已解析作品";
  $("resultMeta").textContent = isUser
    ? `${state.items.length} 个作品${existsCount ? `（本地已有 ${existsCount} · 待下 ${missingCount}）` : ""}`
    : `${state.items[0]?.create_time || ""} · ${typeLabel(state.items[0]?.type || "")}`;

  $("userTabs").hidden = !isUser;
  $("toLive").hidden = !state.items.some((it) => it.type === "实况");
  if (isUser) {
    $("userTabs").querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("is-on", b.dataset.tab === state.tab);
    });
  }

  // 默认全选（如有本地已存在作品时仍保留全选/或可一键只选未下载）
  state.selected = new Set(state.items.map((it) => it.id));
  state.manifestFilter = "all";
  state.manifestLimit = 200;
  state.jobItems = {};
  $("picker").hidden = !isUser;
  $("manifestFilters").hidden = !isUser;
  updateManifestFilterTabs();
  updateSelUI();

  $("cancel").hidden = true;
  $("retryFailed").hidden = true;
  state.failedIds = [];
  $("barWrap").hidden = true;
  $("barFill").style.width = "0%";

  $("manifest").className = "manifest";
  renderManifest();

  $("intro").classList.add("is-hidden");
  $("result").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateManifestFilterTabs() {
  const total = state.items.length;
  const existsCount = state.items.filter((it) => it.exists).length;
  const missingCount = total - existsCount;
  document.querySelectorAll("#manifestFilters .tab").forEach((b) => {
    const f = b.dataset.mf;
    const n = f === "all" ? total : (f === "missing" ? missingCount : existsCount);
    const label = f === "all" ? "全部" : (f === "missing" ? "未下载" : "已存在");
    b.textContent = `${label} ${n}`;
    b.classList.toggle("is-on", f === state.manifestFilter);
  });
  const pickMissing = $("pickMissing");
  if (pickMissing) pickMissing.disabled = state.running || !missingCount;
}

function filteredManifestItems() {
  if (state.manifestFilter === "missing") return state.items.filter((it) => !it.exists);
  if (state.manifestFilter === "exists") return state.items.filter((it) => !!it.exists);
  return state.items;
}

function renderManifest() {
  const isUser = state.mode === "user";
  const list = $("manifest");
  list.innerHTML = "";
  const filtered = filteredManifestItems();
  const shown = filtered.slice(0, state.manifestLimit);

  if (!shown.length) {
    list.append(mk("div", "empty", "当前筛选下没有作品。"));
    $("manifestMore").hidden = true;
    return;
  }

  for (const it of shown) {
    const row = document.createElement("div");
    row.className = "row" + (isUser ? " has-check" : "");
    row.dataset.id = it.id;

    const jobInfo = state.jobItems[it.id];
    const thumbSrc = (jobInfo && jobInfo.file && (jobInfo.status === "done" || jobInfo.status === "skip"))
      ? jobInfo.file
      : (it.cover || "");
    const pic = thumbEl(thumbSrc);
    if (thumbSrc && !/^https?:/i.test(thumbSrc)) pic.dataset.src = thumbSrc;

    const type = document.createElement("span");
    type.className = "type";
    type.dataset.type = typeLabel(it.type);
    type.textContent = typeLabel(it.type);

    const title = document.createElement("span");
    title.className = "title";
    title.textContent = it.desc || it.id;
    title.title = it.desc || it.id;

    const date = document.createElement("span");
    date.className = "date";
    date.textContent = (it.create_time || "").slice(0, 10);

    const st = document.createElement("span");
    st.className = "state";
    if (jobInfo) {
      st.dataset.state = jobInfo.status;
      st.textContent = jobInfo.message || STATUS_TEXT[jobInfo.status] || "";
    } else if (it.exists) {
      st.dataset.state = "skip";
      st.textContent = "已存在";
    } else if (it.download_state === "partial") {
      st.dataset.state = "partial";
      st.textContent = "部分下载";
    } else {
      st.textContent = "";
    }

    if (isUser) {
      const pick = document.createElement("input");
      pick.type = "checkbox";
      pick.className = "pick";
      pick.checked = state.selected.has(it.id);
      pick.setAttribute("aria-label", "选择这个作品");
      pick.addEventListener("click", (e) => e.stopPropagation());
      pick.addEventListener("change", () => {
        if (pick.checked) state.selected.add(it.id);
        else state.selected.delete(it.id);
        updateSelUI();
      });
      row.append(pick, pic, type, title, date, st);
      row.addEventListener("click", () => {
        if (state.running) return;
        pick.checked = !pick.checked;
        pick.dispatchEvent(new Event("change"));
      });
    } else {
      row.append(pic, type, title, date, st);
    }
    list.append(row);
  }

  const remaining = filtered.length - shown.length;
  $("manifestMore").hidden = remaining <= 0;
  if (remaining > 0) {
    $("manifestMore").textContent = `加载更多（还有 ${remaining} 个作品）`;
  }
}

/* ---------------- 下载 ---------------- */

function setRowState(id, status, message, file) {
  state.jobItems[id] = { id, status, message, file };
  if (status === "done" || status === "skip") {
    const item = state.items.find((x) => x.id === id);
    if (item) {
      item.exists = true;
      item.download_state = "all";
    }
  }
  const row = $("manifest").querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (!row) return;
  const st = row.querySelector(".state");
  st.dataset.state = status;
  st.textContent = message || STATUS_TEXT[status] || "";

  const box = row.querySelector(".thumb");
  if (box && file && (status === "done" || status === "skip") && box.dataset.src !== file) {
    box.dataset.src = file;
    fillThumb(box, file, { width: 120 });
  }
}

function updateSelUI() {
  const isUser = state.mode === "user";
  const total = state.items.length;
  const n = state.selected.size;

  $("selMeta").textContent = isUser ? `已选 ${n} / 共 ${total}` : "";
  const all = $("selectAll");
  all.checked = isUser && n > 0 && n === total;
  all.indeterminate = isUser && n > 0 && n < total;

  const btn = $("downloadAll");
  btn.disabled = state.running || !n;
  if (state.running) return;
  btn.textContent = !n ? "没选作品" : (isUser ? `下载选中 ${n}` : "下载");
}

$("selectAll").addEventListener("change", () => {
  const all = $("selectAll").checked;
  state.selected = all ? new Set(state.items.map((it) => it.id)) : new Set();
  document.querySelectorAll("#manifest .pick").forEach((c) => { c.checked = all; });
  updateSelUI();
});

$("manifestFilters").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (!b) return;
  state.manifestFilter = b.dataset.mf || "all";
  state.manifestLimit = 200;
  updateManifestFilterTabs();
  renderManifest();
});

$("pickMissing").addEventListener("click", () => {
  if (state.running) return;
  state.selected = new Set(state.items.filter((it) => !it.exists).map((it) => it.id));
  document.querySelectorAll("#manifest .row").forEach((row) => {
    const pick = row.querySelector(".pick");
    if (pick) pick.checked = state.selected.has(row.dataset.id);
  });
  updateSelUI();
});

$("manifestMore").addEventListener("click", () => {
  state.manifestLimit += 200;
  renderManifest();
});

async function doDownload() {
  if (!state.items.length || state.running) return;
  const picked = state.items.filter((it) => state.selected.has(it.id));
  if (!picked.length) {
    showHint("先勾选要下载的作品", true);
    return;
  }

  state.running = true;
  state.failedIds = [];
  $("retryFailed").hidden = true;
  $("downloadAll").disabled = true;
  $("cancel").hidden = false;
  $("barWrap").hidden = false;
  $("manifest").classList.add("show-state");
  for (const it of picked) setRowState(it.id, "pending", "");

  try {
    const r = await api("/api/download", {
      items: picked,
      root: currentRoot() || state.settings?.config?.root,
      live_folder: state.settings?.config?.live_folder,
      video_folder: state.settings?.config?.video_folder,
      quality: state.qualityHigh ? "high" : "normal",
      tab: state.tab,
    });
    if (!r.ok) {
      showHint(r.message || "启动下载失败", true);
      state.running = false;
      $("cancel").hidden = true;
      updateSelUI();
      return;
    }
    state.jobId = r.job;
    // 下载开始时后端会更新名单里的下载时间和次数
    loadCreators();
    pollJob();
  } catch (e) {
    showHint("请求失败：" + e.message, true);
    state.running = false;
    $("cancel").hidden = true;
    updateSelUI();
  }
}

async function pollJob() {
  clearTimeout(state.poll);
  if (!state.jobId) return;

  try {
    const r = await api("/api/job?id=" + encodeURIComponent(state.jobId));
    if (!r.ok) return;
    const job = r.job;

    for (const it of Object.values(job.items)) {
      setRowState(it.id, it.status, it.message, it.file);
    }
    $("barFill").style.width = `${Math.round((job.finished / Math.max(job.total, 1)) * 100)}%`;

    if (job.running) {
      $("downloadAll").textContent = `下载中 ${job.finished}/${job.total}`;
      state.poll = setTimeout(pollJob, 700);
      return;
    }

    const done = Object.values(job.items).filter((x) => x.status === "done").length;
    const skip = Object.values(job.items).filter((x) => x.status === "skip").length;
    const failed = Object.values(job.items).filter((x) => x.status === "error");
    const err = failed.length;

    state.running = false;
    state.failedIds = failed.map((x) => x.id);
    $("cancel").hidden = true;
    $("retryFailed").hidden = err === 0;
    if (err > 0) $("retryFailed").textContent = `重试失败 ${err}`;
    updateSelUI();
    const first = Object.values(job.items).find((x) => x.file);
    if (first) state.lastFolder = folderOf(first.file);
    $("resultMeta").textContent = `完成 ${done} · 已存在 ${skip} · 失败 ${err}`;
    updateManifestFilterTabs();
    // 刚下完，博主卡片和作品数会变；画廊/博主页开着就顺手刷新
    state.gallery.dirty = true;
    if (state.view === "gallery") loadGallery(true);
    if (state.view === "creator" && state.lib.key) refreshLibrary(true);
  } catch (e) {
    state.poll = setTimeout(pollJob, 1200);
  }
}

/* ---------------- 设置 ---------------- */

async function loadSettings() {
  try {
    const r = await api("/api/settings");
    state.settings = r;
    state.qualityHigh = !!r.app?.original_quality;
    $("setRoot").value = r.config?.root || "";
    $("setLive").value = r.config?.live_folder || "实况";
    $("setVideo").value = r.config?.video_folder || "视频";
    $("setFolderTpl").value = r.config?.folder_template || "UID{uid}_{nickname}_发布作品";
    $("setWorkers").value = r.config?.workers || 3;
    $("setXhsCookie").value = r.config?.xhs_cookie || "";
    previewTpl();
    $("destPath").value = r.config?.root || "";
    $("destPath").title = r.config?.root || "";
    setQuality(state.qualityHigh);
    setBestImage(r.config?.best_image !== false);
  } catch (e) {
    $("destPath").placeholder = "读取失败";
  }
}

function previewTpl() {
  const tpl = $("setFolderTpl").value || "UID{uid}_{nickname}_发布作品";
  const out = tpl
    .replace(/\{uid\}/g, "1234567890")
    .replace(/\{sec_uid\}/g, "MS4wLjABAAAA…")
    .replace(/\{nickname\}/g, "某博主")
    .replace(/\{date\}/g, new Date().toISOString().slice(0, 10));
  $("tplPreview").textContent = out.trim() || "—";
}

$("setFolderTpl").addEventListener("input", previewTpl);

$("pickRoot").addEventListener("click", async () => {
  const btn = $("pickRoot");
  btn.disabled = true;
  btn.textContent = "选择中…";
  try {
    const r = await api("/api/pickfolder", { initial: $("setRoot").value.trim() });
    if (r.ok) $("setRoot").value = r.path;
  } catch (e) {
    showHint("打开选择框失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "浏览…";
  }
});

function setQuality(on) {
  state.qualityHigh = on;
  const s = $("setQuality");
  s.classList.toggle("is-on", on);
  s.setAttribute("aria-checked", on ? "true" : "false");
}

$("setQuality").addEventListener("click", () => setQuality(!state.qualityHigh));

function setBestImage(on) {
  state.bestImage = on;
  const s = $("setBestImage");
  s.classList.toggle("is-on", on);
  s.setAttribute("aria-checked", on ? "true" : "false");
}

$("setBestImage").addEventListener("click", () => setBestImage(!state.bestImage));

$("openSettings").addEventListener("click", () => {
  loadSettings().then(() => $("settings").showModal());
});

$("saveSettings").addEventListener("click", async (ev) => {
  ev.preventDefault();
  const payload = {
    root: $("setRoot").value.trim(),
    live_folder: $("setLive").value.trim() || "实况",
    video_folder: $("setVideo").value.trim() || "视频",
    folder_template: $("setFolderTpl").value.trim() || "UID{uid}_{nickname}_发布作品",
    workers: Number($("setWorkers").value) || 3,
    quality: state.qualityHigh ? "high" : "normal",
    best_image: state.bestImage,
    xhs_cookie: $("setXhsCookie").value.trim(),
  };
  await api("/api/settings", payload);
  await api("/api/apply-quality", { high: state.qualityHigh });
  await loadSettings();
  $("settings").close();
  state.gallery.dirty = true;
  if (state.view === "gallery") loadGallery(true);
  showToast("设置已保存");
});

$("tidyBtn").addEventListener("click", async () => {
  const ok = window.confirm("把「实况」文件夹里的普通照片挪回各自的博主文件夹？视频不动，同名会跳过。");
  if (!ok) return;
  const btn = $("tidyBtn");
  btn.disabled = true;
  btn.textContent = "整理中…";
  try {
    const r = await api("/api/tidy", {
      root: $("setRoot").value.trim() || currentRoot(),
      live_folder: $("setLive").value.trim() || "实况",
    });
    $("settings").close();
    if (!r.ok) showToast(r.message || "整理失败");
    else {
      showToast(`整理了 ${r.moved} 张照片${r.skipped ? `，${r.skipped} 个同名已跳过` : ""}`);
      state.gallery.dirty = true;
      if (state.view === "gallery") loadGallery(true);
    }
  } catch (e) {
    showToast("整理失败：" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "整理";
  }
});

/* ---------------- 打开文件夹 ---------------- */

function folderOf(p) {
  const i = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
  return i > 0 ? p.slice(0, i) : p;
}

async function openFolder(path) {
  try {
    const r = await api("/api/openfolder", { path: path || "" });
    if (!r.ok) showHint(r.message || "打不开这个文件夹", true);
  } catch (e) {
    showHint("打不开文件夹：" + e.message, true);
  }
}

$("openDest").addEventListener("click", () => {
  openFolder(state.settings?.config?.root);
});

async function saveRoot(path) {
  const p = (path || "").trim();
  if (!p) return;
  try {
    const r = await api("/api/settings", { root: p });
    if (!r.ok) return;
    state.settings = state.settings || {};
    state.settings.config = r.config;
    $("destPath").value = r.config.root;
    $("setRoot").value = r.config.root;
    state.gallery.dirty = true;
    if (state.view === "gallery") loadGallery(true);
    showToast("保存路径已改为 " + r.config.root);
  } catch (e) {
    showToast("改保存路径失败：" + e.message);
  }
}

$("destPath").addEventListener("change", () => saveRoot($("destPath").value));

$("browseDest").addEventListener("click", async () => {
  const btn = $("browseDest");
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const r = await api("/api/pickfolder", { initial: $("destPath").value.trim() });
    if (r.ok) await saveRoot(r.path);
    else if (r.message && !/没有选择/.test(r.message)) showHint(r.message, true);
  } catch (e) {
    showHint("打开选择框失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "浏览";
  }
});

$("openFolder").addEventListener("click", () => {
  openFolder(state.lastFolder || state.settings?.config?.root);
});

/* ---------------- 画廊：博主墙 + 全部作品流 ---------------- */

// 名单由后端在解析主页时自动记录；画廊首页进入时会把博主、统计、作品一次拉齐
async function loadCreators() {
  state.gallery.dirty = true;
  if (state.view === "gallery") await loadGallery(false);
}

async function loadGallery(force) {
  const g = state.gallery;
  const token = (g.token || 0) + 1;
  g.token = token;
  g.loading = true;
  if (!g.loaded) {
    $("feedMeta").textContent = "正在读取本地作品…";
    $("creatorMeta").textContent = "正在读取…";
    $("creatorGrid").innerHTML = '<div class="empty">正在扫描保存目录…</div>';
  }
  try {
    const qs = new URLSearchParams({ offset: "0", limit: String(g.limit), sort: g.sort || "newest" });
    if (g.type !== "all") qs.set("type", g.type);
    if (g.folder) qs.set("folder", g.folder);
    if (g.q) qs.set("q", g.q);
    if (force) qs.set("refresh", "1");
    const r = await api("/api/gallery?" + qs.toString());
    if (token !== g.token) return;
    if (!r.ok) throw new Error(r.message || "读取失败");
    g.creators = r.creators || [];
    g.stats = r.stats || null;
    g.counts = r.counts || { types: {} };
    g.posts = r.posts || [];
    g.offset = g.posts.length;
    g.hasMore = !!r.has_more;
    g.loaded = true;
    g.dirty = false;
    renderGallery();
    renderCreatorOptions();
    renderCreatorWall();
  } catch (e) {
    if (token === g.token) {
      $("feedMeta").textContent = "读取失败：" + e.message;
      if (!g.loaded) {
        $("creatorMeta").textContent = "读取失败";
        $("creatorGrid").innerHTML = "";
      }
    }
  } finally {
    if (token === g.token) g.loading = false;
  }
}

async function loadMoreGallery() {
  const g = state.gallery;
  if (!g.hasMore) return;
  const token = g.token;
  g.loading = true;
  try {
    const qs = new URLSearchParams({ offset: String(g.offset), limit: String(g.limit), sort: g.sort || "newest" });
    if (g.type !== "all") qs.set("type", g.type);
    if (g.folder) qs.set("folder", g.folder);
    if (g.q) qs.set("q", g.q);
    const r = await api("/api/gallery?" + qs.toString());
    if (token !== g.token) return;
    if (!r.ok) throw new Error(r.message || "读取失败");
    g.posts = g.posts.concat(r.posts || []);
    g.offset = g.posts.length;
    g.hasMore = !!r.has_more;
    renderFeedGrid();
  } catch (e) {
    if (token === g.token) $("feedMeta").textContent = "读取失败：" + e.message;
  } finally {
    if (token === g.token) g.loading = false;
  }
}

function renderGallery() {
  const g = state.gallery;
  const s = g.stats || {};
  $("statCreators").textContent = s.creators ?? 0;
  $("statPosts").textContent = s.posts ?? 0;
  $("statFiles").textContent = s.files ?? 0;
  $("statBytes").textContent = fmtBytes(s.bytes || 0) || "—";

  const types = (g.counts && g.counts.types) || {};
  const totalTypes = (types["视频"] || 0) + (types["图集"] || 0) + (types["实况"] || 0);
  document.querySelectorAll("#galFilters .tab").forEach((b) => {
    const t = b.dataset.ft;
    const n = t === "all" ? totalTypes : (types[t] || 0);
    b.classList.toggle("is-on", t === g.type);
    b.textContent = `${t === "all" ? "全部" : t} ${n}`;
  });

  const total = (g.counts && g.counts.filtered) ?? g.posts.length;
  const bits = [`${total} 条作品`];
  if (g.folder) {
    const c = g.creators.find((x) => x.folder === g.folder);
    if (c) bits.push(c.remark || c.nickname || g.folder);
  }
  if (g.q) bits.push(`搜「${g.q}」`);
  $("feedMeta").textContent = g.posts.length ? bits.join(" · ") : "还没有作品";
  renderFeedGrid();
}

function renderFeedGrid() {
  const g = state.gallery;
  const grid = $("galGrid");
  grid.innerHTML = "";
  if (!g.posts.length) {
    grid.append(mk("div", "empty", g.creators.length
      ? "这个筛选下还没有作品。"
      : "画廊还是空的。去「下载」页贴一条博主主页链接，下载完就会出现在这里。"));
    $("galMore").hidden = true;
    return;
  }
  for (const p of g.posts) grid.append(postTile(p, { creator: true }));
  $("galMore").hidden = !g.hasMore;
  if (g.hasMore) $("galMore").textContent = `加载更多（已显示 ${g.posts.length} 条）`;
}

function renderCreatorOptions() {
  const g = state.gallery;
  const sel = $("galCreator");
  if (!sel) return;
  const cur = g.folder;
  sel.innerHTML = "";
  sel.append(new Option("全部博主", ""));
  for (const c of g.creators) {
    if (!c.folder) continue;
    const baseName = c.remark ? `${c.remark} (${c.nickname || c.folder})` : (c.nickname || c.folder);
    const label = `${baseName}${c.posts ? ` (${c.posts})` : ""}`;
    sel.append(new Option(label, c.folder));
  }
  sel.value = cur;
}

function avatarBox(src, name, cls) {
  const box = mk("span", cls);
  const letter = (name || "?").trim().slice(0, 1) || "?";
  if (!src) {
    box.classList.add("is-letter");
    box.textContent = letter;
    return box;
  }
  const img = document.createElement("img");
  img.alt = "";
  img.referrerPolicy = "no-referrer";
  img.src = /^https?:/i.test(src) ? src : thumbURL(src, 120);
  img.addEventListener("load", () => box.classList.add("is-ready"));
  img.addEventListener("error", () => {
    img.remove();
    box.classList.add("is-letter");
    box.textContent = letter;
  });
  box.append(img);
  return box;
}

function renderCreatorWall() {
  const grid = $("creatorGrid");
  if (!grid) return;
  const list = state.gallery.creators || [];
  $("creatorMeta").textContent = list.length ? `共 ${list.length} 位 · 最近在前` : "";
  $("creatorEmpty").hidden = !!list.length;
  grid.innerHTML = "";
  for (const c of list) grid.append(creatorCard(c));
}

function creatorCard(c) {
  const card = mk("article", "wall-card");
  card.tabIndex = 0;
  card.dataset.sec = c.sec_uid || "";
  card.title = c.url || c.sec_uid || c.folder || "";

  const cover = mk("div", "wall-cover");
  if (c.cover) fillThumb(cover, c.cover, { width: 480 });
  else cover.classList.add("is-empty");

  const shade = mk("div", "wall-shade");
  const body = mk("div", "wall-body");
  body.append(avatarBox(c.cover, c.nickname, "wall-avatar"));

  const info = mk("div", "wall-info");
  const displayName = c.remark || c.nickname || c.folder || "未命名博主";
  info.append(mk("div", "wall-name", displayName));
  const bits = [];
  const works = c.posts || c.last_count || 0;
  if (works) bits.push(`${works} 条作品`);
  if (c.files) bits.push(`${c.files} 个文件`);
  if (c.bytes) bits.push(fmtBytes(c.bytes));
  if (!c.local) bits.push("本地还没有");
  info.append(mk("div", "wall-meta", bits.join(" · ") || "等待下载"));
  const when = c.last_active || c.last_download_at || c.last_parsed_at || "";
  const subText = c.remark && c.nickname && c.remark !== c.nickname
    ? `原名 ${c.nickname}${when ? " · " + when.slice(0, 10) : ""}`
    : (when ? `最近 ${when.slice(0, 16)}` : (c.local ? "" : "只记了主页，还没解析过"));
  info.append(mk("div", "wall-sub", subText));
  body.append(info);

  card.append(cover, shade, body);

  const edit = mk("button", "wall-edit", "✎");
  edit.type = "button";
  edit.title = "设置博主备注";
  edit.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await promptCreatorRemark(c);
  });
  card.append(edit);

  if (c.sec_uid) {
    const del = mk("button", "wall-del", "×");
    del.type = "button";
    del.title = "移出名单（不会删掉已下载的文件）";
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!window.confirm(`把「${c.nickname || "这位博主"}」移出名单？已经下载的文件不动。`)) return;
      await api("/api/creators/remove", { sec_uid: c.sec_uid });
      loadGallery(true);
    });
    card.append(del);
  }

  card.addEventListener("click", () => openCreator(c));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openCreator(c);
    }
  });
  return card;
}

/* ---------------- 本地作品库（点博主观摩已下载的作品） ---------------- */

function fmtBytes(n) {
  if (!n) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

function fillLibAvatar(src, name) {
  const box = $("libAvatar");
  box.className = "lib-avatar";
  box.innerHTML = "";
  const letter = (name || "?").trim().slice(0, 1) || "?";
  if (!src) {
    box.classList.add("is-letter");
    box.textContent = letter;
    return;
  }
  const img = document.createElement("img");
  img.alt = "";
  img.referrerPolicy = "no-referrer";
  img.src = /^https?:/i.test(src) ? src : thumbURL(src, 160);
  img.addEventListener("load", () => box.classList.add("is-ready"));
  img.addEventListener("error", () => {
    img.remove();
    box.classList.add("is-letter");
    box.textContent = letter;
  });
  box.append(img);
}

async function promptCreatorRemark(c) {
  const cur = (c.remark || "").trim();
  const next = window.prompt(`给「${c.nickname || c.folder || "这位博主"}」设置备注（留空清除）：`, cur);
  if (next === null) return false;
  const remark = next.trim();
  try {
    const r = await api("/api/creators/remark", {
      sec_uid: c.sec_uid || "",
      folder: c.folder || "",
      nickname: c.nickname || "",
      uid: c.uid || "",
      remark,
    });
    if (!r.ok) {
      showToast(r.message || "保存备注失败");
      return false;
    }
    c.remark = r.remark || "";
    if (state.lib && ((c.sec_uid && state.lib.sec_uid === c.sec_uid) || (c.folder && state.lib.folder === c.folder))) {
      state.lib.remark = c.remark;
    }
    showToast(c.remark ? `已备注为「${c.remark}」` : "已清除备注");
    state.gallery.dirty = true;
    if (state.view === "gallery") loadGallery(true);
    if (state.view === "creator") renderLibrary();
    return true;
  } catch (e) {
    showToast("保存备注失败：" + e.message);
    return false;
  }
}

/* ---------------- 博主详情页 ---------------- */

function openCreator(c) {
  if (state.parseJob) {
    showToast("正在解析，等它跑完再看作品");
    return;
  }
  const lib = state.lib;
  lib.key = c.sec_uid || c.folder || c.nickname || "";
  lib.folder = c.folder || "";
  lib.path = c.path || "";
  lib.nickname = c.nickname || "";
  lib.remark = c.remark || "";
  lib.uid = c.uid || "";
  lib.sec_uid = c.sec_uid || "";
  lib.url = c.url || "";
  lib.tab = c.last_tab === "favorite" ? "favorite" : "post";
  lib.posts = [];
  lib.filter = "all";
  lib.q = "";
  lib.sort = "newest";
  lib.shown = 60;

  $("libSearch").value = "";
  if ($("libSort")) $("libSort").value = "newest";
  fillLibAvatar(c.cover || "", lib.remark || lib.nickname);
  $("libTitle").textContent = lib.remark || lib.nickname || "未命名博主";
  $("libUid").textContent = lib.remark && lib.nickname && lib.remark !== lib.nickname
    ? `原名 ${lib.nickname}${c.uid ? ` · UID ${c.uid}` : ""}`
    : (c.uid ? `UID ${c.uid}` : "CREATOR");
  $("libMeta").textContent = "正在读取本地作品…";
  $("creatorStats").innerHTML = "";
  $("libBrowseHome").hidden = !lib.sec_uid;
  $("libNewest").hidden = !lib.sec_uid;
  $("libFolder").disabled = !lib.path;
  $("postGrid").innerHTML = "";
  $("libMore").hidden = true;
  $("library").hidden = false;

  setView("creator");
  window.scrollTo({ top: 0, behavior: "smooth" });
  refreshLibrary();
}

async function refreshLibrary(force) {
  const lib = state.lib;
  if (!lib.key) return;
  const q = new URLSearchParams();
  if (lib.folder) q.set("folder", lib.folder);
  if (lib.sec_uid) q.set("sec_uid", lib.sec_uid);
  if (force) q.set("refresh", "1");
  try {
    const r = await api("/api/library?" + q.toString());
    if (!r.ok) {
      $("libMeta").textContent = r.message || "读取失败";
      return;
    }
    lib.posts = r.posts || [];
    lib.files = r.files || 0;
    lib.bytes = r.bytes || 0;
    lib.exists = !!r.exists;
    lib.folder = r.folder || lib.folder;
    lib.path = r.path || lib.path;
    if (r.nickname) lib.nickname = r.nickname;
    lib.remark = r.remark || "";
    if (r.sec_uid) lib.sec_uid = r.sec_uid;
    if (r.url) lib.url = r.url;
    $("libFolder").disabled = !lib.path;
    renderLibrary();
  } catch (e) {
    $("libMeta").textContent = "读取失败：" + e.message;
  }
}

function renderLibrary() {
  const lib = state.lib;
  const posts = lib.posts;
  $("libTitle").textContent = lib.remark || lib.nickname || "未命名博主";
  $("libUid").textContent = lib.remark && lib.nickname && lib.remark !== lib.nickname
    ? `原名 ${lib.nickname}${lib.uid ? ` · UID ${lib.uid}` : ""}`
    : (lib.uid ? `UID ${lib.uid}` : "CREATOR");
  $("libMeta").textContent = lib.exists
    ? `${posts.length} 条作品 · ${lib.files} 个文件${lib.bytes ? " · " + fmtBytes(lib.bytes) : ""}`
    : "本地还没有她的作品，可以点「下载最新作品」拉一次";

  const latest = posts.reduce((a, p) => ((p.time || "") > (a || "") ? p.time : a), "");
  const stats = $("creatorStats");
  stats.innerHTML = "";
  const rows = [
    ["作品", String(posts.length)],
    ["文件", String(lib.files || 0)],
    ["占用", fmtBytes(lib.bytes) || "—"],
    ["最近", latest ? latest.slice(0, 10) : "—"],
  ];
  for (const [k, v] of rows) {
    const box = mk("div", "lib-stat");
    box.append(mk("span", "lib-stat-num", v));
    box.append(mk("span", "lib-stat-label", k));
    stats.append(box);
  }

  const counts = { all: posts.length, 视频: 0, 图集: 0, 实况: 0 };
  for (const p of posts) counts[p.type] = (counts[p.type] || 0) + 1;
  document.querySelectorAll("#libFilters .tab").forEach((b) => {
    const t = b.dataset.ft;
    const n = t === "all" ? counts.all : (counts[t] || 0);
    b.classList.toggle("is-on", t === lib.filter);
    b.textContent = `${t === "all" ? "全部" : t} ${n}`;
    b.disabled = !n && t !== "all";
  });

  const needle = (lib.q || "").trim().toLowerCase();
  const list = posts.filter((p) => {
    if (lib.filter !== "all" && p.type !== lib.filter) return false;
    if (needle && !String(p.title || "").toLowerCase().includes(needle)) return false;
    return true;
  });
  if (lib.sort === "oldest") {
    list.sort((a, b) => (a.time || "").localeCompare(b.time || "") || (a.key || "").localeCompare(b.key || ""));
  } else if (lib.sort === "largest") {
    list.sort((a, b) => (b.bytes || 0) - (a.bytes || 0) || (b.time || "").localeCompare(a.time || ""));
  } else if (lib.sort === "most") {
    list.sort((a, b) => (b.count || 0) - (a.count || 0) || (b.time || "").localeCompare(a.time || ""));
  }

  const grid = $("postGrid");
  grid.innerHTML = "";
  if (!list.length) {
    grid.append(mk("div", "empty", lib.exists
      ? (needle ? "没有搜到符合条件的作品。" : "这个分类下还没有作品。")
      : "本地还没有她的作品。"));
    $("libMore").hidden = true;
    return;
  }
  for (const p of list.slice(0, lib.shown)) grid.append(postTile(p));
  $("libMore").hidden = list.length <= lib.shown;
  $("libMore").textContent = `加载更多（还有 ${list.length - lib.shown} 条）`;
}

function guessDragMime(filename, kind) {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
  if (ext === "png") return "image/png";
  if (ext === "webp") return "image/webp";
  if (ext === "gif") return "image/gif";
  if (ext === "bmp") return "image/bmp";
  if (ext === "avif") return "image/avif";
  if (ext === "mp4") return "video/mp4";
  if (ext === "mov") return "video/quicktime";
  if (ext === "webm") return "video/webm";
  return kind === "video" ? "video/mp4" : "image/jpeg";
}

function pickPostDragFile(p) {
  const files = (p && p.files) || [];
  const normalImg = files.find((f) => f && f.kind !== "video" && !/\.(heic|heif)$/i.test(f.name || f.path || ""));
  if (normalImg) return normalImg;
  const anyImg = files.find((f) => f && f.kind !== "video");
  if (anyImg) return anyImg;
  const frames = (p && p.frames) || [];
  if (frames.length) return frames[0];
  if (files.length) return files[0];
  if (p && p.cover && !/^https?:/i.test(p.cover)) {
    return { path: p.cover, name: "", kind: "image" };
  }
  return null;
}

function attachAssetDrag(el, file, opts) {
  const o = opts || {};
  const rawPath = file && file.path;
  if (!rawPath || /^https?:/i.test(rawPath)) return;
  const rawName = (file.name || rawPath.split(/[\\/]/).pop() || "image.jpg").replace(/[:\\/\r\n]/g, "_");
  const isHeic = /\.(heic|heif)$/i.test(rawName);
  const relUrl = "/api/asset?path=" + encodeURIComponent(rawPath) + (isHeic ? "&preview=1" : "");
  const absUrl = new URL(relUrl, window.location.href).href;
  const filename = isHeic ? rawName.replace(/\.(heic|heif)$/i, ".jpg") : rawName;
  const mime = guessDragMime(filename, file.kind);

  el.draggable = true;
  el.addEventListener("dragstart", (ev) => {
    if (o.ignoreSelector && ev.target && ev.target.closest(o.ignoreSelector)) {
      ev.preventDefault();
      return;
    }
    const dt = ev.dataTransfer;
    if (!dt) return;
    dt.effectAllowed = "copy";
    dt.setData("text/uri-list", absUrl);
    dt.setData("text/plain", absUrl);
    dt.setData("text/html", `<img src="${escapeHtml(absUrl)}" alt="${escapeHtml(filename)}">`);
    dt.setData("DownloadURL", `${mime}:${filename}:${absUrl}`);
    if (o.dragImageEl) {
      const img = o.dragImageEl();
      if (img && img.complete && img.naturalWidth > 0) {
        try {
          dt.setDragImage(img, Math.min(48, img.width / 2), Math.min(48, img.height / 2));
        } catch (e) { /* 忽略 */ }
      }
    }
    el.classList.add("is-dragging");
  });
  el.addEventListener("dragend", () => {
    el.classList.remove("is-dragging");
  });
}

function postTile(p, opts) {
  const o = opts || {};
  const tile = mk("div", "post");
  tile.tabIndex = 0;
  tile.title = p.title;

  const cover = mk("div", "post-cover");
  fillThumb(cover, p.cover || "", { width: 320, noImgDrag: true });
  if (p.count > 1) cover.append(mk("span", "post-count", `${p.count} 个`));
  if (p.frames && p.frames.length) cover.append(mk("span", "post-frames", `截图 ${p.frames.length}`));
  const badge = mk("span", "post-badge", p.type);
  badge.dataset.type = p.type;
  cover.append(badge);
  if (o.creator && p.creator) {
    cover.append(mk("span", "post-owner", p.creator.remark || p.creator.nickname || p.creator.folder || ""));
  }
  if (p.files && p.files.length) {
    const del = mk("button", "post-del", "×");
    del.type = "button";
    del.title = "移入回收站";
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const n = p.files.length;
      const label = p.title ? `「${p.title}」` : "这条作品";
      if (!window.confirm(`把${label}（${n} 个文件）移到回收站？`)) return;
      del.disabled = true;
      try {
        const r = await api("/api/post/delete", { paths: p.files.map((f) => f.path) });
        if (!r.ok) {
          showToast(r.message || "删除失败");
          return;
        }
        showToast(`已将 ${r.deleted || n} 个文件移到回收站`);
        state.gallery.dirty = true;
        if (state.view === "gallery") loadGallery(true);
        if (state.view === "creator" && state.lib.key) refreshLibrary(true);
      } catch (e) {
        showToast("删除失败：" + e.message);
      } finally {
        del.disabled = false;
      }
    });
    cover.append(del);
  }

  const body = mk("div", "post-body");
  body.append(mk("div", "post-title", p.title));
  body.append(mk("div", "post-date", (p.time || p.date || "").slice(0, 16)));

  tile.append(cover, body);
  attachAssetDrag(tile, pickPostDragFile(p), {
    ignoreSelector: "button",
    dragImageEl: () => cover.querySelector("img"),
  });
  tile.addEventListener("click", () => openViewer(p));
  tile.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openViewer(p);
    }
  });
  return tile;
}

/* ---------------- 大图 / 视频预览 ---------------- */

function openViewer(post) {
  state.viewer.post = post;
  state.viewer.files = [...(post.files || []), ...(post.frames || [])];
  state.viewer.index = 0;
  state.viewer.title = post.title;
  state.viewer.creator = post.creator
    ? (post.creator.nickname || post.creator.folder || "")
    : "";
  $("viewerTitle").textContent = post.title || "";
  $("viewerMeta").textContent = [
    state.viewer.creator,
    post.type,
    (post.time || "").slice(0, 16),
  ].filter(Boolean).join(" · ");
  renderViewerFrames();
  showViewerFile();
  $("viewer").showModal();
}

function renderViewerFrames() {
  const box = $("viewerFrames");
  if (!box) return;
  const v = state.viewer;
  const frames = [];
  v.files.forEach((f, idx) => {
    if (f && f.is_frame) frames.push({ file: f, idx });
  });
  box.innerHTML = "";
  if (!frames.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.append(mk("span", "viewer-frames-label", `作品截图 (${frames.length})`));
  for (const { file, idx } of frames) {
    const btn = mk("button", "viewer-frame-thumb");
    btn.type = "button";
    btn.title = `${file.name || "截图"}（点击查看，可直接拖出）`;
    btn.dataset.idx = String(idx);
    if (idx === v.index) btn.classList.add("is-active");
    const img = document.createElement("img");
    img.src = thumbURL(file.path, 160);
    img.alt = file.name || "";
    img.draggable = false;
    btn.append(img);
    attachAssetDrag(btn, file, { dragImageEl: () => img });
    btn.addEventListener("click", () => {
      v.index = idx;
      showViewerFile();
    });
    box.append(btn);
  }
}

function syncViewerFramesActive() {
  const box = $("viewerFrames");
  if (!box || box.hidden) return;
  const cur = String(state.viewer.index);
  box.querySelectorAll(".viewer-frame-thumb").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.idx === cur);
  });
}

function updateViewerDeleteButtons() {
  const v = state.viewer;
  const f = v.files[v.index];
  const delOne = $("viewerDeleteOne");
  const delAll = $("viewerDelete");
  if (!delOne || !delAll) return;
  const multi = v.files.length > 1 && !!f;
  delOne.hidden = !multi;
  if (multi) {
    delOne.textContent = f.is_frame ? "删这张截图" : (f.kind === "video" ? "删当前视频" : "删这一张");
    delAll.textContent = `删整条 (${v.files.length})`;
    delAll.title = `把这条作品的全部 ${v.files.length} 个文件移到回收站`;
  } else {
    delAll.textContent = "移到回收站";
    delAll.title = "把这条作品的文件移到回收站";
  }
}

function appendViewerFrame(r) {
  const v = state.viewer;
  if (!r || !r.path) return;
  const item = { name: r.name || "", path: r.path, kind: "image", is_frame: true };
  if (v.post) {
    if (!Array.isArray(v.post.frames)) v.post.frames = [];
    v.post.frames.push(item);
  }
  v.files.push(item);
  $("viewerCount").textContent = v.files.length ? `${v.index + 1} / ${v.files.length}` : "";
  $("viewerPrev").hidden = v.files.length < 2;
  $("viewerNext").hidden = v.files.length < 2;
  renderViewerFrames();
  updateViewerDeleteButtons();
}

function clearViewerStage() {
  const v = state.viewer;
  if (v.video) {
    try {
      v.video.pause();
      v.video.removeAttribute("src");
      v.video.load();
    } catch (e) { /* 忽略 */ }
    v.video = null;
  }
  $("viewerStage").innerHTML = "";
}

function showViewerFile() {
  const v = state.viewer;
  clearViewerStage();
  const stage = $("viewerStage");
  vfReset();
  $("viewerExtract").hidden = true;
  $("viewerExtract").disabled = false;
  $("viewerExtract").textContent = "抽这一帧";
  const f = v.files[v.index];
  $("viewerCount").textContent = v.files.length ? `${v.index + 1} / ${v.files.length}` : "";
  $("viewerPrev").hidden = v.files.length < 2;
  $("viewerNext").hidden = v.files.length < 2;
  syncViewerFramesActive();
  updateViewerDeleteButtons();
  if (!f) return;

  const src = "/api/asset?path=" + encodeURIComponent(f.path);
  if (f.kind === "video") {
    const vid = document.createElement("video");
    vid.src = src;
    vid.controls = true;
    vid.autoplay = true;
    vid.playsInline = true;
    stage.append(vid);
    v.video = vid;
    $("viewerExtract").hidden = false;
    vid.addEventListener("play", vfSyncPlayIcon);
    vid.addEventListener("pause", vfSyncPlayIcon);
    vid.addEventListener("ended", vfSyncPlayIcon);
    vid.addEventListener("loadedmetadata", () => {
      if (state.viewer.video === vid) vfInit(vid, f);
    });
    vid.addEventListener("error", () => {
      if (state.viewer.video === vid) vfReset();
    });
  } else if (/\.(heic|heif)$/i.test(f.name)) {
    const img = document.createElement("img");
    img.src = src + "&preview=1";
    img.alt = "";
    attachAssetDrag(img, f);
    img.addEventListener("error", () => {
      img.replaceWith(mk("div", "empty", "HEIC 封面预览失败；转成实况照片后去 LivePhotos 里看。"));
    });
    stage.append(img);
  } else {
    const img = document.createElement("img");
    img.src = src;
    img.alt = "";
    attachAssetDrag(img, f);
    stage.append(img);
  }
}

$("viewerClose").addEventListener("click", () => {
  $("viewer").close();
});
$("viewer").addEventListener("close", () => {
  clearViewerStage();
  vfReset();
});
$("viewerPrev").addEventListener("click", () => {
  const v = state.viewer;
  if (!v.files.length) return;
  v.index = (v.index - 1 + v.files.length) % v.files.length;
  showViewerFile();
});
$("viewerNext").addEventListener("click", () => {
  const v = state.viewer;
  if (!v.files.length) return;
  v.index = (v.index + 1) % v.files.length;
  showViewerFile();
});
$("viewerOpen").addEventListener("click", () => {
  const f = state.viewer.files[state.viewer.index];
  if (f) openFolder(folderOf(f.path));
});

$("viewerDeleteOne").addEventListener("click", async () => {
  const v = state.viewer;
  const f = v.files[v.index];
  if (!f) return;
  const label = f.name ? `「${f.name}」` : "当前文件";
  if (!window.confirm(`把${label}移到回收站？`)) return;
  const btn = $("viewerDeleteOne");
  btn.disabled = true;
  try {
    const r = await api("/api/post/delete", { paths: [f.path] });
    if (!r.ok) {
      showToast(r.message || "删除失败");
      return;
    }
    v.files.splice(v.index, 1);
    if (v.post) {
      if (f.is_frame && Array.isArray(v.post.frames)) {
        v.post.frames = v.post.frames.filter((x) => x.path !== f.path);
      } else if (Array.isArray(v.post.files)) {
        v.post.files = v.post.files.filter((x) => x.path !== f.path);
        v.post.count = v.post.files.length;
        if (v.post.cover === f.path) {
          const nextImg = v.post.files.find((x) => x.kind !== "video") || v.post.files[0];
          v.post.cover = nextImg ? nextImg.path : "";
        }
      }
    }
    showToast(`已将${label}移到回收站`);
    state.gallery.dirty = true;
    if (state.view === "gallery") loadGallery(true);
    if (state.view === "creator" && state.lib.key) refreshLibrary(true);
    if (!v.files.length) {
      $("viewer").close();
      return;
    }
    if (v.index >= v.files.length) v.index = v.files.length - 1;
    renderViewerFrames();
    showViewerFile();
  } catch (e) {
    showToast("删除失败：" + e.message);
  } finally {
    btn.disabled = false;
  }
});

$("viewerDelete").addEventListener("click", async () => {
  const v = state.viewer;
  if (!v.files.length) return;
  const n = v.files.length;
  const label = v.title ? `「${v.title}」` : "这条作品";
  if (!window.confirm(`把${label}（${n} 个文件）移到回收站？`)) return;
  const btn = $("viewerDelete");
  btn.disabled = true;
  try {
    const r = await api("/api/post/delete", { paths: v.files.map((f) => f.path) });
    if (!r.ok) {
      showToast(r.message || "删除失败");
      return;
    }
    $("viewer").close();
    showToast(`已将 ${r.deleted || n} 个文件移到回收站`);
    state.gallery.dirty = true;
    if (state.view === "gallery") loadGallery(true);
    if (state.view === "creator" && state.lib.key) refreshLibrary(true);
  } catch (e) {
    showToast("删除失败：" + e.message);
  } finally {
    btn.disabled = false;
  }
});

$("viewerExtract").addEventListener("click", async () => {
  const v = state.viewer;
  const f = v.files[v.index];
  if (!f || !v.video) return;
  const btn = $("viewerExtract");
  btn.disabled = true;
  btn.textContent = "抽帧中…";
  try {
    const r = await api("/api/frame", {
      path: f.path,
      seconds: v.video.currentTime || 0,
      root: currentRoot(),
    });
    if (!r.ok) throw new Error(r.message || "抽帧失败");
    state.gallery.dirty = true;
    appendViewerFrame(r);
    showToast(`已抽出一帧：${r.name}`);
    btn.textContent = "再抽一帧";
  } catch (e) {
    showToast("抽帧失败：" + e.message);
    btn.textContent = "抽这一帧";
  } finally {
    btn.disabled = false;
  }
});

// 查看器开着时的键盘：视频按逐帧操作（[ ] 或 PageUp/Down 切换文件），图片沿用左右翻页
window.addEventListener("keydown", (e) => {
  if (!$("viewer").open) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;

  if (e.key === "[" || e.key === "PageUp") {
    e.preventDefault();
    $("viewerPrev").click();
    return;
  }
  if (e.key === "]" || e.key === "PageDown") {
    e.preventDefault();
    $("viewerNext").click();
    return;
  }

  if (state.viewer.video) {
    const key = (e.key || "").toLowerCase();
    const code = e.code || "";
    const run = (fn) => {
      e.preventDefault();
      fn();
    };
    if (key === " " || code === "Space") return run(vfTogglePlay);
    if (key === "arrowleft") return run(() => vfStep(-1, e.shiftKey ? VFR.fps : 1));
    if (key === "arrowright") return run(() => vfStep(1, e.shiftKey ? VFR.fps : 1));
    if (key === "j" || code === "KeyJ") return run(() => vfStep(-1, VFR.fps));
    if (key === "l" || code === "KeyL") return run(() => vfStep(1, VFR.fps));
    if (key === "home") {
      return run(() => {
        state.viewer.video.pause();
        vfSeek(0);
      });
    }
    if (key === "end") {
      return run(() => {
        state.viewer.video.pause();
        vfSeek(VFR.dur - 0.05);
      });
    }
    if (key === "s" || code === "KeyS" || key === "enter") return run(vfSave);
    if (key === "c" || code === "KeyC") return run(vfCopy);
  }

  if (e.key === "ArrowLeft") {
    e.preventDefault();
    $("viewerPrev").click();
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    $("viewerNext").click();
  }
});

/* ---------------- 画廊与博主页的操作 ---------------- */

function reloadGallery() {
  $("feedMeta").textContent = "正在读取…";
  state.gallery.offset = 0;
  loadGallery(false);
}

$("creatorRefresh").addEventListener("click", () => loadGallery(true));
$("addCreator").addEventListener("click", () => {
  setView("download");
  $("link").focus();
});
if ($("goGallery")) $("goGallery").addEventListener("click", () => setView("gallery"));

$("galFilters").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (!b || b.disabled) return;
  state.gallery.type = b.dataset.ft;
  reloadGallery();
});

$("galMore").addEventListener("click", loadMoreGallery);

$("galCreator").addEventListener("change", (e) => {
  state.gallery.folder = e.target.value;
  reloadGallery();
});

$("galSort").addEventListener("change", (e) => {
  state.gallery.sort = e.target.value || "newest";
  reloadGallery();
});

let gallerySearchTimer = null;
$("galSearch").addEventListener("input", (e) => {
  const value = e.target.value.trim();
  clearTimeout(gallerySearchTimer);
  gallerySearchTimer = setTimeout(() => {
    state.gallery.q = value;
    reloadGallery();
  }, 260);
});

$("creatorBack").addEventListener("click", () => setView("gallery"));

$("libFilters").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (!b || b.disabled) return;
  state.lib.filter = b.dataset.ft;
  state.lib.shown = 60;
  renderLibrary();
});
$("libMore").addEventListener("click", () => {
  state.lib.shown += 60;
  renderLibrary();
});
$("libRefresh").addEventListener("click", () => refreshLibrary(true));
$("libFolder").addEventListener("click", () => {
  if (state.lib.path) openFolder(state.lib.path);
});

let libSearchTimer = null;
$("libSearch").addEventListener("input", (e) => {
  const value = e.target.value.trim();
  clearTimeout(libSearchTimer);
  libSearchTimer = setTimeout(() => {
    state.lib.q = value;
    state.lib.shown = 60;
    renderLibrary();
  }, 180);
});

$("libSort").addEventListener("change", (e) => {
  state.lib.sort = e.target.value || "newest";
  state.lib.shown = 60;
  renderLibrary();
});

$("libBrowseHome").addEventListener("click", () => {
  const lib = state.lib;
  if (!lib.sec_uid) {
    showToast("还没有记录她的主页链接：去「发现 → + 指定链接下载」贴一次主页链接，就会记住。");
    return;
  }
  setView("discover");
  if (lib.sec_uid.startsWith("xhs_")) {
    openDiscoverUser("xhs", lib.sec_uid.slice(4), lib.nickname, "", lib.url);
  } else {
    openDiscoverUser("douyin", lib.sec_uid, lib.nickname, "", lib.url);
  }
});

$("libRemark")?.addEventListener("click", () => {
  const lib = state.lib;
  if (!lib.sec_uid && !lib.folder) return;
  promptCreatorRemark(lib);
});

$("libNewest").addEventListener("click", () => {
  const lib = state.lib;
  if (!lib.sec_uid) {
    showToast("还没有记录她的主页链接：去「下载」页贴一次主页链接，就会记住。");
    return;
  }
  if (state.running) {
    showToast("正在下载，等下载完再解析新的");
    return;
  }
  if (state.parseJob) {
    showToast("上一个还在解析，等它跑完再换链接");
    return;
  }
  setView("download");
  setMode("user");
  state.tab = lib.tab;
  $("link").value = lib.url || lib.sec_uid;
  doParse();
});

/* ---------------- 实况照片 ---------------- */

const LIVE_TEXT = { pending: "待转换", converting: "转换中", done: "完成", error: "失败" };
let liveScanToken = 0;

function currentRoot() {
  return ($("destPath").value || state.settings?.config?.root || "").trim();
}

function mk(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

let toastTimer = null;
function showToast(msg) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
  clearTimeout(toastTimer);
  if (msg) toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

function showLiveHint(msg, isError) {
  const el = $("liveHint");
  el.textContent = msg || "";
  el.className = isError ? "hint is-error" : "hint";
}

// 工具页子功能注册表：后续新增工具只需在这里登记并补一段 DOM 面板
const TOOLS = {
  live: {
    panelId: "toolLive",
    onEnter: () => scanLive(),
  },
  frame: {
    panelId: "toolFrame",
    wide: true,
    onEnter: () => fxScan(false),
    onLeave: () => {
      const v = $("fxVideo");
      if (v && !v.paused) v.pause();
      FX.stripToken += 1; // 停掉还在跑的胶片条生成
    },
  },
};

function isFrameActive() {
  return state.view === "tools" && state.tool === "frame";
}

function syncToolsUI() {
  const active = TOOLS[state.tool] ? state.tool : "live";
  state.tool = active;
  document.querySelectorAll("#toolsTabs [data-tool]").forEach((btn) => {
    const on = btn.dataset.tool === active;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  for (const [key, cfg] of Object.entries(TOOLS)) {
    const panel = $(cfg.panelId);
    if (panel) panel.hidden = key !== active;
  }
  document.body.classList.toggle("frame-mode", state.view === "tools" && !!TOOLS[active].wide);
}

function setTool(tool) {
  const next = TOOLS[tool] ? tool : "live";
  const prev = state.tool;
  if (prev !== next && TOOLS[prev]?.onLeave) {
    TOOLS[prev].onLeave();
  }
  state.tool = next;
  syncToolsUI();
  if (state.view === "tools" && TOOLS[next]?.onEnter) {
    TOOLS[next].onEnter();
  }
}

function setView(view) {
  const openDownloadPanel = view === "download";
  if (openDownloadPanel) view = "discover";
  if (view === "frame") {
    view = "tools";
    state.tool = "frame";
  }
  const prevView = state.view;
  state.view = view;
  const navKey = view === "creator" ? "gallery" : view;
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("is-on", b.dataset.view === navKey);
  });
  $("viewGallery").hidden = view !== "gallery";
  $("viewCreator").hidden = view !== "creator";
  $("viewDiscover").hidden = view !== "discover";
  if (openDownloadPanel) $("viewDownload").hidden = false;
  $("viewTools").hidden = view !== "tools";

  if (view === "discover" && !state.discover.loaded && !state.discover.loading) {
    loadXhsHomefeed(true);
  }

  if (prevView === "tools" && view !== "tools" && TOOLS[state.tool]?.onLeave) {
    TOOLS[state.tool].onLeave();
  }
  syncToolsUI();

  if (view === "gallery") {
    if (!state.gallery.loaded || state.gallery.dirty) loadGallery(false);
  } else if (view === "tools") {
    TOOLS[state.tool]?.onEnter?.();
  }
}

$("toolsTabs")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tool]");
  if (!btn) return;
  setTool(btn.dataset.tool);
});

async function scanLive() {
  const token = ++liveScanToken;
  showLiveHint("正在扫描保存目录…", false);
  $("liveStart").disabled = true;
  try {
    const r = await api("/api/livephoto/scan", { root: currentRoot() });
    if (token !== liveScanToken) return;
    if (!r.ok) {
      showLiveHint(r.message || "扫描失败", true);
      return;
    }
    state.live.items = r.items || [];
    state.live.tools = r.tools || {};
    state.live.shown = 300;
    state.live.lastResp = r;
    renderLive(r);
  } catch (e) {
    if (token === liveScanToken) showLiveHint("扫描失败：" + e.message, true);
  }
}

function renderLive(r) {
  const pending = r.pending || 0;
  const total = r.total || 0;
  const tools = r.tools || {};

  $("liveTitle").textContent = !total ? "还没有实况视频" : pending ? `${pending} 个待转换` : "都转好了";
  $("liveMeta").textContent = `共 ${total} 个实况 · 已转换 ${r.done}`;

  const badge = $("navLiveCount");
  badge.hidden = !pending;
  badge.textContent = String(pending);

  const running = state.live.running;
  $("liveStart").disabled = running || !pending || !tools.ffmpeg || !tools.exiftool;
  $("liveStart").textContent = running ? "转换中" : pending ? "开始转换" : "都转好了";

  if (!tools.ffmpeg || !tools.exiftool) {
    showLiveHint("转换工具没找到：tools 目录里应该有 ffmpeg.exe 和 exiftool.exe", true);
  } else if (!tools.heic) {
    showLiveHint("HEIC 编码组件不可用，封面会退成 JPG（同样能和 MOV 配对导入）。", false);
  } else if (!pending) {
    showLiveHint(total ? "都转好了，成品在 LivePhotos 文件夹。" : "保存目录里没有待转换的实况视频。", false);
  } else {
    showLiveHint(`准备转换 ${pending} 个，单个大约 2–4 秒。`, false);
  }

  const list = $("liveList");
  list.innerHTML = "";
  const items = (r.items || [])
    .slice()
    .sort((a, b) => (a.done ? 1 : 0) - (b.done ? 1 : 0) || b.mtime - a.mtime);
  if (!items.length) {
    list.append(mk("div", "empty", "保存目录里没有找到文件名带「实况」的视频。"));
    $("liveMore").hidden = true;
    return;
  }

  const shown = items.slice(0, state.live.shown || 300);
  for (const it of shown) {
    const row = mk("div", "row live-row");
    if (it.done) row.classList.add("is-done");
    row.dataset.path = it.path;

    const full = it.rel || it.path;
    const pic = thumbEl(it.thumb || it.path, { width: 120, fallback: it.done ? it.path : "" });
    pic.dataset.src = it.thumb || it.path;
    const title = mk("span", "title", full.split(/[\\/]/).pop());
    title.title = it.path;
    const date = mk("span", "date", it.time || "");
    const st = mk("span", "state", it.done ? "已转换" : "待转换");
    st.dataset.state = it.done ? "done" : "pending";

    row.append(pic, title, date, st);
    list.append(row);
  }
  const remaining = items.length - shown.length;
  $("liveMore").hidden = remaining <= 0;
  if (remaining > 0) $("liveMore").textContent = `加载更多（还有 ${remaining} 个）`;
}

function setLiveRow(path, status, message, file) {
  const row = $("liveList").querySelector(`.row[data-path="${CSS.escape(path)}"]`);
  if (!row) return;
  const st = row.querySelector(".state");
  st.dataset.state = status;
  st.textContent = message || LIVE_TEXT[status] || "";
  row.classList.toggle("is-done", status === "done");

  const box = row.querySelector(".thumb");
  if (box && file && status === "done" && box.dataset.src !== file) {
    box.dataset.src = file;
    fillThumb(box, file, { width: 120, fallback: path });
  }
}

async function startLive() {
  if (state.live.running) return;
  const r = await api("/api/livephoto/start", { root: currentRoot() });
  if (!r.ok) {
    showLiveHint(r.message || "启动失败", true);
    return;
  }
  state.live.running = true;
  state.live.id = r.job;
  $("liveStart").disabled = true;
  $("liveStart").textContent = "转换中";
  $("liveRescan").disabled = true;
  $("liveStop").hidden = false;
  $("liveTrack").hidden = false;
  $("liveFill").style.width = "0%";
  showLiveHint("正在转换：抽帧、封装，再写入 iOS 认的配对信息。", false);
  livePollJob();
}

async function livePollJob() {
  clearTimeout(state.live.poll);
  if (!state.live.id) return;

  try {
    const r = await api("/api/job?id=" + encodeURIComponent(state.live.id));
    if (!r.ok) return;
    const job = r.job;
    const items = Object.values(job.items || {});

    for (const it of items) setLiveRow(it.id, it.status, it.message, it.file);
    $("liveFill").style.width = `${Math.round((job.finished / Math.max(job.total, 1)) * 100)}%`;

    if (job.running) {
      $("liveStart").textContent = `转换中 ${job.finished}/${job.total}`;
      state.live.poll = setTimeout(livePollJob, 700);
      return;
    }

    state.live.running = false;
    state.live.id = null;
    $("liveRescan").disabled = false;
    $("liveStop").hidden = true;
    const done = items.filter((x) => x.status === "done").length;
    const err = items.filter((x) => x.status === "error").length;
    showLiveHint(`完成 ${done} 个${err ? ` · 失败 ${err} 个` : ""}，成品在 LivePhotos 文件夹`, !!err);
    setTimeout(scanLive, 1400);
  } catch (e) {
    state.live.poll = setTimeout(livePollJob, 1200);
  }
}

$("liveRescan").addEventListener("click", scanLive);
$("liveMore").addEventListener("click", () => {
  state.live.shown += 300;
  if (state.live.lastResp) renderLive(state.live.lastResp);
});
$("liveStart").addEventListener("click", startLive);
$("liveStop").addEventListener("click", () => {
  if (state.live.id) api("/api/cancel", { id: state.live.id });
});
$("liveOpen").addEventListener("click", async () => {
  try {
    const r = await api("/api/livephoto/open", { root: currentRoot() });
    if (!r.ok) showLiveHint(r.message || "打不开成品文件夹", true);
  } catch (e) {
    showLiveHint("打不开成品文件夹：" + e.message, true);
  }
});

/* ---------------- 截帧页（逐帧播放器） ---------------- */

const RVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;

// 播放器自己的瞬时状态；列表和选中沿用 state.frame
const FX = {
  fps: 30,
  t: 0,
  dur: 0,
  ready: false,
  busy: false,
  loading: false,
  dragging: false,
  stripToken: 0,
  seekEpoch: 0,
  stepBusy: false,
  stepPending: 0,
  loop: false,
  lastOut: "",
  open: new Set(), // 展开着的博主分组
};

function fxVideo() {
  return $("fxVideo");
}

function fxClamp(v, a, b) {
  return v < a ? a : (v > b ? b : v);
}

function fmtFxTime(t) {
  if (!isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return String(m).padStart(2, "0") + ":" + s.toFixed(3).padStart(6, "0");
}

function fmtFxShort(t) {
  if (!isFinite(t)) return "0";
  return t < 60
    ? t.toFixed(1) + "s"
    : Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
}

function fxOnce(el, ev, timeout, errEv) {
  return new Promise((resolve, reject) => {
    let done = false;
    const ok = () => { if (done) return; done = true; cleanup(); resolve(); };
    const bad = () => { if (done) return; done = true; cleanup(); reject(new Error(ev)); };
    const to = setTimeout(bad, timeout || 10000);
    function cleanup() {
      clearTimeout(to);
      el.removeEventListener(ev, ok);
      if (errEv) el.removeEventListener(errEv, bad);
    }
    el.addEventListener(ev, ok);
    if (errEv) el.addEventListener(errEv, bad);
  });
}

/* ---------- 列表 ---------- */

function fxScan(force) {
  const f = state.frame;
  const token = ++f.scanToken;
  $("fxMeta").textContent = "正在扫描视频…";
  $("fxList").innerHTML = '<div class="empty">正在扫描…</div>';
  // 存一份在途的扫描，截图页跳转时能等它扫完再选视频
  f.pending = (async () => {
    try {
      const qs = new URLSearchParams({ kind: f.kind, limit: "0" });
      if (f.q) qs.set("q", f.q);
      if (force) qs.set("refresh", "1");
      const r = await api("/api/frame/scan?" + qs.toString());
      if (token !== f.scanToken) return;
      if (!r.ok) throw new Error(r.message || "扫描失败");
      f.items = r.items || [];
      f.loaded = true;
      fxRenderList(r);
    } catch (e) {
      if (token !== f.scanToken) return;
      $("fxMeta").textContent = "扫描失败";
      $("fxList").innerHTML = "";
      showToast("扫描失败：" + e.message);
    }
  })();
  return f.pending;
}

function fxGroupItems(items) {
  // 按博主分组，保持服务器给的顺序（新→旧）
  const groups = [];
  const map = new Map();
  for (const it of items) {
    const key = it.creator || "";
    let g = map.get(key);
    if (!g) {
      g = { name: key, items: [] };
      map.set(key, g);
      groups.push(g);
    }
    g.items.push(it);
  }
  return groups;
}

function fxRenderList(r) {
  const f = state.frame;
  const total = r.total ?? f.items.length;
  const list = $("fxList");
  list.innerHTML = "";

  if (!f.items.length) {
    $("fxMeta").textContent = "没有找到视频";
    list.append(mk("div", "empty", "保存目录里没有视频，或者筛选条件太窄。"));
    return;
  }

  const groups = fxGroupItems(f.items);
  $("fxMeta").textContent =
    `共 ${total} 条视频 · ${groups.length} 位博主` +
    (total > f.items.length ? ` · 先显示 ${f.items.length} 条` : "");

  // 第一次进来先展开第一位博主，让人一眼看到怎么用
  if (!f.seeded) {
    f.seeded = true;
    FX.open.add(groups[0].name);
  }

  const searching = !!String(f.q || "").trim();
  let budget = 2500;
  let rendered = 0;

  for (const g of groups) {
    const pinned = f.selected && (f.selected.creator || "") === g.name;
    const open = searching || FX.open.has(g.name) || pinned;
    const wrap = mk("div", "fx-creator" + (open ? " is-open" : ""));
    wrap.dataset.creator = g.name;

    const head = mk("button", "fx-creator-head");
    head.type = "button";
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.title = (g.name ? `${g.name} · ${g.items.length} 条视频`
                         : `未归类 · ${g.items.length} 条视频`);
    const chev = mk("span", "fx-chev", open ? "▾" : "▸");
    head.append(chev, thumbEl(g.items[0].thumb || g.items[0].path, { width: 120 }));

    const idBox = mk("span", "fx-creator-id");
    idBox.append(mk("span", "fx-creator-name", g.name || "未归类"));
    const lives = g.items.filter((x) => x.live).length;
    const bits = [`${g.items.length} 条`];
    if (lives) bits.push(`实况 ${lives}`);
    idBox.append(mk("span", "fx-creator-meta", bits.join(" · ")));
    head.append(idBox);

    const body = mk("div", "fx-creator-body");
    const rows = g.items.slice(0, Math.min(150, Math.max(0, budget)));
    for (const it of rows) {
      const row = mk("button", "fx-item");
      row.type = "button";
      row.dataset.path = it.path;
      const pic = thumbEl(it.thumb || it.path, { width: 120 });
      const rowBody = mk("span", "fx-item-body");
      rowBody.append(mk("span", "fx-item-name", it.name));
      const rowBits = [];
      if (it.live) rowBits.push("实况");
      if (it.time) rowBits.push(it.time.slice(0, 10));
      if (it.size) rowBits.push(fmtBytes(it.size));
      rowBody.append(mk("span", "fx-item-meta", rowBits.filter(Boolean).join(" · ")));
      row.append(pic, rowBody);
      row.addEventListener("click", () => fxSelect(it));
      body.append(row);
    }
    rendered += rows.length;
    budget -= rows.length;
    if (rows.length < g.items.length) {
      body.append(mk("div", "fx-more",
        `另外 ${g.items.length - rows.length} 条没显示，搜一下缩小范围。`));
    }

    head.addEventListener("click", () => {
      const nowOpen = !wrap.classList.contains("is-open");
      wrap.classList.toggle("is-open", nowOpen);
      head.setAttribute("aria-expanded", nowOpen ? "true" : "false");
      chev.textContent = nowOpen ? "▾" : "▸";
      if (nowOpen) FX.open.add(g.name);
      else FX.open.delete(g.name);
    });

    wrap.append(head, body);
    list.append(wrap);
  }

  if (rendered < f.items.length) {
    list.append(mk("div", "fx-more",
      `还有 ${f.items.length - rendered} 条没显示，用搜索缩小范围。`));
  }

  if (f.selected) fxMarkSelected(f.selected.path);
}

function fxExpandCreator(name) {
  FX.open.add(name);
  document.querySelectorAll("#fxList .fx-creator").forEach((w) => {
    if (w.dataset.creator !== name || w.classList.contains("is-open")) return;
    w.classList.add("is-open");
    const head = w.querySelector(".fx-creator-head");
    if (head) head.setAttribute("aria-expanded", "true");
    const chev = w.querySelector(".fx-chev");
    if (chev) chev.textContent = "▾";
  });
}

function fxMarkSelected(path) {
  document.querySelectorAll("#fxList .fx-item").forEach((b) => {
    b.classList.toggle("is-on", b.dataset.path === path);
  });
}

/* ---------- 载入 ---------- */

async function fxSelect(it) {
  const f = state.frame;
  const video = fxVideo();
  f.selected = it;
  fxMarkSelected(it.path);
  fxExpandCreator(it.creator || "");
  fxHideError();
  $("fxEmpty").hidden = true;
  $("fxSaved").hidden = true;
  $("fxName").hidden = false;
  $("fxName").textContent = it.name;
  $("fxName").title = it.path;

  FX.ready = false;
  FX.loading = true;
  FX.busy = true;
  FX.stripToken++;
  fxResetHud();
  $("fxStrip").innerHTML = "";
  fxSetFrameButtons(false);

  video.pause();
  video.removeAttribute("src");
  video.load();
  video.src = "/api/asset?path=" + encodeURIComponent(it.path);
  video.load();

  try {
    await fxOnce(video, "loadedmetadata", 12000, "error");
  } catch (e) {
    if (state.frame.selected !== it) return;
    FX.loading = false;
    FX.busy = false;
    fxShowDecodeError(it);
    return;
  }
  if (state.frame.selected !== it) return;

  FX.loading = false;
  FX.dur = isFinite(video.duration)
    ? video.duration
    : (video.seekable.length ? video.seekable.end(0) : 0);
  FX.ready = true;
  FX.busy = false;
  $("fxSeek").max = Math.max(0.001, FX.dur - 0.0005);
  fxDrawTicks();
  fxStartLoop();
  FX.t = 0;
  await fxSeekTo(0);
  fxUpdateHud();
  fxSetFrameButtons(true);

  // 先测帧率（要短暂播放），再生成胶片条（要连续 seek）；同时做会互相打断
  const myToken = FX.stripToken;
  await fxProbeFps(myToken);
  if (FX.stripToken === myToken) fxBuildStrip();
}

function fxSetFrameButtons(on) {
  ["fxSave", "fxCopy", "fxPlay", "fxPrev", "fxNext", "fxBack1", "fxFwd1", "fxStart"]
    .forEach((id) => { $(id).disabled = !on; });
}

/* ---------- 帧时间跟踪 ---------- */

function fxStartLoop() {
  if (!RVFC || FX.loop) return;
  FX.loop = true;
  const video = fxVideo();
  const loop = (now, meta) => {
    // 暂停时帧时间由 seek 结果决定，别让回调里的旧时间把 HUD 拉回去
    if (!video.paused && meta && isFinite(meta.mediaTime)) {
      FX.t = meta.mediaTime;
      fxUpdateHud();
    }
    video.requestVideoFrameCallback(loop);
  };
  video.requestVideoFrameCallback(loop);
}

function seekVideoElement(video, target, timeoutMs) {
  if (Math.abs(video.currentTime - target) < 0.0008) return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      video.removeEventListener("seeked", finish);
      clearTimeout(to);
      resolve();
    };
    const to = setTimeout(finish, timeoutMs || 1500);
    video.addEventListener("seeked", finish);
    try {
      video.currentTime = target;
    } catch (e) {
      finish();
    }
  });
}

async function probeVideoFps(video, shouldAbort, maxWaitMs) {
  if (!RVFC) return 0;
  const samples = [];
  let last = null;
  let stop = false;
  let aborted = false;
  const cb = (now, meta) => {
    if (aborted) return;
    if (last !== null) {
      const d = meta.mediaTime - last;
      if (d > 0.003 && d < 0.2) samples.push(d);
    }
    last = meta.mediaTime;
    if (samples.length >= 10) {
      stop = true;
      return;
    }
    video.requestVideoFrameCallback(cb);
  };
  const wasMuted = video.muted;
  video.muted = true;
  try {
    video.requestVideoFrameCallback(cb);
    await video.play();
    const t0 = performance.now();
    while (!stop && performance.now() - t0 < (maxWaitMs || 1600)) {
      if (shouldAbort && shouldAbort()) {
        aborted = true;
        break;
      }
      await new Promise((r) => setTimeout(r, 25));
    }
  } catch (e) {
    /* 自动播放被拦，退回默认帧率 */
  }
  aborted = true;
  video.pause();
  video.muted = wasMuted;

  if (samples.length < 4) return 0;
  const sorted = samples.slice().sort((a, b) => a - b);
  // 取偏小的分位：丢帧只会让间隔变长，不会变短
  const fd = sorted[Math.floor(sorted.length * 0.2)];
  let fps = 1 / fd;
  const common = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 120, 240];
  for (const c of common) {
    if (Math.abs(fps - c) / c < 0.03) {
      fps = c;
      break;
    }
  }
  return (fps >= 8 && fps <= 240) ? fps : 0;
}

async function runDeckStepQueue(deck, getVideo, seekFn) {
  deck.stepBusy = true;
  const video = getVideo();
  if (video && !video.paused) video.pause();
  try {
    while (deck.stepPending !== 0 && getVideo()) {
      const n = deck.stepPending;
      deck.stepPending = 0;
      const fps = deck.fps || 30;
      const maxFrame = Math.max(0, Math.floor((deck.dur - 0.0005) * fps));
      const cur = Math.round(deck.t * fps);
      const tf = fxClamp(cur + n, 0, maxFrame);
      await seekFn((tf + 0.5) / fps);
    }
  } finally {
    deck.stepBusy = false;
  }
}

// 把任意时间点对齐到它所在这一帧的起点
function fxQuantize(t) {
  const fps = FX.fps || 30;
  const q = Math.floor(t * fps + 0.001) / fps;
  return fxClamp(q, 0, Math.max(0, FX.dur - 0.0005));
}

async function fxSeekTo(t) {
  const video = fxVideo();
  if (!FX.ready) return;
  const target = fxClamp(t, 0, Math.max(0, FX.dur - 0.0005));
  await seekVideoElement(video, target, 1500);
  FX.t = fxQuantize(target);
  fxUpdateHud();
}

/* ---------- 帧率识别 ---------- */

function fxSetFps(v, confident) {
  v = Math.round(v);
  if (!(v >= 1 && v <= 240)) v = 30;
  FX.fps = v;
  $("fxFrameNo").title = confident ? "帧率自动识别" : "按默认 30 估算，逐帧可能略有偏差";
}

async function fxProbeFps(token) {
  const video = fxVideo();
  const epoch0 = FX.seekEpoch;
  const resumeAt = FX.t;
  const fps = await probeVideoFps(
    video,
    () => FX.seekEpoch !== epoch0 || FX.stripToken !== token,
    1600,
  );
  fxSetFps(fps || 30, !!fps);
  await fxSeekTo(resumeAt);
  fxUpdateHud();
  fxDrawTicks();
}

/* ---------- HUD / 时间轴 ---------- */

function fxResetHud() {
  $("fxClock").innerHTML = "00:00.000 <i>/</i> 00:00.000";
  $("fxFrameNo").textContent = "第 — 帧";
  const seek = $("fxSeek");
  seek.value = 0;
  seek.max = 1;
  fxPaintSeek();
  $("fxTicks").innerHTML = "";
}

function fxUpdateHud() {
  $("fxClock").innerHTML = fmtFxTime(FX.t) + " <i>/</i> " + fmtFxTime(FX.dur);
  if (FX.ready) {
    const f = Math.floor(FX.t * FX.fps + 0.001) + 1;
    $("fxFrameNo").textContent = `第 ${f} 帧 · ${FX.fps} fps`;
  }
  const seek = $("fxSeek");
  if (!FX.dragging) {
    seek.value = fxClamp(FX.t, 0, parseFloat(seek.max) || 1);
    fxPaintSeek();
  }
}

function fxPaintSeek() {
  const seek = $("fxSeek");
  const max = parseFloat(seek.max) || 1;
  const p = fxClamp((parseFloat(seek.value) || 0) / max, 0, 1) * 100;
  seek.style.background =
    "linear-gradient(to right, var(--copper) 0%, var(--copper) " + p +
    "%, var(--line-2) " + p + "%, var(--line-2) 100%)";
}

function fxDrawTicks() {
  const ticks = $("fxTicks");
  ticks.innerHTML = "";
  if (!FX.dur) return;
  const n = FX.dur < 12 ? 4 : (FX.dur < 60 ? 5 : 6);
  for (let i = 0; i <= n; i += 1) {
    const sp = document.createElement("span");
    sp.textContent = fmtFxShort(FX.dur * i / n);
    sp.style.left = (i / n * 100) + "%";
    ticks.appendChild(sp);
  }
}

/* ---------- 胶片条 ---------- */

// 缩略图用「裁切填充」，竖屏视频也不会被压成细条
function fxDrawCover(ctx, el, W, H) {
  const vw = el.videoWidth;
  const vh = el.videoHeight;
  if (!vw || !vh) return;
  const s = Math.max(W / vw, H / vh);
  const dw = vw * s;
  const dh = vh * s;
  ctx.drawImage(el, (W - dw) / 2, (H - dh) / 2, dw, dh);
}

async function fxBuildStrip() {
  const video = fxVideo();
  const strip = $("fxStrip");
  const token = ++FX.stripToken;
  strip.innerHTML = "";
  const d = FX.dur;
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!d || !vw || !vh) return;

  const n = d < 4 ? 8 : (d < 15 ? 12 : 16);
  const H = 50;
  const W = Math.max(34, Math.round(H * vw / vh));
  strip.innerHTML = '<span class="fx-strip-msg">正在生成缩略图…</span>';

  const canvases = [];
  for (let i = 0; i < n; i += 1) {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    c.dataset.t = (d * (i + 0.5) / n).toFixed(3);
    c.addEventListener("click", () => fxSeekTo(parseFloat(c.dataset.t)));
    strip.appendChild(c);
    canvases.push(c);
  }

  const prev = FX.t;
  for (let i = 0; i < n; i += 1) {
    if (token !== FX.stripToken) return;
    await fxSeekTo(d * (i + 0.5) / n);
    if (token !== FX.stripToken) return;
    if (i === 0) {
      const msg = strip.querySelector(".fx-strip-msg");
      if (msg) msg.remove();
    }
    try {
      fxDrawCover(canvases[i].getContext("2d"), video, W, H);
      canvases[i].classList.add("on");
    } catch (e) {
      /* 单张失败就留着空白 */
    }
  }
  if (token === FX.stripToken) await fxSeekTo(prev);
}

/* ---------- 播放控制 ---------- */

function fxTogglePlay() {
  const video = fxVideo();
  if (!FX.ready) return;
  if (video.paused) video.play().catch(() => {});
  else video.pause();
}

function fxSyncPlayIcon() {
  const video = fxVideo();
  const playing = !video.paused && !video.ended;
  $("fxPlayIcon").innerHTML = playing
    ? '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>'
    : '<path d="M8 5v14l11-7z"/>';
}

// 逐帧步进：用帧序号算，连按/长按不会丢步（seek 没回来时先攒着）
function fxStep(dir, mult) {
  if (!FX.ready) return;
  FX.stepPending += dir * (mult || 1);
  if (!FX.stepBusy) runDeckStepQueue(FX, fxVideo, fxSeekTo);
}

/* ---------- 导出（截帧页和画廊查看器共用） ---------- */

async function frameGrabCanvas(video) {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return null;
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const c = document.createElement("canvas");
  c.width = vw;
  c.height = vh;
  c.getContext("2d").drawImage(video, 0, 0, vw, vh);
  return c;
}

// canvas 原始分辨率 → PNG → 直接写进 截帧 目录
async function frameSavePng(video, srcPath, seconds) {
  const c = await frameGrabCanvas(video);
  if (!c) throw new Error("这一帧还没解出来，稍等一下再试");
  const blob = await new Promise((res) => c.toBlob(res, "image/png"));
  if (!blob) throw new Error("导出 PNG 失败");
  const qs = new URLSearchParams({
    path: srcPath,
    seconds: Number(seconds || 0).toFixed(3),
    root: currentRoot(),
  });
  const res = await fetch("/api/frame/save?" + qs.toString(), {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: blob,
  });
  const r = await res.json().catch(() => ({}));
  if (!res.ok || !r.ok) throw new Error(r.message || `HTTP ${res.status}`);
  state.gallery.dirty = true;
  return { ...r, width: c.width, height: c.height };
}

async function frameCopyPng(video) {
  const c = await frameGrabCanvas(video);
  if (!c) throw new Error("这一帧还没解出来");
  const blob = await new Promise((res) => c.toBlob(res, "image/png"));
  if (!navigator.clipboard || !window.ClipboardItem) throw new Error("浏览器不支持");
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  return { width: c.width, height: c.height };
}

function fxShowSaved(r) {
  FX.lastOut = folderOf(r.path);
  const box = $("fxSaved");
  box.innerHTML = "";
  const img = document.createElement("img");
  img.src = "/api/thumb?path=" + encodeURIComponent(r.path) + "&w=360";
  img.alt = "";
  const info = mk("div", "fx-saved-info");
  info.append(mk("div", "fx-saved-name", r.name));
  info.append(mk("div", "fx-saved-path", r.rel));
  const btn = mk("button", "ghost", "打开文件夹");
  btn.type = "button";
  btn.addEventListener("click", () => fxOpenDir(folderOf(r.path)));
  box.append(img, info, btn);
  box.hidden = false;
}

async function fxSave() {
  const f = state.frame;
  if (!FX.ready || FX.busy || !f.selected) return;
  FX.busy = true;
  $("fxSave").disabled = true;
  try {
    await fxSeekTo(FX.t);
    const r = await frameSavePng(fxVideo(), f.selected.path, FX.t);
    fxShowSaved(r);
    showToast(`已存到 ${r.rel}（${r.width}×${r.height}）`);
  } catch (e) {
    showToast("保存失败：" + e.message);
  } finally {
    FX.busy = false;
    $("fxSave").disabled = false;
  }
}

async function fxCopy() {
  if (!FX.ready || FX.busy) return;
  FX.busy = true;
  $("fxCopy").disabled = true;
  try {
    await fxSeekTo(FX.t);
    const s = await frameCopyPng(fxVideo());
    showToast(`已复制到剪贴板（${s.width}×${s.height}）`);
  } catch (e) {
    showToast("复制失败，用「保存 PNG」吧");
  } finally {
    FX.busy = false;
    $("fxCopy").disabled = false;
  }
}

/* ---------- 打不开的文件 ---------- */

function fxHideError() {
  $("fxErr").hidden = true;
  fxVideo().style.visibility = "";
}

function fxShowDecodeError(it) {
  const video = fxVideo();
  video.style.visibility = "hidden";
  fxSetFrameButtons(false);
  const base = it.name.replace(/\.[^.]+$/, "");
  const isMov = /\.mov$/i.test(it.name);
  $("fxErrTitle").textContent = "这个文件解不开";
  $("fxErrBody").innerHTML =
    (isMov
      ? "iPhone 实况的 MOV 常见是 <b>HEVC / H.265</b> 编码，Chrome 要先装系统的「HEVC 视频扩展」才能解码。<br>"
      : "这个文件的编码浏览器认不出来（可能是 HEVC、ProRes 或 AV1）。<br>") +
    "两条路：① 去 Microsoft Store 装「HEVC 视频扩展」，装完刷新页面再选这条；" +
    "② 先用 ffmpeg 挑一帧应急，或者转成 H.264 再回来逐帧截。";
  $("fxErrCmd").textContent =
    'ffmpeg -i "' + it.name + '" -c:v libx264 -crf 18 -pix_fmt yuv420p "' +
    base + '_h264.mp4"';
  $("fxErr").hidden = false;
}

async function fxOpenDir(path) {
  if (!path) return;
  try {
    const r = await api("/api/openfolder", { path });
    if (!r.ok) showToast(r.message || "打不开这个文件夹");
  } catch (e) {
    showToast("打不开文件夹：" + e.message);
  }
}

/* ---------- 事件绑定 ---------- */

function fxInit() {
  const video = fxVideo();
  fxSetFrameButtons(false);

  video.addEventListener("play", fxSyncPlayIcon);
  video.addEventListener("pause", fxSyncPlayIcon);
  video.addEventListener("ended", fxSyncPlayIcon);
  video.addEventListener("seeking", () => { FX.seekEpoch += 1; });
  video.addEventListener("timeupdate", () => {
    if (!FX.dragging && FX.ready && !RVFC) {
      FX.t = video.currentTime;
      fxUpdateHud();
    }
  });
  video.addEventListener("error", () => {
    const sel = state.frame.selected;
    if (!FX.ready && FX.loading && sel) {
      FX.loading = false;
      fxShowDecodeError(sel);
    }
  });
  video.addEventListener("dblclick", fxTogglePlay);

  const seek = $("fxSeek");
  seek.addEventListener("input", () => {
    FX.dragging = true;
    fxPaintSeek();
    FX.t = parseFloat(seek.value) || 0;
    fxUpdateHud();
    try { video.currentTime = FX.t; } catch (e) { /* 忽略 */ }
  });
  seek.addEventListener("change", () => { FX.dragging = false; });
  seek.addEventListener("pointerup", () => { FX.dragging = false; });

  $("fxPlay").addEventListener("click", fxTogglePlay);
  $("fxPrev").addEventListener("click", () => fxStep(-1, 1));
  $("fxNext").addEventListener("click", () => fxStep(1, 1));
  $("fxBack1").addEventListener("click", () => fxStep(-1, FX.fps));
  $("fxFwd1").addEventListener("click", () => fxStep(1, FX.fps));
  $("fxStart").addEventListener("click", () => {
    video.pause();
    fxSeekTo(0);
  });

  $("fxSave").addEventListener("click", fxSave);
  $("fxCopy").addEventListener("click", fxCopy);

  $("fxZoom").addEventListener("click", () => {
    const on = document.body.classList.toggle("fx-zoom");
    $("fxZoom").textContent = on ? "原始像素" : "适应窗口";
  });

  $("fxRescan").addEventListener("click", () => fxScan(true));
  $("fxOpenOut").addEventListener("click", async () => {
    try {
      const r = await api("/api/frame/open", { root: currentRoot() });
      if (!r.ok) showToast(r.message || "打不开截帧文件夹");
    } catch (e) {
      showToast("打不开截帧文件夹：" + e.message);
    }
  });

  let searchTimer = null;
  $("fxSearch").addEventListener("input", (e) => {
    const value = e.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.frame.q = value;
      fxScan(false);
    }, 260);
  });
  $("fxKind").addEventListener("change", (e) => {
    state.frame.kind = e.target.value;
    fxScan(false);
  });

  $("fxErrClose").addEventListener("click", fxHideError);
  $("fxErrCopy").addEventListener("click", async () => {
    const txt = $("fxErrCmd").textContent;
    if (!txt) return;
    try {
      await navigator.clipboard.writeText(txt);
      showToast("命令已复制");
    } catch (e) {
      showToast("复制失败，手动选中吧");
    }
  });
  $("fxErrAuto").addEventListener("click", async () => {
    const f = state.frame;
    if (!f.selected) return;
    const btn = $("fxErrAuto");
    btn.disabled = true;
    btn.textContent = "挑帧中…";
    try {
      const r = await api("/api/frame", {
        path: f.selected.path,
        root: currentRoot(),
        auto: true,
      });
      if (!r.ok) throw new Error(r.message || "抽帧失败");
      state.gallery.dirty = true;
      fxShowSaved(r);
      showToast("已用 ffmpeg 存到 " + r.rel);
    } catch (e) {
      showToast("抽帧失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "用 ffmpeg 挑最清晰一帧";
    }
  });
}

fxInit();

// 键盘快捷键：只在截帧工具、且没有弹窗时接管
window.addEventListener("keydown", (e) => {
  if (!isFrameActive()) return;
  // 查看器 / 设置这类弹窗开着时，把按键还回去
  if (document.querySelector("dialog[open]")) return;
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select";
  if (e.key === "Escape") {
    fxHideError();
    if (typing) e.target.blur();
    return;
  }
  if (typing || !FX.ready) return;

  // 中文输入法开着时字母会被吞成 Process，key 认不出就用 code 兜底
  const key = (e.key || "").toLowerCase();
  const code = e.code || "";
  const shift = e.shiftKey;
  const run = (fn) => {
    e.preventDefault();
    fn();
  };

  if (key === " " || code === "Space") return run(fxTogglePlay);
  if (key === "arrowleft") return run(() => fxStep(-1, shift ? FX.fps : 1));
  if (key === "arrowright") return run(() => fxStep(1, shift ? FX.fps : 1));
  if (key === "home") {
    return run(() => {
      fxVideo().pause();
      fxSeekTo(0);
    });
  }
  if (key === "end") {
    return run(() => {
      fxVideo().pause();
      fxSeekTo(FX.dur - 0.05);
    });
  }
  if (key === "j" || code === "KeyJ") return run(() => fxStep(-1, FX.fps));
  if (key === "l" || code === "KeyL") return run(() => fxStep(1, FX.fps));
  if (key === "s" || code === "KeyS" || key === "enter") return run(fxSave);
  if (key === "c" || code === "KeyC") return run(fxCopy);
});

/* ---------------- 画廊查看器里的逐帧截取 ---------------- */

const VFR = {
  path: "",
  name: "",
  dur: 0,
  fps: 30,
  t: 0,
  ready: false,
  busy: false,
  stripToken: 0,
  loop: false,
  stepBusy: false,
  stepPending: 0,
};

function vfReset() {
  VFR.path = "";
  VFR.name = "";
  VFR.dur = 0;
  VFR.t = 0;
  VFR.ready = false;
  VFR.busy = false;
  VFR.loop = false;
  VFR.stripToken += 1;
  VFR.stepPending = 0;
  $("vfBar").hidden = true;
  $("vfStrip").hidden = true;
  $("vfStrip").innerHTML = "";
  $("vfClock").innerHTML = "00:00.000 <i>/</i> 00:00.000";
  $("vfFrameNo").textContent = "第 — 帧";
  $("vfSave").disabled = true;
  $("vfCopy").disabled = true;
}

function vfUpdateHud() {
  if (!VFR.path) return;
  $("vfClock").innerHTML = fmtFxTime(VFR.t) + " <i>/</i> " + fmtFxTime(VFR.dur);
  $("vfFrameNo").textContent = VFR.ready
    ? `第 ${Math.floor(VFR.t * VFR.fps + 0.001) + 1} 帧 · ${VFR.fps} fps`
    : "第 — 帧";
}

function vfStartLoop(video) {
  if (!RVFC) return;
  VFR.loop = true;
  const loop = (now, meta) => {
    const cur = state.viewer.video;
    if (!cur) {
      VFR.loop = false;
      return;
    }
    // 暂停时帧时间由 seek 结果决定，别让回调里的旧时间把 HUD 拉回去
    if (!cur.paused && meta && isFinite(meta.mediaTime)) {
      VFR.t = meta.mediaTime;
      vfUpdateHud();
    }
    cur.requestVideoFrameCallback(loop);
  };
  video.requestVideoFrameCallback(loop);
}

async function vfInit(video, file) {
  VFR.path = file.path;
  VFR.name = file.name;
  VFR.dur = isFinite(video.duration) ? video.duration : 0;
  VFR.t = 0;
  VFR.fps = 30;
  VFR.ready = VFR.dur > 0;
  $("vfBar").hidden = false;
  $("vfStrip").hidden = false;
  $("vfSave").disabled = false;
  $("vfCopy").disabled = false;
  vfUpdateHud();
  vfSyncPlayIcon();
  vfStartLoop(video);
  video.addEventListener("seeked", () => {
    // 拖动播放器自带的进度条时也要跟着更新（播放中交给 frame callback）
    if (state.viewer.video !== video || !video.paused) return;
    const fps = VFR.fps || 30;
    VFR.t = Math.floor(video.currentTime * fps + 0.001) / fps;
    vfUpdateHud();
  });
  if (!RVFC) {
    video.addEventListener("timeupdate", () => {
      if (state.viewer.video !== video) return;
      VFR.t = video.currentTime;
      vfUpdateHud();
    });
  }
  // 另开一条隐藏的副本测帧率、做胶片条，不打断正在播放的画面
  vfProbeAndStrip(video.currentSrc || video.src);
}

async function vfProbeAndStrip(src) {
  const token = ++VFR.stripToken;
  const strip = $("vfStrip");
  strip.innerHTML = '<span class="empty">正在生成缩略图…</span>';
  const probe = document.createElement("video");
  probe.muted = true;
  probe.playsInline = true;
  probe.preload = "auto";
  probe.src = src;
  try {
    await fxOnce(probe, "loadedmetadata", 12000, "error");
  } catch (e) {
    strip.innerHTML = "";
    return;
  }
  if (token !== VFR.stripToken) return;

  const fps = Math.round(await probeVideoFps(probe, () => token !== VFR.stripToken, 1200));
  if (token !== VFR.stripToken) return;
  if (fps) {
    VFR.fps = fps;
    vfUpdateHud();
  }

  const dur = isFinite(probe.duration) ? probe.duration : VFR.dur;
  const vw = probe.videoWidth;
  const vh = probe.videoHeight;
  if (!dur || !vw || !vh) {
    strip.innerHTML = "";
    probe.removeAttribute("src");
    probe.load();
    return;
  }

  const n = 10;
  const H = 40;
  const W = Math.max(28, Math.round(H * vw / vh));
  strip.innerHTML = "";
  const canvases = [];
  for (let i = 0; i < n; i += 1) {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    c.dataset.t = (dur * (i + 0.5) / n).toFixed(3);
    c.addEventListener("click", () => vfSeek(parseFloat(c.dataset.t)));
    strip.appendChild(c);
    canvases.push(c);
  }
  for (let i = 0; i < n; i += 1) {
    if (token !== VFR.stripToken) break;
    const seekTarget = fxClamp(dur * (i + 0.5) / n, 0, Math.max(0, dur - 0.0005));
    await seekVideoElement(probe, seekTarget, 1200);
    if (token !== VFR.stripToken) break;
    try {
      fxDrawCover(canvases[i].getContext("2d"), probe, W, H);
    } catch (e) {
      /* 单张失败就留着空白 */
    }
  }
  probe.removeAttribute("src");
  probe.load();
}

async function vfSeek(t) {
  const video = state.viewer.video;
  if (!video || !VFR.ready) return;
  const target = fxClamp(t, 0, Math.max(0, VFR.dur - 0.0005));
  await seekVideoElement(video, target, 1200);
  const fps = VFR.fps || 30;
  VFR.t = Math.floor(target * fps + 0.001) / fps;
  vfUpdateHud();
}

function vfTogglePlay() {
  const video = state.viewer.video;
  if (!video || !VFR.ready) return;
  if (video.paused) video.play().catch(() => {});
  else video.pause();
}

function vfSyncPlayIcon() {
  const video = state.viewer.video;
  const playing = video && !video.paused && !video.ended;
  $("vfPlayIcon").innerHTML = playing
    ? '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>'
    : '<path d="M8 5v14l11-7z"/>';
}

function vfStep(dir, mult) {
  if (!VFR.ready) return;
  VFR.stepPending += dir * (mult || 1);
  if (!VFR.stepBusy) runDeckStepQueue(VFR, () => state.viewer.video, vfSeek);
}

async function vfSave() {
  const v = state.viewer;
  if (!VFR.ready || VFR.busy || !v.video || !VFR.path) return;
  VFR.busy = true;
  $("vfSave").disabled = true;
  try {
    await vfSeek(VFR.t);
    const r = await frameSavePng(v.video, VFR.path, VFR.t);
    appendViewerFrame(r);
    showToast(`已存到 ${r.rel}（${r.width}×${r.height}）`);
  } catch (e) {
    showToast("保存失败：" + e.message);
  } finally {
    VFR.busy = false;
    $("vfSave").disabled = false;
  }
}

async function vfCopy() {
  const v = state.viewer;
  if (!VFR.ready || VFR.busy || !v.video) return;
  VFR.busy = true;
  $("vfCopy").disabled = true;
  try {
    await vfSeek(VFR.t);
    const s = await frameCopyPng(v.video);
    showToast(`已复制到剪贴板（${s.width}×${s.height}）`);
  } catch (e) {
    showToast("复制失败，用「保存 PNG」吧");
  } finally {
    VFR.busy = false;
    $("vfCopy").disabled = false;
  }
}

// 从画廊直接把这支视频送进工具页的视频截帧
async function fxOpenPath(path, name, creator) {
  setView("frame");
  if (state.frame.pending) {
    try {
      await state.frame.pending;
    } catch (e) {
      /* 扫描失败也照样把这条塞进去 */
    }
  }
  let it = state.frame.items.find((x) => x.path === path);
  if (!it) {
    it = {
      path,
      name: name || path.split(/[\\/]/).pop(),
      creator: creator || "",
      live: false,
      thumb: path,
      size: 0,
      time: "",
    };
  }
  fxSelect(it);
}

$("vfPlay").addEventListener("click", vfTogglePlay);
$("vfPrev").addEventListener("click", () => vfStep(-1, 1));
$("vfNext").addEventListener("click", () => vfStep(1, 1));
$("vfBack1").addEventListener("click", () => vfStep(-1, VFR.fps));
$("vfFwd1").addEventListener("click", () => vfStep(1, VFR.fps));
$("vfStart").addEventListener("click", () => {
  const video = state.viewer.video;
  if (video) video.pause();
  vfSeek(0);
});
$("vfSave").addEventListener("click", vfSave);
$("vfCopy").addEventListener("click", vfCopy);
$("vfDeck").addEventListener("click", () => {
  if (!VFR.path) return;
  const path = VFR.path;
  const name = VFR.name;
  const creator = state.viewer.creator || "";
  $("viewer").close();
  fxOpenPath(path, name, creator);
});



document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});

$("toLive").addEventListener("click", () => {
  state.tool = "live";
  setView("tools");
});

/* ---------------- 事件绑定 ---------------- */

$("go").addEventListener("click", doParse);
$("parseStop").addEventListener("click", stopParse);
$("link").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doParse();
});
$("downloadAll").addEventListener("click", doDownload);
$("retryFailed").addEventListener("click", () => {
  if (state.running || !state.failedIds.length) return;
  const failed = new Set(state.failedIds);
  state.selected = new Set(state.items.filter((it) => failed.has(it.id)).map((it) => it.id));
  document.querySelectorAll("#manifest .row").forEach((row) => {
    const pick = row.querySelector(".pick");
    if (pick) pick.checked = state.selected.has(row.dataset.id);
  });
  updateSelUI();
  doDownload();
});

document.querySelectorAll("#userTabs .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (state.running || btn.dataset.tab === state.tab) return;
    state.tab = btn.dataset.tab;
    $("userTabs").querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("is-on", b === btn);
    });
    doParse();
  });
});
$("cancel").addEventListener("click", () => {
  if (state.jobId) api("/api/cancel", { id: state.jobId });
});

/* ---------------- 初始化 ---------------- */

$("status").addEventListener("click", async () => {
  if (state.connected) return;
  $("statusText").textContent = "正在启动…";
  try {
    const r = await api("/api/start-api", {});
    showToast(r.message || "");
    setTimeout(checkHealth, 4000);
  } catch (e) {
    showToast("启动失败：" + e.message);
  }
});

$("status").style.cursor = "pointer";

setMode("work");
loadBuild().finally(checkHealth);
loadSettings();
setView("gallery");
setInterval(checkHealth, 5000);

/* ---------------- 关掉窗口就收掉后台 ---------------- */

// 页面关了发一个 beacon；服务端等 8 秒，没有新请求就退出（顺带收掉 DouK 的隐藏 API）。
// 刷新页面时新页面会马上请求静态文件，等于自动取消退出。
// 页面开一条常连（SSE），窗口一关连接就断，后端据此退出；刷新时新页面马上重连，不会误杀
let presence = null;
try {
  presence = new EventSource("/api/events");
} catch (e) {
  presence = null;
}

let saidBye = false;
function sayBye() {
  if (saidBye) return;
  saidBye = true;
  try {
    navigator.sendBeacon("/api/shutdown");
  } catch (e) {
    /* 没有 sendBeacon 就算了，手动 停止.cmd */
  }
}
// beforeunload 在正常关窗口时也会触发；两个都监听，只发一次
window.addEventListener("pagehide", sayBye);
window.addEventListener("beforeunload", sayBye);

/* ---------------- 快捷操作 ---------------- */

const DOUYIN_LINK = /(v\.douyin\.com|douyin\.com|iesdouyin\.com|xiaohongshu\.com|rednote\.com|xhslink\.com)/i;

// 粘贴链接或分享文案就自动解析，省掉一次点按
$("link").addEventListener("paste", () => {
  setTimeout(() => {
    const v = $("link").value.trim();
    if (!v || !DOUYIN_LINK.test(v)) return;
    if ($("go").disabled) return;   // 正在解析，别叠加
    doParse();
  }, 40);
});

// Ctrl / Cmd + K 跳到下载链接框；在空白处按 / 聚焦当前页搜索框（或下载链接框）
window.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]") || isFrameActive()) return;
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select";
  if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    if (state.view !== "download") setView("download");
    $("link").focus();
    $("link").select();
    return;
  }
  if (e.key === "/" && !typing) {
    e.preventDefault();
    const target = state.view === "gallery"
      ? $("galSearch")
      : (state.view === "creator" ? $("libSearch") : $("link"));
    if (target) {
      target.focus();
      target.select();
    }
  }
});

// 在「保存到」里按回车立刻生效，不用再点空白处
$("destPath").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("destPath").blur();
  }
});


/* ---------------- 发现 ---------------- */

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function proxyImg(url) {
  return url ? `/api/proxy/img?url=${encodeURIComponent(url)}` : "";
}

function fmtCount(n) {
  const num = Number(n) || 0;
  if (num >= 10000) return (num / 10000).toFixed(1).replace(/\.0$/, "") + "w";
  return String(num);
}

function jumpToParse(url, mode) {
  if (state.running) {
    showToast("正在下载，等下载完再解析新的");
    return;
  }
  if (state.parseJob) {
    showToast("上一个还在解析，等它跑完再换链接");
    return;
  }
  setView("download");
  setMode(mode);
  $("link").value = url;
  doParse();
}

function showDiscoverXhsHint(msg, isError) {
  const el = $("discoverXhsHint");
  el.hidden = !msg;
  el.textContent = msg || "";
  el.className = isError ? "hint is-error" : "hint";
}

const XHS_IRRELEVANT_RE = /美食|做饭|菜谱|烘焙|奶茶|咖啡|外卖|探店|餐厅|火锅|烧烤|游戏|王者|原神|和平精英|手游|电竞|显卡|装机|评测|数码|代码|编程|装修|租房|买房|家具|收纳|家政|考研|考公|作业|论文|数学|英语|面试|招聘|求职|副业|理财|股票|基金|男装|男生|帅哥|猛男|兄弟|寸头|男大|钓鱼|汽车|摩托|机车|宠物|猫猫|狗狗|小猫|小狗|猫咪|二哈|表情包|壁纸|聊天记录|截图|求助|避雷|吐槽|黄金|快递|拆箱/i;

function isDiscoverItemVisible(it) {
  if (!it || !it.id) return false;
  if (state.discover.hiddenIds.has(it.id)) return false;
  const text = `${it.title || ""} ${it.nickname || ""}`;
  if (state.discover.filterIrrelevant && XHS_IRRELEVANT_RE.test(text)) {
    return false;
  }
  const rawWords = (state.discover.blockWords || "").trim();
  if (rawWords) {
    const words = rawWords.split(/[\s,，]+/).filter(Boolean);
    const lower = text.toLowerCase();
    for (const w of words) {
      if (lower.includes(w.toLowerCase())) return false;
    }
  }
  return true;
}

function renderXhsDiscover() {
  const grid = $("discoverXhsGrid");
  const raw = state.discover.xhsItems || [];
  const items = raw.map((it, idx) => ({ it, idx })).filter(({ it }) => isDiscoverItemVisible(it));
  if (!items.length) {
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = items.map(({ it, idx }) => `
    <article class="discover-card">
      <div class="discover-cover-wrap">
        <img class="discover-cover" src="${escapeHtml(proxyImg(it.cover))}" loading="lazy" alt="">
        <button class="discover-dismiss" data-dxhs-hide="${idx}" type="button" title="不看这条">×</button>
        <span class="discover-badge">${escapeHtml(it.type || "图文")}</span>
      </div>
      <div class="discover-body">
        <h3 class="discover-title">${escapeHtml(it.title || "无标题")}</h3>
        <div class="discover-author">
          ${it.avatar ? `<img class="discover-avatar" src="${escapeHtml(proxyImg(it.avatar))}" loading="lazy" alt="">` : ""}
          <span class="discover-nick">${escapeHtml(it.nickname || "小红书用户")}</span>
          <span class="discover-likes">♥ ${escapeHtml(it.liked_count || "0")}</span>
        </div>
        <div class="discover-actions">
          <button class="mini" data-dxhs-user="${idx}" type="button">看Ta主页</button>
          <button class="mini" data-dxhs-note="${idx}" type="button">预览图文</button>
        </div>
      </div>
    </article>
  `).join("");
}

async function loadXhsHomefeed(reset) {
  if (state.discover.loading) return;
  state.discover.loading = true;
  if (reset) {
    state.discover.cursorScore = "";
    state.discover.noteIndex = 0;
    state.discover.xhsItems = [];
    renderXhsDiscover();
  }
  showDiscoverXhsHint("正在加载小红书主页推荐…", false);
  $("discoverXhsRefresh").disabled = true;
  $("discoverXhsMore").hidden = true;
  try {
    const qs = new URLSearchParams({
      category: state.discover.category,
      cursor_score: state.discover.cursorScore,
      refresh_type: reset ? "1" : "3",
      note_index: String(state.discover.noteIndex),
    });
    const r = await api(`/api/discover/xhs?${qs.toString()}`);
    if (!r.ok) {
      showDiscoverXhsHint(r.message || "获取推荐失败", true);
      return;
    }
    state.discover.loaded = true;
    const incoming = r.items || [];
    const seen = new Set(state.discover.xhsItems.map((x) => x.id));
    for (const it of incoming) {
      if (it.id && !seen.has(it.id)) {
        seen.add(it.id);
        state.discover.xhsItems.push(it);
      }
    }
    state.discover.cursorScore = r.cursor_score || "";
    state.discover.noteIndex += incoming.length;
    showDiscoverXhsHint(state.discover.xhsItems.length ? "" : "这一栏暂时没有推荐内容", false);
    renderXhsDiscover();
    $("discoverXhsMore").hidden = !r.has_more;
  } catch (e) {
    showDiscoverXhsHint("加载失败：" + e.message, true);
  } finally {
    state.discover.loading = false;
    $("discoverXhsRefresh").disabled = false;
  }
}

function showDiscoverDyHint(msg, isError) {
  const el = $("discoverDyHint");
  el.hidden = !msg;
  el.textContent = msg || "";
  el.className = isError ? "hint is-error" : "hint";
}

function renderDyDiscover() {
  const grid = $("discoverDyGrid");
  const raw = state.discover.dyItems || [];
  const items = raw.map((it, idx) => ({ it, idx })).filter(({ it }) => !state.discover.hiddenIds.has(it.id));
  if (!items.length) {
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = items.map(({ it, idx }) => `
    <article class="discover-card">
      <div class="discover-cover-wrap">
        <img class="discover-cover" src="${escapeHtml(proxyImg(it.cover))}" loading="lazy" alt="">
        <button class="discover-dismiss" data-ddy-hide="${idx}" type="button" title="不看这条">×</button>
        <span class="discover-badge">${escapeHtml(it.type || "视频")}</span>
      </div>
      <div class="discover-body">
        <h3 class="discover-title">${escapeHtml(it.title || it.desc || "无标题")}</h3>
        <div class="discover-author">
          <span class="discover-nick">${escapeHtml(it.nickname || "抖音博主")}</span>
          <span class="discover-likes">♥ ${escapeHtml(fmtCount(it.liked_count || 0))}</span>
        </div>
        <div class="discover-actions">
          <button class="mini" data-ddy-user="${idx}" type="button">看Ta主页</button>
          <button class="mini" data-ddy-note="${idx}" type="button">预览视频</button>
        </div>
      </div>
    </article>
  `).join("");
}

async function loadDouyinBeautyFeed(reset) {
  if (state.discover.dyLoading) return;
  state.discover.dyLoading = true;
  if (reset) {
    state.discover.dyOffset = 0;
    state.discover.dyItems = [];
    renderDyDiscover();
  }
  const kw = $("discoverDyInput").value.trim();
  showDiscoverDyHint("正在加载抖音美女推荐…", false);
  $("discoverDyRefresh").disabled = true;
  $("discoverDyGo").disabled = true;
  $("discoverDyMore").hidden = true;
  try {
    const qs = new URLSearchParams({
      category: state.discover.dyCategory,
      offset: String(state.discover.dyOffset),
      keyword: kw,
    });
    const r = await api(`/api/discover/douyin/feed?${qs.toString()}`);
    if (!r.ok) {
      showDiscoverDyHint(r.message || "获取抖音推荐失败", true);
      return;
    }
    state.discover.dyLoaded = true;
    const incoming = r.items || [];
    const seen = new Set(state.discover.dyItems.map((x) => x.id));
    for (const it of incoming) {
      if (it.id && !seen.has(it.id)) {
        seen.add(it.id);
        state.discover.dyItems.push(it);
      }
    }
    state.discover.dyOffset = Number(r.next_offset || state.discover.dyOffset + 1);
    showDiscoverDyHint(state.discover.dyItems.length ? "" : (r.message || "暂无更多推荐"), false);
    renderDyDiscover();
    $("discoverDyMore").hidden = !r.has_more;
  } catch (e) {
    showDiscoverDyHint("加载失败：" + e.message, true);
  } finally {
    state.discover.dyLoading = false;
    $("discoverDyRefresh").disabled = false;
    $("discoverDyGo").disabled = false;
  }
}

document.querySelectorAll("#discoverPlatformTabs .mode").forEach((btn) => {
  btn.addEventListener("click", () => {
    const plat = btn.dataset.dplat;
    state.discover.platform = plat;
    $("discoverUserPane").hidden = true;
    document.querySelectorAll("#discoverPlatformTabs .mode").forEach((b) => {
      const on = b.dataset.dplat === plat;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    $("discoverXhsPane").hidden = plat !== "xhs";
    $("discoverDyPane").hidden = plat !== "douyin";
    if (plat === "xhs" && !state.discover.loaded && !state.discover.loading) {
      loadXhsHomefeed(true);
    } else if (plat === "douyin" && !state.discover.dyLoaded && !state.discover.dyLoading) {
      loadDouyinBeautyFeed(true);
    }
  });
});

document.querySelectorAll("#discoverXhsCats .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.discover.category = btn.dataset.cat;
    document.querySelectorAll("#discoverXhsCats .tab").forEach((b) => {
      b.classList.toggle("is-on", b === btn);
    });
    loadXhsHomefeed(true);
  });
});

$("discoverXhsRefresh").addEventListener("click", () => loadXhsHomefeed(true));
$("discoverXhsMore").addEventListener("click", () => loadXhsHomefeed(false));

$("discoverXhsGrid").addEventListener("click", (e) => {
  const hideBtn = e.target.closest("[data-dxhs-hide]");
  if (hideBtn) {
    const it = state.discover.xhsItems[Number(hideBtn.dataset.dxhsHide)];
    if (it && it.id) {
      state.discover.hiddenIds.add(it.id);
      const arr = Array.from(state.discover.hiddenIds).slice(-300);
      localStorage.setItem("douk_xhs_hidden_ids", JSON.stringify(arr));
      renderXhsDiscover();
    }
    return;
  }
  const uBtn = e.target.closest("[data-dxhs-user]");
  if (uBtn) {
    const it = state.discover.xhsItems[Number(uBtn.dataset.dxhsUser)];
    if (it && it.user_id) {
      openDiscoverUser("xhs", it.user_id, it.nickname, it.avatar, it.user_url);
    }
    return;
  }
  const nBtn = e.target.closest("[data-dxhs-note]");
  if (nBtn) {
    const it = state.discover.xhsItems[Number(nBtn.dataset.dxhsNote)];
    if (it) openDiscoverPreview(it);
    return;
  }
  const card = e.target.closest(".discover-card");
  if (card && (e.target.closest(".discover-cover-wrap") || e.target.closest(".discover-title"))) {
    const idx = Number((card.querySelector("[data-dxhs-note]") || {}).dataset?.dxhsNote);
    const it = state.discover.xhsItems[idx];
    if (it) openDiscoverPreview(it);
  }
});

$("discoverBlockInput").value = state.discover.blockWords;
$("discoverBeautyFilter").addEventListener("change", () => {
  state.discover.filterIrrelevant = $("discoverBeautyFilter").checked;
  renderXhsDiscover();
});
$("discoverBlockInput").addEventListener("input", () => {
  state.discover.blockWords = $("discoverBlockInput").value;
  localStorage.setItem("douk_xhs_block_words", state.discover.blockWords);
  renderXhsDiscover();
});

document.querySelectorAll("#discoverDyCats .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.discover.dyCategory = btn.dataset.dycat;
    $("discoverDyInput").value = "";
    document.querySelectorAll("#discoverDyCats .tab").forEach((b) => {
      b.classList.toggle("is-on", b === btn);
    });
    loadDouyinBeautyFeed(true);
  });
});

$("discoverDyGo").addEventListener("click", () => loadDouyinBeautyFeed(true));
$("discoverDyRefresh").addEventListener("click", () => {
  state.discover.dyOffset += 1;
  loadDouyinBeautyFeed(false);
});
$("discoverDyMore").addEventListener("click", () => loadDouyinBeautyFeed(false));
$("discoverDyInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    loadDouyinBeautyFeed(true);
  }
});

$("discoverDyGrid").addEventListener("click", (e) => {
  const hideBtn = e.target.closest("[data-ddy-hide]");
  if (hideBtn) {
    const it = state.discover.dyItems[Number(hideBtn.dataset.ddyHide)];
    if (it && it.id) {
      state.discover.hiddenIds.add(it.id);
      const arr = Array.from(state.discover.hiddenIds).slice(-300);
      localStorage.setItem("douk_xhs_hidden_ids", JSON.stringify(arr));
      renderDyDiscover();
    }
    return;
  }
  const uBtn = e.target.closest("[data-ddy-user]");
  if (uBtn) {
    const it = state.discover.dyItems[Number(uBtn.dataset.ddyUser)];
    if (it && it.sec_uid) {
      openDiscoverUser("douyin", it.sec_uid, it.nickname, "", it.user_url);
    }
    return;
  }
  const nBtn = e.target.closest("[data-ddy-note]");
  if (nBtn) {
    const it = state.discover.dyItems[Number(nBtn.dataset.ddyNote)];
    if (it) openDiscoverPreview(it);
    return;
  }
  const card = e.target.closest(".discover-card");
  if (card && (e.target.closest(".discover-cover-wrap") || e.target.closest(".discover-title"))) {
    const idx = Number((card.querySelector("[data-ddy-note]") || {}).dataset?.ddyNote);
    const it = state.discover.dyItems[idx];
    if (it) openDiscoverPreview(it);
  }
});


/* ---------------- 发现：博主主页浏览 & 作品预览 ---------------- */

const discoverBrowse = {
  platform: "xhs",
  id: "",
  nickname: "",
  avatar: "",
  userUrl: "",
  cursor: "",
  items: [],
  loading: false,
};

const discoverModal = {
  item: null,
  slides: [],
  index: 0,
  isVideo: false,
};

function renderDiscoverUserGrid() {
  const grid = $("discoverUserGrid");
  const items = discoverBrowse.items || [];
  if (!items.length) {
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = items.map((it, idx) => {
    const isPartial = !it.exists && it.download_state === "partial";
    const badgeHtml = it.exists
      ? '<span class="discover-exists-badge">已下载</span>'
      : (isPartial ? '<span class="discover-exists-badge is-partial">部分下载</span>' : "");
    const btnCls = it.exists ? " is-exists" : (isPartial ? " is-partial" : "");
    const btnText = it.exists ? "已下载" : (isPartial ? "部分下载" : "下载");
    return `
    <article class="discover-card">
      <div class="discover-cover-wrap" data-du-open="${idx}">
        <img class="discover-cover" src="${escapeHtml(proxyImg(it.cover))}" loading="lazy" alt="">
        ${badgeHtml}
        <span class="discover-badge">${escapeHtml(it.type || "图文")}</span>
      </div>
      <div class="discover-body">
        <h3 class="discover-title" data-du-open="${idx}">${escapeHtml(it.title || it.desc || "无标题")}</h3>
        <div class="discover-actions">
          <button class="mini" data-du-open="${idx}" type="button">预览</button>
          <button class="mini${btnCls}" data-du-dl="${idx}" type="button">${btnText}</button>
        </div>
      </div>
    </article>
  `;
  }).join("");
}

async function loadDiscoverUserPage(reset) {
  if (discoverBrowse.loading) return;
  discoverBrowse.loading = true;
  if (reset) {
    discoverBrowse.cursor = "";
    discoverBrowse.items = [];
    renderDiscoverUserGrid();
  }
  $("discoverUserHint").hidden = false;
  $("discoverUserHint").className = "hint";
  $("discoverUserHint").textContent = "正在加载博主主页作品…";
  $("discoverUserMore").hidden = true;
  try {
    let r;
    if (discoverBrowse.platform === "xhs") {
      const qs = new URLSearchParams({ user_id: discoverBrowse.id, cursor: discoverBrowse.cursor });
      r = await api(`/api/discover/xhs/user?${qs.toString()}`);
    } else {
      r = await api(`/api/discover/douyin/user?sec_uid=${encodeURIComponent(discoverBrowse.id)}`);
    }
    if (!r.ok) {
      $("discoverUserHint").className = "hint is-error";
      $("discoverUserHint").textContent = r.message || "加载博主主页失败";
      return;
    }
    if (r.nickname) {
      discoverBrowse.nickname = r.nickname;
      $("discoverUserName").textContent = r.nickname;
    }
    if (r.avatar) {
      discoverBrowse.avatar = r.avatar;
      $("discoverUserAvatar").src = proxyImg(r.avatar);
      $("discoverUserAvatar").hidden = false;
    }
    const incoming = r.items || [];
    const seen = new Set(discoverBrowse.items.map((x) => x.id));
    for (const it of incoming) {
      if (it.id && !seen.has(it.id)) {
        seen.add(it.id);
        discoverBrowse.items.push(it);
      }
    }
    discoverBrowse.cursor = r.cursor || "";
    const existsCount = discoverBrowse.items.filter((x) => x.exists).length;
    const partialCount = discoverBrowse.items.filter((x) => !x.exists && x.download_state === "partial").length;
    const statusParts = [];
    if (existsCount) statusParts.push(`已下载 ${existsCount} 篇`);
    if (partialCount) statusParts.push(`部分下载 ${partialCount} 篇`);
    $("discoverUserMeta").textContent = `已加载 ${discoverBrowse.items.length} 篇作品${statusParts.length ? `（本地${statusParts.join("，")}）` : ""}（点击封面可直接预览）`;
    $("discoverUserHint").hidden = discoverBrowse.items.length > 0;
    if (!discoverBrowse.items.length) {
      $("discoverUserHint").textContent = r.message || "暂无可显示的作品";
    }
    renderDiscoverUserGrid();
    $("discoverUserMore").hidden = !(discoverBrowse.platform === "xhs" && r.has_more);
  } catch (e) {
    $("discoverUserHint").className = "hint is-error";
    $("discoverUserHint").textContent = "加载失败：" + e.message;
  } finally {
    discoverBrowse.loading = false;
  }
}

function openDiscoverUser(platform, id, nickname, avatar, userUrl) {
  discoverBrowse.platform = platform;
  discoverBrowse.id = id;
  discoverBrowse.nickname = nickname || "博主主页";
  discoverBrowse.avatar = avatar || "";
  discoverBrowse.userUrl = userUrl || (
    platform === "xhs" ? `https://www.xiaohongshu.com/user/profile/${id}` : `https://www.douyin.com/user/${id}`
  );
  $("discoverXhsPane").hidden = true;
  $("discoverDyPane").hidden = true;
  $("discoverUserPane").hidden = false;
  $("discoverUserName").textContent = discoverBrowse.nickname;
  $("discoverUserMeta").textContent = "";
  if (discoverBrowse.avatar) {
    $("discoverUserAvatar").src = proxyImg(discoverBrowse.avatar);
    $("discoverUserAvatar").hidden = false;
  } else {
    $("discoverUserAvatar").hidden = true;
  }
  loadDiscoverUserPage(true);
}

$("discoverUserBack").addEventListener("click", () => {
  $("discoverUserPane").hidden = true;
  $("discoverXhsPane").hidden = state.discover.platform !== "xhs";
  $("discoverDyPane").hidden = state.discover.platform !== "douyin";
});

$("discoverUserMore").addEventListener("click", () => loadDiscoverUserPage(false));
$("discoverUserBatch").addEventListener("click", () => {
  if (discoverBrowse.userUrl) jumpToParse(discoverBrowse.userUrl, "user");
});

function renderPreviewSlide() {
  const img = $("dpImg");
  const vid = $("dpVideo");
  const prev = $("dpPrev");
  const next = $("dpNext");
  const counter = $("dpCounter");
  const slides = discoverModal.slides || [];
  if (!slides.length) {
    img.hidden = true;
    vid.hidden = true;
    prev.hidden = true;
    next.hidden = true;
    counter.hidden = true;
    return;
  }
  const cur = slides[discoverModal.index] || "";
  if (discoverModal.isVideo) {
    img.hidden = true;
    vid.hidden = false;
    vid.src = `/api/proxy/media?url=${encodeURIComponent(cur)}`;
    vid.load();
    prev.hidden = true;
    next.hidden = true;
    counter.hidden = true;
  } else {
    vid.pause();
    vid.hidden = true;
    img.hidden = false;
    img.src = proxyImg(cur);
    const multi = slides.length > 1;
    prev.hidden = !multi;
    next.hidden = !multi;
    counter.hidden = !multi;
    counter.textContent = `${discoverModal.index + 1} / ${slides.length}`;
  }
}

function isVideoMediaUrl(u) {
  return /\.mp4(\?|$)/i.test(u)
    || /sns-video|douyinvod\.com|mime_type=video_mp4|\/video\/tos\//i.test(u);
}

async function openDiscoverPreview(rawItem) {
  const dlg = $("discoverPreview");
  discoverModal.item = null;
  discoverModal.slides = rawItem.cover ? [rawItem.cover] : [];
  discoverModal.index = 0;
  discoverModal.isVideo = false;
  $("dpTitle").textContent = rawItem.title || rawItem.desc || "正在加载详情…";
  $("dpNick").textContent = rawItem.nickname || "";
  $("dpMeta").textContent = "正在加载高清图文…";
  if (rawItem.avatar) {
    $("dpAvatar").src = proxyImg(rawItem.avatar);
    $("dpAvatar").hidden = false;
  } else {
    $("dpAvatar").hidden = true;
  }
  $("dpDownload").disabled = true;
  $("dpViewUser").hidden = !(rawItem.user_id || rawItem.sec_uid);
  renderPreviewSlide();
  if (!dlg.open) dlg.showModal();

  try {
    let fullItem = rawItem;
    if ((rawItem.platform === "xhs" || !rawItem.downloads) && rawItem.id) {
      const qs = new URLSearchParams({
        id: rawItem.id,
        xsec_token: rawItem.xsec_token || "",
        xsec_source: "pc_feed",
      });
      const r = await api(`/api/discover/xhs/note?${qs.toString()}`);
      if (!r.ok || !r.item) {
        $("dpMeta").textContent = r.message || "获取详情失败";
        return;
      }
      fullItem = {
        ...r.item,
        avatar: rawItem.avatar || "",
        user_url: rawItem.user_url || `https://www.xiaohongshu.com/user/profile/${r.item.uid}`,
      };
    }
    discoverModal.item = fullItem;
    const urls = fullItem.downloads || [];
    const imgs = urls.filter((u) => !isVideoMediaUrl(u));
    const vids = urls.filter((u) => isVideoMediaUrl(u));
    if (fullItem.type === "视频" && vids.length) {
      discoverModal.isVideo = true;
      discoverModal.slides = [vids[0]];
    } else if (imgs.length) {
      discoverModal.isVideo = false;
      discoverModal.slides = imgs;
    } else if (vids.length) {
      discoverModal.isVideo = true;
      discoverModal.slides = [vids[0]];
    }
    discoverModal.index = 0;
    $("dpTitle").textContent = fullItem.desc || rawItem.title || "无标题";
    $("dpNick").textContent = fullItem.nickname || rawItem.nickname || "";
    $("dpMeta").textContent = [fullItem.type, fullItem.create_time, `${urls.length} 个文件`].filter(Boolean).join(" · ");
    $("dpDownload").disabled = !urls.length;
    renderPreviewSlide();
  } catch (e) {
    $("dpMeta").textContent = "加载失败：" + e.message;
  }
}

async function downloadSingleFromDiscover(item) {
  if (!item || !(item.downloads || []).length) {
    showToast("没有可下载的文件");
    return;
  }
  showToast("正在下载，请稍候…");
  try {
    const r = await api("/api/download", { items: [item], tab: "post" });
    if (!r.ok || !r.job) {
      showToast(r.message || "启动下载失败");
      return;
    }
    state.gallery.dirty = true;
    for (let i = 0; i < 120; i++) {
      await new Promise((res) => setTimeout(res, 500));
      const jr = await api(`/api/job?id=${encodeURIComponent(r.job)}`);
      const job = jr && jr.job;
      if (!job) break;
      if (!job.running) {
        const itState = (job.items || {})[item.id] || {};
        if (itState.status === "done") {
          item.exists = true;
          item.download_state = "all";
          renderDiscoverUserGrid();
          showToast("下载完成，已收录到画廊");
        } else if (itState.status === "skip") {
          item.exists = true;
          item.download_state = "all";
          renderDiscoverUserGrid();
          showToast("本地已存在该作品");
        } else if (itState.status === "error") {
          showToast("下载失败：" + (itState.message || "未知错误"));
        } else {
          showToast("下载已结束");
        }
        return;
      }
    }
    showToast("仍在后台下载中，稍后可在画廊查看");
  } catch (e) {
    showToast("下载失败：" + e.message);
  }
}

$("toggleLinkParse").addEventListener("click", () => {
  const panel = $("viewDownload");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) {
    $("link").focus();
  }
});

$("closeLinkParse").addEventListener("click", () => {
  $("viewDownload").hidden = true;
});

$("dpPrev").addEventListener("click", () => {
  const n = discoverModal.slides.length;
  if (!n) return;
  discoverModal.index = (discoverModal.index - 1 + n) % n;
  renderPreviewSlide();
});

$("dpNext").addEventListener("click", () => {
  const n = discoverModal.slides.length;
  if (!n) return;
  discoverModal.index = (discoverModal.index + 1) % n;
  renderPreviewSlide();
});

$("dpClose").addEventListener("click", () => {
  $("dpVideo").pause();
  $("discoverPreview").close();
});

$("discoverPreview").addEventListener("click", (e) => {
  if (e.target === $("discoverPreview")) {
    $("dpVideo").pause();
    $("discoverPreview").close();
  }
});

$("dpViewUser").addEventListener("click", () => {
  const it = discoverModal.item;
  $("dpVideo").pause();
  $("discoverPreview").close();
  if (!it) return;
  if (it.platform === "xhs" && it.uid) {
    openDiscoverUser("xhs", it.uid, it.nickname, it.avatar, it.user_url);
  } else if (it.sec_uid) {
    openDiscoverUser("douyin", it.sec_uid, it.nickname, it.avatar, "");
  }
});

$("dpDownload").addEventListener("click", () => {
  if (discoverModal.item) downloadSingleFromDiscover(discoverModal.item);
});

$("discoverUserGrid").addEventListener("click", async (e) => {
  const openEl = e.target.closest("[data-du-open]");
  if (openEl) {
    const it = discoverBrowse.items[Number(openEl.dataset.duOpen)];
    if (it) openDiscoverPreview(it);
    return;
  }
  const dlBtn = e.target.closest("[data-du-dl]");
  if (dlBtn) {
    const it = discoverBrowse.items[Number(dlBtn.dataset.duDl)];
    if (!it) return;
    if (it.downloads && it.downloads.length) {
      downloadSingleFromDiscover(it);
    } else if (it.id) {
      dlBtn.disabled = true;
      try {
        const qs = new URLSearchParams({ id: it.id, xsec_token: it.xsec_token || "", xsec_source: "pc_user" });
        const r = await api(`/api/discover/xhs/note?${qs.toString()}`);
        if (r.ok && r.item) downloadSingleFromDiscover(r.item);
        else showToast(r.message || "获取作品详情失败");
      } finally {
        dlBtn.disabled = false;
      }
    }
  }
});
