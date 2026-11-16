const filters = document.querySelector("#filters");
function hideBrokenIcon(image) {
  if (image instanceof HTMLImageElement && image.matches(".tool-mark img")) image.hidden = true;
}

document.addEventListener("error", (event) => hideBrokenIcon(event.target), true);
for (const image of document.querySelectorAll(".tool-mark img")) {
  if (image.complete && !image.naturalWidth) hideBrokenIcon(image);
}

const catalog = document.querySelector("#entries");
const rows = [...catalog.querySelectorAll(".entry")].map((element) => ({
  element,
  categories: JSON.parse(element.dataset.category),
  search: normalize(element.dataset.search),
}));
const filterNames = ["q", "category", "maturity", "source", "sort"];
const resultCount = document.querySelector("#result-count");
const empty = document.querySelector("#empty");
const clear = document.querySelector("#clear");
let currentSort = "";

function normalize(text) {
  return text.normalize("NFKC").toLowerCase();
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  for (const name of filterNames) {
    const control = filters.elements.namedItem(name);
    const value = params.get(name) || "";
    control.value = control.tagName === "SELECT" && ![...control.options].some((option) => option.value === value)
      ? ""
      : value;
  }
}

function sortRows(key) {
  if (key === currentSort) return;
  currentSort = key;
  const ordered = [...rows];
  if (key) {
    ordered.sort((first, second) => {
      const a = first.element.dataset[key];
      const b = second.element.dataset[key];
      if (!a || !b) return Number(!a) - Number(!b);
      return key === "pushed" ? b.localeCompare(a) : Number(b) - Number(a);
    });
  }
  const fragment = document.createDocumentFragment();
  for (const row of ordered) fragment.append(row.element);
  catalog.append(fragment);
}

function applyFilters(writeUrl) {
  const { q, category, maturity, source, sort } = Object.fromEntries(new FormData(filters));
  const terms = normalize(q).trim().split(/\s+/).filter(Boolean);
  let visible = 0;
  for (const { element, categories, search } of rows) {
    const matches = (!category || categories.includes(category))
      && (!maturity || element.dataset.maturity === maturity)
      && (!source || element.dataset.source === source)
      && terms.every((term) => search.includes(term));
    element.hidden = !matches;
    visible += Number(matches);
  }
  sortRows(sort);
  empty.hidden = visible !== 0;
  clear.hidden = !q && !category && !maturity && !source && !sort;
  resultCount.textContent = `${visible.toLocaleString()} ${visible === 1 ? "tool" : "tools"}`;
  if (writeUrl) {
    const url = new URL(location.href);
    for (const [name, value] of Object.entries({ q, category, maturity, source, sort })) {
      if (value) url.searchParams.set(name, value);
      else url.searchParams.delete(name);
    }
    history.replaceState(null, "", url);
  }
}

filters.addEventListener("submit", (event) => event.preventDefault());
filters.addEventListener("input", () => applyFilters(true));
clear.addEventListener("click", () => {
  filters.reset();
  applyFilters(true);
  filters.elements.namedItem("q").focus();
});
window.addEventListener("popstate", () => {
  readUrl();
  applyFilters(false);
});
readUrl();
applyFilters(false);
filters.hidden = false;
