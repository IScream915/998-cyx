import * as components from "./shared/components.js";
import {
  NAV_ITEMS,
  closeMobileMenu,
  setActiveNav,
  setPageHeader,
  setPageStyle,
  setupLayout,
} from "./shared/layout.js";

const PAGE_REGISTRY = {
  fullflow: {
    loader: () => import("./pages/fullflow/page.js"),
    stylePath: "./pages/fullflow/page.css",
  },
  "module-b": {
    loader: () => import("./pages/module-b/page.js"),
    stylePath: "./pages/module-b/page.css",
  },
  "module-c": {
    loader: () => import("./pages/module-c/page.js"),
    stylePath: "./pages/module-c/page.css",
  },
  "module-d": {
    loader: () => import("./pages/module-d/page.js"),
    stylePath: "./pages/module-d/page.css",
  },
  "module-e": {
    loader: () => import("./pages/module-e/page.js"),
    stylePath: "./pages/module-e/page.css",
  },
};

const pageRoot = document.getElementById("page-root");
if (!pageRoot) {
  throw new Error("page-root 节点不存在");
}

let activeCleanup = null;
let renderTicket = 0;

function normalizeRoute(raw) {
  const cleaned = raw.replace(/^#\/?/, "").trim();
  return PAGE_REGISTRY[cleaned] ? cleaned : "fullflow";
}

function getRouteMeta(routeKey) {
  return NAV_ITEMS.find((item) => item.key === routeKey) ?? NAV_ITEMS[0];
}

function cleanupActivePage() {
  if (typeof activeCleanup === "function") {
    activeCleanup();
  }
  activeCleanup = null;
}

async function renderRoute(routeKey) {
  const currentTicket = ++renderTicket;
  const pageDef = PAGE_REGISTRY[routeKey] ?? PAGE_REGISTRY.fullflow;

  setActiveNav(routeKey);
  setPageHeader(getRouteMeta(routeKey));
  setPageStyle(pageDef.stylePath);

  cleanupActivePage();
  pageRoot.innerHTML = '<div class="page-loading">页面加载中...</div>';

  try {
    const pageModule = await pageDef.loader();
    if (currentTicket !== renderTicket) {
      return;
    }

    components.clearNode(pageRoot);

    const mounted = pageModule.mount(pageRoot, { components, routeKey });
    if (typeof mounted === "function") {
      activeCleanup = mounted;
    } else if (mounted && typeof mounted.unmount === "function") {
      activeCleanup = () => mounted.unmount();
    } else {
      activeCleanup = null;
    }
  } catch (error) {
    console.error(error);
    pageRoot.innerHTML = '<div class="page-error">页面加载失败，请检查控制台错误信息。</div>';
  }
}

function navigateTo(routeKey) {
  const hash = `#/${routeKey}`;
  if (window.location.hash === hash) {
    renderRoute(routeKey);
    return;
  }
  window.location.hash = hash;
}

setupLayout(navigateTo);

window.addEventListener("hashchange", () => {
  const route = normalizeRoute(window.location.hash);
  renderRoute(route);
  closeMobileMenu();
});

window.addEventListener("beforeunload", cleanupActivePage);

if (!window.location.hash) {
  navigateTo("fullflow");
} else {
  renderRoute(normalizeRoute(window.location.hash));
}
