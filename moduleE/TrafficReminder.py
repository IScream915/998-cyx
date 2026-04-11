# -*- coding: utf-8 -*-
import time
import threading
import queue
import re
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from scipy.spatial.distance import cosine
import logging
import json
import os


try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    raise ImportError("请安装 ‘sentence-transformers’ 以支持语义向量功能")

import pyttsx3

# 屏蔽 comtypes 的日志输出
logging.getLogger('comtypes').setLevel(logging.CRITICAL)

# ==========================================
# 1. 数据结构与知识库 (The Mini-RAG Schema)
# ==========================================
@dataclass
class TrafficSignEvent:
    event_id: str
    standard_text: str         # 锚点：用于高维向量匹配
    category: str              # LIMIT, WARN, MANDATORY, INFO
    applicable_scenes: List[str] # 适用场景 (对应前端 JSON 的 scene)
    tts_template: str          # 语音模板

@dataclass
class GlobalBlackboard:
    """全局状态黑板：用于高低频数据的异步对齐"""
    latest_perception_json: dict = field(default_factory=dict)
    latest_telematics: dict = field(default_factory=dict)
    last_json_time: float = 0.0
    last_telematics_time: float = 0.0

# ==========================================
# 2. 异步语音播报引擎 (Producer-Consumer)
# 为避免延迟，引入生产者-消费者模型和优先队列，让大模型的推理主线程不会因为等待语音播报而阻塞丢失前端数据
# ==========================================
class AsyncTTSManager:
    """异步非阻塞优先级语音播报引擎"""
    def __init__(self):
        self.audio_queue = queue.PriorityQueue()
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="TTS-Worker")
        self.worker_thread.start()

    def _worker_loop(self):
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 170)
            engine.setProperty('volume', 1.0)
            # 尝试设置中文
            for voice in engine.getProperty('voices'):
                if "Chinese" in voice.name or "Huihui" in voice.name:
                    engine.setProperty('voice', voice.id)
                    break
        except Exception as e:
            print(f">> [Error] TTS 引擎初始化失败: {e}")
            return

        while self.is_running:
            try:
                priority, text = self.audio_queue.get(timeout=0.5)
                # 实际执行播报
                engine.say(text)
                engine.runAndWait()
                self.audio_queue.task_done()
            except queue.Empty:
                continue

    def speak(self, text: str, priority: int = 2):
        if priority == 0:
            self._clear_queue() # 触发熔断抢麦
        self.audio_queue.put((priority, text))

    def _clear_queue(self):
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except queue.Empty:
                break
                
    def stop(self):
        self.is_running = False

# ==========================================
# 3. 模拟数据提供者 (Mock Data Providers)
# ==========================================
class VehicleTelematicsProvider:
    """模拟获取车辆 CAN 总线数据"""
    def __init__(self):
        self.speed = 60.0

    def get_current_state(self) -> dict:
        return {"speed": self.speed}

