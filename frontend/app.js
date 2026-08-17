/* Movie & Book Tracker — Person C (Frontend)
 *
 * Talks to the API contract agreed in Step 4 of the project plan:
 *   GET    /items
 *   POST   /items
 *   PATCH  /items/{id}
 *   DELETE /items/{id}
 *   GET    /fetch-info?title=...&type=...
 *
 * Everything backend-specific lives in the config block below,
 * so integration is a one-place change.
 */

// ---------------------------------------------------------------- config
const API_BASE = "http://127.0.0.1:8000";

// Values must match models.Item.status in the backend exactly.
const STATUSES = [
  { value: "want_to_watch", label: "Want to watch/read" },
  { value: "watching",      label: "In progress" },
  { value: "finished",      label: "Finished" },
];

const DEFAULT_STATUS = "want_to_watch";

// ---------------------------------------------------------------- api layer
async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(API_BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new Error("Cannot reach the server. Is the backend running on " + API_BASE + "?");
  }

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Request failed (${response.status}): ${body.slice(0, 150)}`);
  }

  return response.status === 204 ? null : response.json();
}

const api = {
  list:      ()          => request("/items"),
  create:    (item)      => request("/items", { method: "POST", body: JSON.stringify(item) }),
  update:    (id, patch) => request(`/items/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  remove:    (id)        => request(`/items/${id}`, { method: "DELETE" }),
  fetchInfo: (title, type) =>
    request(`/fetch-info?title=${encodeURIComponent(title)}&type=${encodeURIComponent(type)}`),
};

// ---------------------------------------------------------------- state
let items = [];
const filters = { status: "all", type: "all", search: "" };
let sortBy = "newest";

// ---------------------------------------------------------------- elements
const el = {
  form:        document.getElementById("add-form"),
  titleInput:  document.getElementById("title-input"),
  typeInput:   document.getElementById("type-input"),
  addBtn:      document.getElementById("add-btn"),
  banner:      document.getElementById("banner"),
  statusPills: document.getElementById("status-filters"),
  search:      document.getElementById("search-input"),
  typeFilter:  document.getElementById("type-filter"),
  sortSelect:  document.getElementById("sort-select"),
  list:        document.getElementById("list"),
  empty:       document.getElementById("empty-state"),
  count:       document.getElementById("count-summary"),
};

// ---------------------------------------------------------------- helpers
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

let bannerTimer;
function showBanner(message, kind = "error") {
  clearTimeout(bannerTimer);
  el.banner.textContent = message;
  el.banner.className = `banner ${kind}`;
  el.banner.hidden = false;
  bannerTimer = setTimeout(() => { el.banner.hidden = true; }, 6000);
}

// ---------------------------------------------------------------- filtering & sorting
function visibleItems() {
  const search = filters.search.trim().toLowerCase();

  const filtered = items.filter((item) => {
    if (filters.status !== "all" && item.status !== filters.status) return false;
    if (filters.type !== "all" && item.type !== filters.type) return false;
    if (search && !String(item.title || "").toLowerCase().includes(search)) return false;
    return true;
  });

  const comparators = {
    // The items table has no created_at column, so the auto-increment id
    // is what tells us insertion order.
    newest: (a, b) => (Number(b.id) || 0) - (Number(a.id) || 0),
    title:  (a, b) => String(a.title || "").localeCompare(String(b.title || "")),
    rating: (a, b) => (b.rating || 0) - (a.rating || 0),
    year:   (a, b) => (Number(b.year) || 0) - (Number(a.year) || 0),
  };

  return filtered.sort(comparators[sortBy]);
}

// ---------------------------------------------------------------- rendering
function renderStatusFilters() {
  const options = [{ value: "all", label: "All" }, ...STATUSES];

  el.statusPills.innerHTML = options.map((option) => {
    const count = option.value === "all"
      ? items.length
      : items.filter((item) => item.status === option.value).length;

    return `<button type="button" class="pill" data-status="${option.value}"
              aria-pressed="${filters.status === option.value}">
              ${escapeHtml(option.label)}<span class="badge">${count}</span>
            </button>`;
  }).join("");
}

function renderPoster(item) {
  const fallback = item.type === "book" ? "📚" : "🎬";
  if (item.poster_url) {
    return `<img class="poster" src="${escapeHtml(item.poster_url)}"
              data-fallback="${fallback}" alt="Cover of ${escapeHtml(item.title)}" />`;
  }
  return `<div class="poster-fallback" aria-hidden="true">${fallback}</div>`;
}

function renderStars(item) {
  if (item.status !== "finished") return "";

  const stars = [1, 2, 3, 4, 5].map((value) =>
    `<button type="button" class="star ${value <= (item.rating || 0) ? "on" : ""}"
       data-rating="${value}" aria-label="Rate ${value} of 5">★</button>`
  ).join("");

  return `<div class="stars" role="group" aria-label="Rating">${stars}</div>`;
}

