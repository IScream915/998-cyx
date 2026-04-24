export const NAV_ITEMS = [
  {
    key: "fullflow",
    label: "Full Pipeline",
    desc: "Coordinated demo",
    title: "Full Pipeline Demo",
    subtitle: "",
  },
  {
    key: "module-b",
    label: "Module B",
    desc: "Scene classification and heatmap",
    title: "Module B Demo",
    subtitle: "",
  },
  {
    key: "module-c",
    label: "Module C",
    desc: "Live tracking overlay",
    title: "Module C Demo",
    subtitle: "Live dual-view blind-spot tracking and alerts",
  },
  {
    key: "module-d",
    label: "Module D",
    desc: "Local sequence and detection stats",
    title: "Module D Demo",
    subtitle: "",
  },
  {
    key: "module-e",
    label: "Module E",
    desc: "Simulation trigger and arbitration",
    title: "Module E Demo",
    subtitle: "Trigger the moduleE reminder flow with simulated upstream input",
  },
];

const BREAKPOINT = 960;

function renderNav(navRoot, onNavigate) {
  navRoot.innerHTML = "";

  for (const item of NAV_ITEMS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-item";
    button.dataset.route = item.key;

    const label = document.createElement("span");
    label.className = "nav-label";
    label.textContent = item.label;

    const desc = document.createElement("p");
    desc.className = "nav-desc";
    desc.textContent = item.desc;

    button.appendChild(label);
    button.appendChild(desc);
    button.addEventListener("click", () => onNavigate(item.key));
    navRoot.appendChild(button);
  }
}

function openMobileMenu() {
  document.body.classList.add("mobile-menu-open");
}

export function closeMobileMenu() {
  document.body.classList.remove("mobile-menu-open");
}

export function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
}

export function setActiveNav(routeKey) {
  const navItems = document.querySelectorAll(".nav-item");
  navItems.forEach((item) => {
    item.classList.toggle("is-active", item.dataset.route === routeKey);
  });
}

export function setPageHeader(meta) {
  const titleNode = document.getElementById("page-title");
  const subtitleNode = document.getElementById("page-subtitle");
  if (titleNode) {
    titleNode.textContent = meta?.title ?? "";
  }
  if (subtitleNode) {
    subtitleNode.textContent = meta?.subtitle ?? "";
  }
}

let currentPageStyleHref = "";

export function setPageStyle(href) {
  if (!href || href === currentPageStyleHref) {
    return;
  }

  const old = document.getElementById("page-style-link");
  if (old) {
    old.remove();
  }

  const link = document.createElement("link");
  link.id = "page-style-link";
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);

  currentPageStyleHref = href;
}

export function setupLayout(onNavigate) {
  const navRoot = document.getElementById("sidebar-nav");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const mobileMenuBtn = document.getElementById("mobile-menu-btn");
  const overlay = document.getElementById("mobile-overlay");

  if (!navRoot || !sidebarToggle || !mobileMenuBtn || !overlay) {
    throw new Error("Page shell nodes are missing; layout cannot be initialized");
  }

  renderNav(navRoot, onNavigate);

  sidebarToggle.addEventListener("click", () => {
    const next = !document.body.classList.contains("sidebar-collapsed");
    setSidebarCollapsed(next);
  });

  mobileMenuBtn.addEventListener("click", openMobileMenu);
  overlay.addEventListener("click", closeMobileMenu);

  window.addEventListener("resize", () => {
    if (window.innerWidth > BREAKPOINT) {
      closeMobileMenu();
    }
  });
}
