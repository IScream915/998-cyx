from __future__ import annotations

import torch
from torch import Tensor, nn


class ZoneSegHead(nn.Module):
    def __init__(self, in_channels: int = 128, out_h: int = 640, out_w: int = 640):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, 128, 3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 1, 1),
        )
        self.upsample = nn.Upsample(
            size=(out_h, out_w), mode="bilinear", align_corners=False
        )

    def forward(self, neck_feature: Tensor) -> Tensor:
        return torch.sigmoid(self.upsample(self.conv(neck_feature)))
