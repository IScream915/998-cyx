"""
TSR-YOLO 全局配置
"""
import os
from pathlib import Path

# ============ TT100K 类别映射 ============

TRAFFIC_SIGN = {
    # ---- Mandatory / Indication (i) ----
    "i2":    {"name": "Pedestrian Crossing"},
    "i2r":   {"name": "Pedestrian Crossing (Right)"},
    "i4":    {"name": "Roundabout"},
    "i4l":   {"name": "Roundabout (Left)"},
    "i5":    {"name": "Motor Vehicle Lane"},
    "io":    {"name": "Other Mandatory Sign"},
    "ip":    {"name": "Pedestrian Zone"},

    # ---- Minimum Speed (il) ----
    "il50":  {"name": "Minimum Speed 50 km/h"},
    "il60":  {"name": "Minimum Speed 60 km/h"},
    "il70":  {"name": "Minimum Speed 70 km/h"},
    "il80":  {"name": "Minimum Speed 80 km/h"},
    "il90":  {"name": "Minimum Speed 90 km/h"},
    "il100": {"name": "Minimum Speed 100 km/h"},
    "il110": {"name": "Minimum Speed 110 km/h"},

    # ---- Speed Limit (pl) ----
    "pl5":   {"name": "Speed Limit 5 km/h"},
    "pl20":  {"name": "Speed Limit 20 km/h"},
    "pl30":  {"name": "Speed Limit 30 km/h"},
    "pl40":  {"name": "Speed Limit 40 km/h"},
    "pl50":  {"name": "Speed Limit 50 km/h"},
    "pl60":  {"name": "Speed Limit 60 km/h"},
    "pl70":  {"name": "Speed Limit 70 km/h"},
    "pl80":  {"name": "Speed Limit 80 km/h"},
    "pl100": {"name": "Speed Limit 100 km/h"},
    "pl110": {"name": "Speed Limit 110 km/h"},
    "pl120": {"name": "Speed Limit 120 km/h"},

    # ---- Prohibitory (p) ----
    "p1":    {"name": "No Straight Through"},
    "p2":    {"name": "No Left Turn"},
    "p3":    {"name": "No Right Turn"},
    "p4":    {"name": "No Straight or Left"},
    "p5":    {"name": "No Straight or Right"},
    "p6":    {"name": "No Left or Right Turn"},
    "p7":    {"name": "No Parking (Temporary OK)"},
    "p8":    {"name": "No Stopping / No Parking"},
    "p9":    {"name": "No Motor Vehicles"},
    "p10":   {"name": "No Trucks"},
    "p11":   {"name": "No Entry (Certain Time)"},
    "p12":   {"name": "No Honking"},
    "p13":   {"name": "No Motor Tricycles"},
    "p14":   {"name": "No Non-Motor Vehicles"},
    "p15":   {"name": "No Pedestrians"},
    "p16":   {"name": "No Animal-Drawn Vehicles"},
    "p17":   {"name": "No Trailers"},
    "p18":   {"name": "No Motorcycles"},
    "p19":   {"name": "No Hazardous Materials"},
    "p20":   {"name": "No Tractors"},
    "p21":   {"name": "No Minibus"},
    "p22":   {"name": "No Human-Powered Vehicles"},
    "p23":   {"name": "No Bicycle Entry"},
    "p24":   {"name": "No Rickshaws"},
    "p25":   {"name": "No Vehicle Entry"},
    "p26":   {"name": "No Overtaking"},
    "p27":   {"name": "End of No Overtaking"},
    "pa14":  {"name": "No Parking (Area)"},
    "pb":    {"name": "No Honking"},
    "pc":    {"name": "No Certain Vehicles"},
    "pg":    {"name": "No Passing Zone"},

    # ---- Height / Weight / Width Limits ----
    "ph4":   {"name": "Height Limit 4m"},
    "ph4.5": {"name": "Height Limit 4.5m"},
    "ph5":   {"name": "Height Limit 5m"},
    "pm20":  {"name": "Weight Limit 20t"},
    "pm30":  {"name": "Weight Limit 30t"},
    "pm55":  {"name": "Weight Limit 55t"},
    "pr40":  {"name": "Width Limit 4m"},

    # ---- No U-Turn / No Entry ----
    "pn":    {"name": "No U-Turn"},
    "pne":   {"name": "No Entry"},
    "po":    {"name": "No Overtaking"},

    # ---- Warning Signs (w) ----
    "w13":   {"name": "Construction Zone"},
    "w32":   {"name": "Accident-Prone Area"},
    "w55":   {"name": "Slippery Road"},
    "w57":   {"name": "Falling Rocks"},
    "w59":   {"name": "Sharp Curve Ahead"},
}

# # 默认建议（未识别的类别）
# DEFAULT_ADVISORY = {
#     "name": "Traffic Sign",
#     "icon": "🚦",
#     "severity": "info",
#     "advice": "Traffic sign detected. Always observe and follow all traffic signs and signals for safe driving."
# }
