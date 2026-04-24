export const FULLFLOW_SCENARIOS = [
  {
    id: "scene-1",
    name: "Urban Arterial - Midday Peak",
    description: "Mixed traffic and pedestrian flow",
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
        moduleD: {
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
          voice_prompt: "Speed limit 40 km/h ahead. Please slow down.",
        },
        log: [
          "moduleA published frame_id=101",
          "moduleB scene classification: city street (84.0%)",
          "moduleD detected 1 speed-limit sign, 2 pedestrians, and 7 vehicles",
          "moduleE triggered reminder: Speed limit 40 km/h ahead. Please slow down.",
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
        moduleD: {
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
          voice_prompt: "Pedestrian crossing ahead. Yield to pedestrians.",
        },
        log: [
          "moduleA published frame_id=102",
          "moduleB scene classification: city street (79.0%)",
          "moduleD pedestrian density increased and a crossing sign was detected",
          "moduleE triggered reminder: Pedestrian crossing ahead. Yield to pedestrians.",
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
        moduleD: {
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
          voice_prompt: "Maintain current speed and watch the traffic ahead.",
        },
        log: [
          "moduleA published frame_id=103",
          "moduleB scene classification: city street (82.0%)",
          "moduleD detected no new traffic signs",
          "moduleE produced silent advisory: Maintain current speed and watch the traffic ahead.",
        ],
      },
    ],
  },
  {
    id: "scene-2",
    name: "Highway Segment - Cruising",
    description: "Highway cruising with speed-limit reminders",
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
        moduleD: {
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
          voice_prompt: "You are speeding. Current speed limit is 80 km/h.",
        },
        log: [
          "moduleA published frame_id=201",
          "moduleB scene classification: highway (93.0%)",
          "moduleD detected speed limit 80 sign",
          "moduleE triggered overspeed reminder: You are speeding. Current speed limit is 80 km/h.",
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
        moduleD: {
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
          voice_prompt: "No overtaking ahead. Keep a safe following distance.",
        },
        log: [
          "moduleA published frame_id=202",
          "moduleB scene classification: highway (90.0%)",
          "moduleD detected no-overtaking sign",
          "moduleE triggered reminder: No overtaking ahead. Keep a safe following distance.",
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
        moduleD: {
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
          voice_prompt: "Speed is back to normal. Stay focused while driving.",
        },
        log: [
          "moduleA published frame_id=203",
          "moduleB scene classification: highway (88.0%)",
          "moduleD detected no new risk signs",
          "moduleE produced silent advisory: Speed is back to normal.",
        ],
      },
    ],
  },
  {
    id: "scene-3",
    name: "Tunnel Segment - Entry and Exit",
    description: "Lighting changes and dense vehicle flow",
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
        moduleD: {
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
          voice_prompt: "Slow down in the tunnel segment. Current speed limit is 60 km/h.",
        },
        log: [
          "moduleA published frame_id=301",
          "moduleB scene classification: tunnel (89.0%)",
          "moduleD detected speed limit 60 sign with dense traffic",
          "moduleE triggered reminder: Slow down in the tunnel segment. Current speed limit is 60 km/h.",
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
        moduleD: {
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
          voice_prompt: "No honking in the tunnel. Drive smoothly.",
        },
        log: [
          "moduleA published frame_id=302",
          "moduleB scene classification: tunnel (86.0%)",
          "moduleD detected no-honking sign",
          "moduleE triggered reminder: No honking in the tunnel. Drive smoothly.",
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
        moduleD: {
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
          voice_prompt: "Approaching tunnel exit. Watch for lighting changes.",
        },
        log: [
          "moduleA published frame_id=303",
          "moduleB scene classification: tunnel (85.0%)",
          "moduleD detected no new signs in this frame",
          "moduleE produced silent advisory: Approaching tunnel exit. Watch for lighting changes.",
        ],
      },
    ],
  },
];

export function findScenarioIndexById(id) {
  const index = FULLFLOW_SCENARIOS.findIndex((item) => item.id === id);
  return index >= 0 ? index : 0;
}
