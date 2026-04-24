import { FULLFLOW_SCENARIOS } from "../fullflow/data.js";

export const MODULE_B_SCENARIOS = FULLFLOW_SCENARIOS.map((scenario) => ({
  id: scenario.id,
  name: scenario.name,
  description: scenario.description,
  frameIntervalMs: scenario.frameIntervalMs,
  frames: scenario.frames,
  outputs: scenario.timeline.map((entry) => ({
    frameId: entry.frameId,
    scene: entry.moduleB.scene,
    confidence: entry.moduleB.confidence,
    conference: entry.moduleB.conference,
    speed: entry.moduleB.speed,
    log: [
      `moduleB received frame_id=${entry.moduleB.frame_id}`,
      `Scene classification: ${entry.moduleB.scene}`,
      `Confidence: ${(entry.moduleB.confidence * 100).toFixed(1)}%`,
      `Speed estimate: ${entry.moduleB.speed} km/h`,
    ],
  })),
}));
