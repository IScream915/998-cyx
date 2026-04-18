export function mount(container) {
  container.innerHTML = `
    <section class="placeholder-page">
      <article class="card">
        <div class="card-body">
          <h3 class="placeholder-title">模块C展示页（预留）</h3>
          <p class="placeholder-desc">
            该页面用于展示模块C的独立处理效果。当前版本仅提供占位结构与接口说明，后续可接入检测目标可视化、统计指标与时序回放。
          </p>
        </div>
      </article>
      <section class="placeholder-grid">
        <article class="placeholder-item">
          <h4>输入区预留</h4>
          <p>预留视频源选择与帧级控制。</p>
        </article>
        <article class="placeholder-item">
          <h4>处理可视化预留</h4>
          <p>预留模块C中间特征与结果叠加展示。</p>
        </article>
        <article class="placeholder-item">
          <h4>输出日志预留</h4>
          <p>预留关键事件与异常日志时间线。</p>
        </article>
      </section>
    </section>
  `;

  return () => {};
}
