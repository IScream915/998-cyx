export const FULLFLOW_SCENARIOS = [
  {
    id: "scene-1",
    name: "城市主干道 · 午高峰",
    description: "多车流与行人混行场景",
    frameIntervalMs: 1300,
    frames: [
      { id: 101, src: "./assets/scenes/scene-1/frame-1.jpg", ts: "00:00:01" },
      { id: 102, src: "./assets/scenes/scene-1/frame-2.jpg", ts: "00:00:02" },
      { id: 103, src: "./assets/scenes/scene-1/frame-3.jpg", ts: "00:00:03" },
    ],
    timeline: [
      {
        frameId: 101,
        moduleA: {
          frame_id: 101,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 101,
          scene: "city street",
          confidence: 0.84,
          conference: 0.84,
          speed: 46,
        },
        moduleC: {
          frame_id: 101,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "Speed Limit 40 km/h", confidence: 0.91 },
          ],
          num_pedestrians: 2,
          num_vehicles: 7,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 101,
          status: "processed",
          alert_level: "P2",
          voice_prompt: "前方限速40公里，请减速慢行",
        },
        log: [
          "moduleA 发布 frame_id=101",
          "moduleB 场景识别: city street (84.0%)",
          "moduleC 检测到限速标志 1 个，行人 2 个，车辆 7 个",
          "moduleE 触发提醒: 前方限速40公里，请减速慢行",
        ],
      },
      {
        frameId: 102,
        moduleA: {
          frame_id: 102,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 102,
          scene: "city street",
          confidence: 0.79,
          conference: 0.79,
          speed: 48,
        },
        moduleC: {
          frame_id: 102,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "Pedestrian Crossing", confidence: 0.88 },
          ],
          num_pedestrians: 4,
          num_vehicles: 6,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 102,
          status: "processed",
          alert_level: "P2",
          voice_prompt: "前方人行横道，请礼让行人",
        },
        log: [
          "moduleA 发布 frame_id=102",
          "moduleB 场景识别: city street (79.0%)",
          "moduleC 行人密度上升，检测到人行横道标志",
          "moduleE 触发提醒: 前方人行横道，请礼让行人",
        ],
      },
      {
        frameId: 103,
        moduleA: {
          frame_id: 103,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 103,
          scene: "city street",
          confidence: 0.82,
          conference: 0.82,
          speed: 41,
        },
        moduleC: {
          frame_id: 103,
          num_traffic_signs: 0,
          traffic_signs: [],
          num_pedestrians: 1,
          num_vehicles: 5,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 103,
          status: "processed",
          alert_level: "P3",
          voice_prompt: "保持当前车速，注意前方车流",
        },
        log: [
          "moduleA 发布 frame_id=103",
          "moduleB 场景识别: city street (82.0%)",
          "moduleC 未检测到新交通标志",
          "moduleE 输出静默建议: 保持当前车速，注意前方车流",
        ],
      },
    ],
  },
  {
    id: "scene-2",
    name: "高速路段 · 巡航",
    description: "高速巡航与限速提醒场景",
    frameIntervalMs: 1400,
    frames: [
      { id: 201, src: "./assets/scenes/scene-2/frame-1.jpg", ts: "00:00:01" },
      { id: 202, src: "./assets/scenes/scene-2/frame-2.jpg", ts: "00:00:02" },
      { id: 203, src: "./assets/scenes/scene-2/frame-3.jpg", ts: "00:00:03" },
    ],
    timeline: [
      {
        frameId: 201,
        moduleA: {
          frame_id: 201,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 201,
          scene: "highway",
          confidence: 0.93,
          conference: 0.93,
          speed: 98,
        },
        moduleC: {
          frame_id: 201,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "Speed Limit 80 km/h", confidence: 0.9 },
          ],
          num_pedestrians: 0,
          num_vehicles: 10,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 201,
          status: "processed",
          alert_level: "P1",
          voice_prompt: "您已超速，当前限速80公里",
        },
        log: [
          "moduleA 发布 frame_id=201",
          "moduleB 场景识别: highway (93.0%)",
          "moduleC 检测到限速 80 标志",
          "moduleE 触发超速提醒: 您已超速，当前限速80公里",
        ],
      },
      {
        frameId: 202,
        moduleA: {
          frame_id: 202,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 202,
          scene: "highway",
          confidence: 0.9,
          conference: 0.9,
          speed: 82,
        },
        moduleC: {
          frame_id: 202,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "No Overtaking", confidence: 0.86 },
          ],
          num_pedestrians: 0,
          num_vehicles: 8,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 202,
          status: "processed",
          alert_level: "P2",
          voice_prompt: "前方禁止超车，请保持车距",
        },
        log: [
          "moduleA 发布 frame_id=202",
          "moduleB 场景识别: highway (90.0%)",
          "moduleC 检测到禁止超车标志",
          "moduleE 触发提醒: 前方禁止超车，请保持车距",
        ],
      },
      {
        frameId: 203,
        moduleA: {
          frame_id: 203,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 203,
          scene: "highway",
          confidence: 0.88,
          conference: 0.88,
          speed: 78,
        },
        moduleC: {
          frame_id: 203,
          num_traffic_signs: 0,
          traffic_signs: [],
          num_pedestrians: 0,
          num_vehicles: 7,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 203,
          status: "processed",
          alert_level: "P3",
          voice_prompt: "车速恢复正常，请继续保持专注驾驶",
        },
        log: [
          "moduleA 发布 frame_id=203",
          "moduleB 场景识别: highway (88.0%)",
          "moduleC 无新增风险标志",
          "moduleE 输出静默建议: 车速恢复正常",
        ],
      },
    ],
  },
  {
    id: "scene-3",
    name: "隧道路段 · 进出隧道",
    description: "亮暗变化与密集车辆场景",
    frameIntervalMs: 1350,
    frames: [
      { id: 301, src: "./assets/scenes/scene-3/frame-1.jpg", ts: "00:00:01" },
      { id: 302, src: "./assets/scenes/scene-3/frame-2.jpg", ts: "00:00:02" },
      { id: 303, src: "./assets/scenes/scene-3/frame-3.jpg", ts: "00:00:03" },
    ],
    timeline: [
      {
        frameId: 301,
        moduleA: {
          frame_id: 301,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 301,
          scene: "tunnel",
          confidence: 0.89,
          conference: 0.89,
          speed: 72,
        },
        moduleC: {
          frame_id: 301,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "Speed Limit 60 km/h", confidence: 0.87 },
          ],
          num_pedestrians: 0,
          num_vehicles: 11,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 301,
          status: "processed",
          alert_level: "P1",
          voice_prompt: "隧道路段请降速，当前限速60公里",
        },
        log: [
          "moduleA 发布 frame_id=301",
          "moduleB 场景识别: tunnel (89.0%)",
          "moduleC 检测到限速60标志，车流较密",
          "moduleE 触发提醒: 隧道路段请降速，当前限速60公里",
        ],
      },
      {
        frameId: 302,
        moduleA: {
          frame_id: 302,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 302,
          scene: "tunnel",
          confidence: 0.86,
          conference: 0.86,
          speed: 65,
        },
        moduleC: {
          frame_id: 302,
          num_traffic_signs: 1,
          traffic_signs: [
            { class_name: "No Honking", confidence: 0.79 },
          ],
          num_pedestrians: 0,
          num_vehicles: 9,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 302,
          status: "processed",
          alert_level: "P2",
          voice_prompt: "隧道内禁止鸣笛，请平稳驾驶",
        },
        log: [
          "moduleA 发布 frame_id=302",
          "moduleB 场景识别: tunnel (86.0%)",
          "moduleC 检测到禁止鸣笛标志",
          "moduleE 触发提醒: 隧道内禁止鸣笛，请平稳驾驶",
        ],
      },
      {
        frameId: 303,
        moduleA: {
          frame_id: 303,
          topic: "Frame",
          image_source: "base64_jpg",
        },
        moduleB: {
          frame_id: 303,
          scene: "tunnel",
          confidence: 0.85,
          conference: 0.85,
          speed: 58,
        },
        moduleC: {
          frame_id: 303,
          num_traffic_signs: 0,
          traffic_signs: [],
          num_pedestrians: 0,
          num_vehicles: 6,
          tracked_pedestrians: false,
        },
        moduleE: {
          frame_id: 303,
          status: "processed",
          alert_level: "P3",
          voice_prompt: "即将驶出隧道，请关注光线变化",
        },
        log: [
          "moduleA 发布 frame_id=303",
          "moduleB 场景识别: tunnel (85.0%)",
          "moduleC 当前帧无新增标志",
          "moduleE 输出静默建议: 即将驶出隧道，请关注光线变化",
        ],
      },
    ],
  },
];

export function findScenarioIndexById(id) {
  const index = FULLFLOW_SCENARIOS.findIndex((item) => item.id === id);
  return index >= 0 ? index : 0;
}