# ==========================================
# 4. 融合决策大脑 (The Neuro-Symbolic Engine)
# ==========================================
class FusionDecisionEngine:
    def __init__(self, model_name='paraphrase-multilingual-MiniLM-L12-v2', kb_path='gb5768_rules.json'):
        print(">> [System] 正在初始化语义引擎与决策模块...")
        self.encoder = SentenceTransformer(model_name)
        self.blackboard = GlobalBlackboard()
        self.tts_manager = AsyncTTSManager()
        
        # 将知识库路径作为参数传入，并在初始化时构建
        self.knowledge_base = self._build_knowledge_base(kb_path)
        
        print(f">> [System] 成功加载知识库: 共 {len(self.knowledge_base)} 条 GB5768 国标规则。")
        print(">> [System] 正在构建高维向量索引 (Embedding)...")
        
        self.kb_texts = [e.standard_text for e in self.knowledge_base]
        self.kb_embeddings = self.encoder.encode(self.kb_texts)
        
        self.cooldown_tracker = {}
        self.last_processed_ocr = "" 

        print(">> [System] 引擎就绪 (Ready).")

    def _build_knowledge_base(self, filepath: str) -> List[TrafficSignEvent]:
        """从 JSON 文件读取并构建知识库"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"找不到知识库文件: {filepath}。请确保 gb5768_rules.json 与脚本在同一目录下。")
            
        kb_list = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_rules = json.load(f)
                
                # 将 JSON 字典解包并实例化为 dataclass
                for rule in raw_rules:
                    event = TrafficSignEvent(
                        event_id=rule['event_id'],
                        standard_text=rule['standard_text'],
                        category=rule['category'],
                        applicable_scenes=rule['applicable_scenes'],
                        tts_template=rule['tts_template']
                    )
                    kb_list.append(event)
        except Exception as e:
            print(f">> [Error] 解析知识库 JSON 失败: {e}")
            
        return kb_list

    def update_telematics(self, data: dict):
        self.blackboard.latest_telematics = data
        self.blackboard.last_telematics_time = time.time()

    def update_perception(self, perception_json: dict):
        """输入接口：接收前端 JSON 并触发仲裁"""
        self.blackboard.latest_perception_json = perception_json
        self.blackboard.last_json_time = time.time()
        self._evaluate()

    def _evaluate(self):
        """神经-符号融合仲裁逻辑"""
        current_time = time.time()
        perception = self.blackboard.latest_perception_json
        telematics = self.blackboard.latest_telematics
        current_speed = telematics.get("speed", 0.0)

        # ----------------------------------------------------
        # 仲裁级 1：P0 最高级物理危险 (熔断所有其他逻辑)
        # ----------------------------------------------------
        tracked_obj = perception.get("tracked_pedestrians", {})
        if tracked_obj.get("risk_level") == "HIGH" and tracked_obj.get("in_blind_spot"):
            if self._can_trigger("P0_BLIND_SPOT", current_time, cooldown=3.0):
                self._dispatch_alert("🚨 警报！盲区发现高危目标，立即避让！", priority=0)
            return # 短路返回，无视后面的路牌

        # ----------------------------------------------------
        # 仲裁级 2：NLP 路牌解析与状态融合 (Neuro-Symbolic)
        # ----------------------------------------------------
        detected_signs = perception.get("detected_signs", [])
        if not detected_signs:
            return

        ocr_text = detected_signs[0].get("content", "")
        # 去噪预处理
        clean_text = re.sub(r'[^\w\u4e00-\u9fa5]+', '', ocr_text)
        
        # 性能优化：相同的字符串在短时间内不重复过大模型计算
        if not clean_text or clean_text == self.last_processed_ocr:
            return
        self.last_processed_ocr = clean_text

        # 【神经计算】：计算与国标库的向量相似度
        input_emb = self.encoder.encode([clean_text])[0]
        scores = 1 - np.array([cosine(input_emb, kb_emb) for kb_emb in self.kb_embeddings])
        best_idx = np.argmax(scores)
        best_score = scores[best_idx]
        
        if best_score < 0.60:
            return # 相似度太低，当做杂音忽略

        matched_event = self.knowledge_base[best_idx]
        current_scene = perception.get("scene", "unknown")

        # 【符号计算】：逻辑门控与校验
        # 校验 1：场景合法性 (有些路牌在特定场景无效)
        if current_scene not in matched_event.applicable_scenes and current_scene != "unknown":
            return 

        # 校验 2：状态耦合 (车速判断)
        if matched_event.category == "LIMIT":
            limit_val = int(re.findall(r'\d+', matched_event.standard_text)[0])
            if current_speed > (limit_val + 5):
                # P1 级：违规超速
                if self._can_trigger(f"P1_SPEED_{limit_val}", current_time, cooldown=5.0):
                    self._dispatch_alert(f"您已超速，当前限速{limit_val}公里", priority=1)
            else:
                # P3 级：仅 UI 静默提醒
                print(f"[{time.strftime('%H:%M:%S')}] 🖥️ UI 更新 -> 发现标志: {matched_event.standard_text} (未超速，静默处理)")
        
        elif matched_event.category == "WARN":
            # P2 级：普通预警
            if self._can_trigger(f"P2_WARN_{matched_event.event_id}", current_time, cooldown=10.0):
                self._dispatch_alert(matched_event.tts_template, priority=2)

    def _can_trigger(self, event_key: str, current_time: float, cooldown: float) -> bool:
        """冷却防抖机制"""
        last_trigger = self.cooldown_tracker.get(event_key, 0.0)
        if (current_time - last_trigger) >= cooldown:
            self.cooldown_tracker[event_key] = current_time
            return True
        return False

    def _dispatch_alert(self, tts_content: str, priority: int):
        print(f"[{time.strftime('%H:%M:%S')}] 🔊 仲裁下发 [P{priority}] -> {tts_content}")
        self.tts_manager.speak(tts_content, priority=priority)

    def shutdown(self):
        self.tts_manager.stop()
        print(">> [System] 引擎已安全关闭。")

# ==========================================
# 5. 演示模拟器 (The Demo Runner)
# ==========================================
if __name__ == "__main__":
    engine = FusionDecisionEngine()
    telemetry = VehicleTelematicsProvider()
    
    print("\n================ 开始实况演示 ================")
    
    # 场景 1：静默处理 (识别到限速，但未超速)
    print("\n[测试场景 1] 识别到限速80，当前车速70 (验证状态融合与克制)")
    telemetry.speed = 70.0
    engine.update_telematics(telemetry.get_current_state())
    engine.update_perception({
        "frame_id": 1, "scene": "highway",
        "detected_signs": [{"content": "限  速 8 0", "confidence": 0.8}]
    })
    time.sleep(2)
    
    # 场景 2：抗噪识别 + 超速预警
    print("\n[测试场景 2] 识别到噪乱字符'前方施二'，并触发普通预警 (验证微型RAG抗噪)")
    engine.update_perception({
        "frame_id": 2, "scene": "street",
        "detected_signs": [{"content": "前方 施 二", "confidence": 0.6}]
    })
    time.sleep(1) # 给系统一点时间说话
    
    # 场景 3：多模态高危熔断 (核心亮点)
    print("\n[测试场景 3] 正在播报施工提醒时，盲区突然出现行人 (验证多模态P0抢麦与熔断)")
    # 模拟前方遇到学校
    engine.update_perception({
        "frame_id": 3, "scene": "street",
        "detected_signs": [{"content": "学校区域", "confidence": 0.9}]
    })
    time.sleep(0.5) # 学校刚刚开始播报0.5秒
    
    # 瞬间突发危险
    print(">> ⚠️ 0.5秒后：雷达突然返回 P0 级危险！")
    engine.update_perception({
        "frame_id": 4, "scene": "street",
        "tracked_pedestrians": {"risk_level": "HIGH", "in_blind_spot": True}
    })
    
    # 等待语音播报完成
    time.sleep(5)
    
    print("\n================ 演示结束 ================")
    engine.shutdown()
