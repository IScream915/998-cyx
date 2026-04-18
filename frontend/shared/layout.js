export const NAV_ITEMS = [
  {
    key: "fullflow",
    label: "全流程展示",
    desc: "A/B/CD/E 协同演示",
    title: "全流程展示",
    subtitle: "选择场景并播放帧序列，查看各模块输出与语音提示",
  },
  {
    key: "module-b",
    label: "模块B展示",
    desc: "场景分类与热力图预留",
    title: "模块B展示",
    subtitle: "左侧原始场景，右侧热力图占位窗口",
  },
  {
    key: "module-c",
    label: "模块C展示",
    desc: "检测模块预留页面",
    title: "模块C展示",
    subtitle: "当前为占位页，等待接入模块C能力",
  },
  {
    key: "module-d",
    label: "模块D展示",
    desc: "决策模块预留页面",
    title: "模块D展示",
    subtitle: "当前为占位页，等待接入模块D能力",
  },
  {
    key: "module-e",
    label: "模块E展示",
    desc: "提醒模块预留页面",
    title: "模块E展示",
    subtitle: "当前为占位页，等待接入模块E能力",
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
    throw new Error("页面骨架节点缺失，无法初始化布局");
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