function renderItem(item) {
  const meta = [item.year, item.genre].filter(Boolean).map(escapeHtml).join(" · ");

  const statusOptions = STATUSES.map((status) =>
    `<option value="${status.value}" ${status.value === item.status ? "selected" : ""}>
       ${escapeHtml(status.label)}
     </option>`
  ).join("");

  return `
    <article class="item" data-id="${escapeHtml(item.id)}">
      ${renderPoster(item)}
      <div class="item-body">
        <h3 class="item-title">${escapeHtml(item.title)}</h3>
        ${meta ? `<p class="item-meta">${meta}</p>` : ""}
        ${item.description ? `<p class="item-desc">${escapeHtml(item.description)}</p>` : ""}
        <div class="item-actions">
          <select class="status-select" aria-label="Status for ${escapeHtml(item.title)}">
            ${statusOptions}
          </select>
          ${renderStars(item)}
          <button type="button" class="btn link delete-btn">Delete</button>
        </div>
      </div>
    </article>`;
}

function render() {
  renderStatusFilters();

  const visible = visibleItems();
  el.list.innerHTML = visible.map(renderItem).join("");

  // Swap in a placeholder if a poster URL is dead (common with OMDb "N/A").
  el.list.querySelectorAll(".poster").forEach((img) => {
    img.addEventListener("error", () => {
      img.outerHTML = `<div class="poster-fallback" aria-hidden="true">${img.dataset.fallback}</div>`;
    }, { once: true });
  });

  el.empty.hidden = visible.length > 0;
  el.empty.textContent = items.length === 0
    ? "Nothing here yet — add your first movie or book above."
    : "No items match these filters.";

  el.count.textContent = items.length === 0
    ? ""
    : `Showing ${visible.length} of ${items.length} item${items.length === 1 ? "" : "s"}.`;
}

// ---------------------------------------------------------------- actions
async function loadItems() {
  try {
    const data = await api.list();
    items = Array.isArray(data) ? data : [];
    render();
  } catch (error) {
    showBanner(error.message);
  }
}

async function addItem(event) {
  event.preventDefault();

  const title = el.titleInput.value.trim();
  const type = el.typeInput.value;
  if (!title) return;

  el.addBtn.disabled = true;
  el.addBtn.textContent = "Adding…";

  try {
    // Ask Person B's endpoint for the details; the item is still added if that fails.
    let details = {};
    try {
      details = (await api.fetchInfo(title, type)) || {};
    } catch {
      showBanner(`Couldn't fetch details for "${title}" — added with the title only.`, "warning");
    }

    const created = await api.create({
      title: details.title || title,
      type,
      poster_url: details.poster_url || null,
      description: details.description || null,
      year: details.year || null,
      genre: details.genre || null,
      status: DEFAULT_STATUS,
    });

    items.push(created);
    render();
    el.form.reset();
    el.titleInput.focus();
  } catch (error) {
    showBanner(error.message);
  } finally {
    el.addBtn.disabled = false;
    el.addBtn.textContent = "Add";
  }
}

async function patchItem(id, patch) {
  try {
    const updated = await api.update(id, patch);
    const index = items.findIndex((item) => String(item.id) === String(id));
    if (index !== -1) items[index] = updated || { ...items[index], ...patch };
    render();
  } catch (error) {
    showBanner(error.message);
    loadItems(); // resync so the UI never disagrees with the server
  }
}

async function deleteItem(id, title) {
  if (!confirm(`Remove "${title}" from your list?`)) return;

  try {
    await api.remove(id);
    items = items.filter((item) => String(item.id) !== String(id));
    render();
  } catch (error) {
    showBanner(error.message);
  }
}

// ---------------------------------------------------------------- events
el.form.addEventListener("submit", addItem);

el.statusPills.addEventListener("click", (event) => {
  const pill = event.target.closest(".pill");
  if (!pill) return;
  filters.status = pill.dataset.status;
  render();
});

el.search.addEventListener("input", (event) => {
  filters.search = event.target.value;
  render();
});

el.typeFilter.addEventListener("change", (event) => {
  filters.type = event.target.value;
  render();
});

el.sortSelect.addEventListener("change", (event) => {
  sortBy = event.target.value;
  render();
});

// One delegated handler for every card: rating and delete.
el.list.addEventListener("click", (event) => {
  const card = event.target.closest(".item");
  if (!card) return;

  const star = event.target.closest(".star");
  if (star) {
    patchItem(card.dataset.id, { rating: Number(star.dataset.rating) });
    return;
  }

  if (event.target.closest(".delete-btn")) {
    deleteItem(card.dataset.id, card.querySelector(".item-title").textContent.trim());
  }
});

el.list.addEventListener("change", (event) => {
  const select = event.target.closest(".status-select");
  if (!select) return;
  patchItem(event.target.closest(".item").dataset.id, { status: select.value });
});

// ---------------------------------------------------------------- start
loadItems();
