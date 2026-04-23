# -*- coding: utf-8 -*-
import time
import threading
import queue
import re
import numpy as np
from dataclasses import dataclass, field
from typing import Any, List, Dict, Optional, Tuple
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

    def clear_queue(self):
        self._clear_queue()

    def queue_size(self) -> int:
        return self.audio_queue.qsize()
                
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
        self.limit_event_by_value = self._build_limit_event_index()
        
        self.cooldown_tracker = {}
        self.last_processed_ocr = "" 
        self._state_lock = threading.Lock()
        self.last_decision: Dict[str, Any] = {
            "decision_code": "NO_SIGN",
            "speak": False,
            "priority": None,
            "voice_prompt": "",
            "match_source": None,
            "matched_event_id": None,
            "reason": "init",
            "ts": time.time(),
        }

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

    def _build_limit_event_index(self) -> Dict[int, TrafficSignEvent]:
        """构建限速规则的数值索引：20 -> limit_20"""
        index: Dict[int, TrafficSignEvent] = {}
        warned_values = set()
        for event in self.knowledge_base:
            if event.category != "LIMIT":
                continue
            nums = re.findall(r'\d+', event.standard_text)
            if not nums:
                continue
            value = int(nums[0])
            if value in index:
                if value not in warned_values:
                    warned_values.add(value)
                    print(f">> [Warn] 限速值 {value} 在知识库中重复，保留首条: {index[value].event_id}")
                continue
            index[value] = event
        return index

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_non_negative_int(value, default: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    def _evaluate_density_risk(self, scene: str, num_pedestrians: int, num_vehicles: int) -> str:
        """按场景阈值评估交通密度风险等级：HIGH/MEDIUM/LOW。"""
        scene_key = str(scene or "unknown").strip().lower()

        if scene_key in ("parking lot", "parking_lot"):
            if num_pedestrians >= 1 or num_vehicles >= 6:
                return "HIGH"
            if num_vehicles >= 3:
                return "MEDIUM"
            return "LOW"

        if scene_key in ("city street", "city_street", "street", "residential"):
            if num_pedestrians >= 3 or num_vehicles >= 10 or (num_pedestrians >= 2 and num_vehicles >= 6):
                return "HIGH"
            if num_pedestrians >= 1 or num_vehicles >= 5:
                return "MEDIUM"
            return "LOW"

        if scene_key in ("highway", "tunnel"):
            if num_pedestrians >= 1 or num_vehicles >= 14:
                return "HIGH"
            if num_vehicles >= 8:
                return "MEDIUM"
            return "LOW"

        if num_pedestrians >= 2 or num_vehicles >= 10:
            return "HIGH"
        if num_pedestrians >= 1 or num_vehicles >= 6:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _build_density_suffix(risk_level: str) -> str:
        if risk_level == "HIGH":
            return "前方交通密度高，请立即减速并保持安全车距"
        if risk_level == "MEDIUM":
            return "前方交通较繁忙，请谨慎驾驶"
        return ""

    def _pick_sign_text(self, detected_signs: List[Dict]) -> Tuple[str, str, float]:
        """
        从多标志中选择最高置信度文本。
        返回: (原始文本, 清洗文本, 置信度)
        """
        best_text = ""
        best_conf = -1.0
        for item in detected_signs:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            if not isinstance(content, str):
                continue
            raw_text = content.strip()
            if not raw_text:
                continue
            conf = self._to_float(item.get("confidence"), 0.0)
            if conf > best_conf:
                best_conf = conf
                best_text = raw_text

        if not best_text:
            return "", "", 0.0

        clean_text = re.sub(r'[^\w\u4e00-\u9fa5]+', '', best_text)
        return best_text, clean_text, best_conf

    def _hard_match_limit_event(self, raw_text: str, clean_text: str) -> Optional[TrafficSignEvent]:
        """
        限速硬匹配：
        仅当文本包含“限速”或“speedlimit”语义时，按数字直接匹配 LIMIT 规则。
        """
        if not clean_text:
            return None
        lowered = clean_text.lower()
        is_limit_text = ("限速" in clean_text) or ("speedlimit" in lowered)
        if not is_limit_text:
            return None

        nums = re.findall(r'\d+', clean_text)
        if not nums:
            return None

        limit_val = int(nums[0])
        return self.limit_event_by_value.get(limit_val)

    def _semantic_match_event(self, clean_text: str) -> Tuple[Optional[TrafficSignEvent], float]:
        """
        语义兜底匹配：保持原有 embedding + cosine 阈值逻辑。
        """
        if not clean_text:
            return None, 0.0

        input_emb = self.encoder.encode([clean_text])[0]
        scores = 1 - np.array([cosine(input_emb, kb_emb) for kb_emb in self.kb_embeddings])
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        if best_score < 0.60:
            return None, best_score
        return self.knowledge_base[best_idx], best_score

    def update_telematics(self, data: dict):
        self.blackboard.latest_telematics = data
        self.blackboard.last_telematics_time = time.time()

    def update_perception(self, perception_json: dict) -> Dict[str, Any]:
        """输入接口：接收前端 JSON 并触发仲裁"""
        self.blackboard.latest_perception_json = perception_json
        self.blackboard.last_json_time = time.time()
        decision = self._evaluate()
        self._store_decision(decision)
        return decision

    def _build_decision(
        self,
        *,
        code: str,
        speak: bool,
        priority: Optional[int],
        voice_prompt: str = "",
        match_source: Optional[str] = None,
        matched_event_id: Optional[str] = None,
        reason: str = "",
        semantic_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "decision_code": code,
            "speak": bool(speak),
            "priority": priority,
            "voice_prompt": voice_prompt,
            "match_source": match_source,
            "matched_event_id": matched_event_id,
            "reason": reason,
            "ts": time.time(),
        }
        if semantic_score is not None:
            payload["semantic_score"] = float(semantic_score)
        return payload

    def _store_decision(self, decision: Dict[str, Any]) -> None:
        with self._state_lock:
            self.last_decision = dict(decision)

    def _evaluate(self) -> Dict[str, Any]:
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
            voice_prompt = "🚨 警报！盲区发现高危目标，立即避让！"
            if self._can_trigger("P0_BLIND_SPOT", current_time, cooldown=3.0):
                self._dispatch_alert(voice_prompt, priority=0)
                return self._build_decision(
                    code="P0_BLIND_SPOT",
                    speak=True,
                    priority=0,
                    voice_prompt=voice_prompt,
                    reason="blind_spot_high_risk",
                )
            return self._build_decision(
                code="COOLDOWN_BLOCK",
                speak=False,
                priority=0,
                voice_prompt=voice_prompt,
                reason="p0_cooldown_blocked",
            )

        # ----------------------------------------------------
        # 仲裁级 2：NLP 路牌解析与状态融合 (Neuro-Symbolic)
        # ----------------------------------------------------
        detected_signs = perception.get("detected_signs", [])
        if not detected_signs:
            return self._build_decision(
                code="NO_SIGN",
                speak=False,
                priority=None,
                reason="detected_signs_empty",
            )

        raw_text, clean_text, chosen_conf = self._pick_sign_text(detected_signs)
        if not clean_text:
            return self._build_decision(
                code="NO_SIGN",
                speak=False,
                priority=None,
                reason="no_valid_sign_text",
            )

        # 性能优化：相同的字符串在短时间内不重复过大模型计算
        with self._state_lock:
            if clean_text == self.last_processed_ocr:
                return self._build_decision(
                    code="COOLDOWN_BLOCK",
                    speak=False,
                    priority=None,
                    reason="ocr_duplicate_blocked",
                )
            self.last_processed_ocr = clean_text

        # Step1: 数字硬匹配（仅限速类）
        matched_event = self._hard_match_limit_event(raw_text, clean_text)
        match_source = "hard_match"
        semantic_score = None

        # Step2: 语义兜底
        if matched_event is None:
            matched_event, semantic_score = self._semantic_match_event(clean_text)
            if matched_event is None:
                return self._build_decision(
                    code="NO_MATCH",
                    speak=False,
                    priority=None,
                    reason="semantic_score_too_low",
                    semantic_score=semantic_score,
                )
            match_source = "semantic_fallback"

        current_scene = perception.get("scene", "unknown")
        num_pedestrians = self._to_non_negative_int(perception.get("num_pedestrians"), 0)
        num_vehicles = self._to_non_negative_int(perception.get("num_vehicles"), 0)

        # 对历史调用方保持兼容：若未透传 num_*，回退数组长度
        if "num_pedestrians" not in perception:
            pedestrians = perception.get("pedestrians")
            if isinstance(pedestrians, list):
                num_pedestrians = len(pedestrians)
        if "num_vehicles" not in perception:
            vehicles = perception.get("vehicles")
            if isinstance(vehicles, list):
                num_vehicles = len(vehicles)

        # 【符号计算】：逻辑门控与校验
        # 校验 1：场景合法性 (有些路牌在特定场景无效)
        if current_scene not in matched_event.applicable_scenes and current_scene != "unknown":
            return self._build_decision(
                code="NO_MATCH",
                speak=False,
                priority=None,
                match_source=match_source,
                matched_event_id=matched_event.event_id,
                reason=f"scene_not_applicable:{current_scene}",
                semantic_score=semantic_score,
            )

        if semantic_score is None:
            print(
                f"[{time.strftime('%H:%M:%S')}] 🔎 匹配命中 source={match_source}, "
                f"event_id={matched_event.event_id}, sign='{raw_text}', conf={chosen_conf:.4f}"
            )
        else:
            print(
                f"[{time.strftime('%H:%M:%S')}] 🔎 匹配命中 source={match_source}, "
                f"event_id={matched_event.event_id}, score={semantic_score:.4f}, "
                f"sign='{raw_text}', conf={chosen_conf:.4f}"
            )

        # 校验 2：状态耦合 (车速判断)
        if matched_event.category == "LIMIT":
            limit_val = int(re.findall(r'\d+', matched_event.standard_text)[0])
            if current_speed > (limit_val + 5):
                # P1 级：违规超速
                tts_content = f"您已超速，当前限速{limit_val}公里"
                density_level = self._evaluate_density_risk(current_scene, num_pedestrians, num_vehicles)
                density_suffix = self._build_density_suffix(density_level)
                if density_suffix:
                    tts_content = f"{tts_content}，{density_suffix}"
                print(
                    f"[{time.strftime('%H:%M:%S')}] 📊 密度评估 level={density_level}, "
                    f"scene={current_scene}, ped={num_pedestrians}, veh={num_vehicles}"
                )
                if self._can_trigger(f"P1_SPEED_{limit_val}", current_time, cooldown=5.0):
                    self._dispatch_alert(tts_content, priority=1)
                    return self._build_decision(
                        code="P1_SPEED",
                        speak=True,
                        priority=1,
                        voice_prompt=tts_content,
                        match_source=match_source,
                        matched_event_id=matched_event.event_id,
                        reason="overspeed_triggered",
                        semantic_score=semantic_score,
                    )
                return self._build_decision(
                    code="COOLDOWN_BLOCK",
                    speak=False,
                    priority=1,
                    voice_prompt=tts_content,
                    match_source=match_source,
                    matched_event_id=matched_event.event_id,
                    reason="p1_cooldown_blocked",
                    semantic_score=semantic_score,
                )
            else:
                # P3 级：仅 UI 静默提醒
                print(f"[{time.strftime('%H:%M:%S')}] 🖥️ UI 更新 -> 发现标志: {matched_event.standard_text} (未超速，静默处理)")
                return self._build_decision(
                    code="P3_SILENT",
                    speak=False,
                    priority=3,
                    voice_prompt=f"保持当前车速，检测到{matched_event.standard_text}",
                    match_source=match_source,
                    matched_event_id=matched_event.event_id,
                    reason="speed_within_limit",
                    semantic_score=semantic_score,
                )

        elif matched_event.category == "WARN":
            # P2 级：普通预警
            tts_content = matched_event.tts_template
            density_level = self._evaluate_density_risk(current_scene, num_pedestrians, num_vehicles)
            density_suffix = self._build_density_suffix(density_level)
            if density_suffix:
                tts_content = f"{tts_content}，{density_suffix}"
            print(
                f"[{time.strftime('%H:%M:%S')}] 📊 密度评估 level={density_level}, "
                f"scene={current_scene}, ped={num_pedestrians}, veh={num_vehicles}"
            )
            if self._can_trigger(f"P2_WARN_{matched_event.event_id}", current_time, cooldown=10.0):
                self._dispatch_alert(tts_content, priority=2)
                return self._build_decision(
                    code="P2_WARN",
                    speak=True,
                    priority=2,
                    voice_prompt=tts_content,
                    match_source=match_source,
                    matched_event_id=matched_event.event_id,
                    reason="warn_triggered",
                    semantic_score=semantic_score,
                )
            return self._build_decision(
                code="COOLDOWN_BLOCK",
                speak=False,
                priority=2,
                voice_prompt=tts_content,
                match_source=match_source,
                matched_event_id=matched_event.event_id,
                reason="p2_cooldown_blocked",
                semantic_score=semantic_score,
            )

        return self._build_decision(
            code="NO_MATCH",
            speak=False,
            priority=None,
            match_source=match_source,
            matched_event_id=matched_event.event_id,
            reason=f"category_not_supported:{matched_event.category}",
            semantic_score=semantic_score,
        )

    def _can_trigger(self, event_key: str, current_time: float, cooldown: float) -> bool:
        """冷却防抖机制"""
        with self._state_lock:
            last_trigger = self.cooldown_tracker.get(event_key, 0.0)
            if (current_time - last_trigger) >= cooldown:
                self.cooldown_tracker[event_key] = current_time
                return True
            return False

    def _dispatch_alert(self, tts_content: str, priority: int):
        print(f"[{time.strftime('%H:%M:%S')}] 🔊 仲裁下发 [P{priority}] -> {tts_content}")
        self.tts_manager.speak(tts_content, priority=priority)

    def reset_runtime_state(self) -> dict:
        with self._state_lock:
            self.cooldown_tracker.clear()
            self.last_processed_ocr = ""
            self.last_decision = {
                "decision_code": "NO_SIGN",
                "speak": False,
                "priority": None,
                "voice_prompt": "",
                "match_source": None,
                "matched_event_id": None,
                "reason": "manual_reset",
                "ts": time.time(),
            }
        self.tts_manager.clear_queue()
        return {"ok": True, "reset_at": time.time()}

    def get_runtime_state(self) -> dict:
        with self._state_lock:
            cooldown_size = len(self.cooldown_tracker)
            last_processed_ocr = self.last_processed_ocr
            last_decision = dict(self.last_decision)
        return {
            "cooldown_size": cooldown_size,
            "last_processed_ocr": last_processed_ocr,
            "last_decision": last_decision,
            "tts_queue_size": self.tts_manager.queue_size(),
        }

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
